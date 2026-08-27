# Como adicionar um fabricante de inversor solar

O painel fala com cada fabricante através de um **driver** (`app/solar/`), e
o resto do sistema — coletor, telas, grupos de energia — só conhece a
abstração. Adicionar um fabricante **não muda** coletor, repository, schema,
rotas nem templates: é um arquivo novo e uma linha de registro.

A regra de ouro: **nenhuma nomenclatura do fabricante sai do driver**. O que
sai são os códigos canônicos de `CANAIS_SOLAR` (`app/solar/base.py`), com
valores já convertidos para a unidade real e timestamps em UTC de verdade.

## Passo a passo

### 0. Antes de codificar

- Consiga a **documentação da API** e **credenciais reais de teste**.
  Guarde os PDFs/manuais em `docs/` (são versionados; credenciais nunca).
- Crie um arquivo local gitignorado para as credenciais de teste (padrão:
  `solar.local.json` na raiz). Nunca as coloque em código, commit ou log.

### 1. O arquivo do driver

Crie `app/solar/<fabricante>.py` com uma classe que estende `FonteSolar`:

```python
from .base import CANAIS_SOLAR, CAPACIDADES, FonteSolar, Inversor, Planta, \
                  ResumoPlanta, Telemetria

class MeuFabricanteDriver(FonteSolar):
    id = "meufabricante"          # estável; vira prefixo do device_id
    rotulo = "Meu Fabricante"     # o que o usuário vê no formulário
```

Metadados que o configurador usa para montar o formulário sozinho:

- `campos_credenciais`: lista de `{"chave", "rotulo", "secreto",
  "opcional"?, "dica"?}` — um fabricante com credenciais diferentes não
  exige mudança de template.
- `niveis_acesso`: se o fabricante vende a API em camadas (como a AiSWEI:
  Pro × comum), declare cada nível com `{"valor", "rotulo", "descricao",
  "disponivel", "capacidades"}`. As capacidades (`plantas`, `inversores`,
  `telemetria`, `canais`, `historico`, `resumo`) limitam automaticamente o
  configurador, as telas e o coletor. Um nível documentado mas ainda não
  implementado entra com `"disponivel": False` — aparece no formulário,
  desabilitado, com o motivo na descrição.

### 2. A tabela de tradução (MAPA_CANAIS)

Declare a tradução campo-do-fabricante → código canônico + fator de escala,
como dado, não como código:

```python
MAPA_CANAIS = {
    "outPower": ("potencia_ca", 1.0),     # já vem em W
    "eToday":   ("geracao_hoje", 0.1),    # vem em décimos de kWh
    ...
}
```

- Só use códigos que existem em `CANAIS_SOLAR`. Se o fabricante medir algo
  inédito, adicione o código **uma vez** em `base.py` (nome amigável em
  português + unidade) — ele passa a existir para todos os fabricantes.
- Canal ausente/`null` na resposta **não entra** no dict de valores — é
  assim que um inversor de 2 canais deixa de anunciar `corrente_mppt_3`.

### 3. Os sete métodos da interface

| Método | Devolve | Cuidados |
|---|---|---|
| `testar()` | nada; `ValidationError` com motivo legível se falhar | é o botão "Testar" do configurador — a mensagem aparece na tela |
| `descobrir_plantas()` | `[Planta]` | se a conta não puder listar, aceite o identificador da planta como credencial manual |
| `descobrir_inversores()` | `[Inversor]` (sn é a identidade) | usa `planta_apikey` das credenciais |
| `descobrir_canais(sn)` | `[{code, name, unit}]` só dos canais reais | normalmente deriva de `ultima_telemetria` |
| `ultima_telemetria(sn)` | `Telemetria(tmstp_ms, valores, online)` | `tmstp_ms` é o instante da **medição**, epoch UTC REAL |
| `historico(sn, inicio_utc, fim_utc)` | iterador de `Telemetria` | pagina/fatia internamente; alimenta o backfill |
| `resumo_planta()` | `ResumoPlanta` | cartão da tela Solar; converta unidades dinâmicas (kW/MWh) |

### 4. Registro

Em `app/solar/__init__.py`:

```python
from .meufabricante import MeuFabricanteDriver
DRIVERS[MeuFabricanteDriver.id] = MeuFabricanteDriver
```

Pronto: o fabricante aparece no configurador, o coletor sabe lê-lo e os
canais entram nos grupos de energia.

### 5. Valide com a API real ANTES da interface

Copie `scripts/sonda_solplanet.py` como modelo e faça uma sonda CLI que
roda `testar → plantas → inversores → telemetria` com as credenciais do
arquivo local, imprimindo os valores convertidos (nunca as credenciais).
É aqui que assinatura, escala e fuso quebram — não na UI.

### 6. Teste offline e configure

- As suítes de teste usam **drivers falsos** registrados em `DRIVERS` para
  exercitar configurador, coletor e grupos sem rede — siga o padrão.
- Depois: *Solar → Nova integração* na interface, e confira a checklist
  abaixo com dados reais.

## Checklist de armadilhas (todas aconteceram com a SolPlanet)

- [ ] **Fuso do timestamp**: confira o epoch contra o relógio de parede da
  medição. A AiSWEI grava a hora local da planta como se fosse UTC+8 — sem
  correção, as curvas deslocam horas. `Telemetria.tmstp_ms` DEVE ser epoch
  UTC verdadeiro; corrija dentro do driver.
- [ ] **Fuso das janelas de consulta**: `starttime/endtime` podem ser em
  hora local da planta, não UTC. Teste as duas hipóteses.
- [ ] **Limites não documentados**: janela máxima por consulta (a AiSWEI
  recusa ≥ 24 h — fatie), e **rajadas** (o gateway corta chamadas
  simultâneas bem abaixo do rate limit oficial — serialize com espaçamento
  e faça retry com backoff; veja `_TRAVA` no driver SolPlanet).
- [ ] **Região/host**: o mesmo fabricante pode ter gateways por região, e a
  chave só existe em um deles ("Invalid AppKey" = chave desconhecida
  NAQUELE host, não credencial errada). Deixe a região configurável.
- [ ] **Escalas e unidades**: valores costumam vir como string escalada
  ("4203" = 420,3 V) e resumos com unidade dinâmica (kW/MWh) — converta
  na borda, nunca depois.
- [ ] **Envelopes de resposta**: podem variar por endpoint até dentro da
  mesma API ({"status"}, {"code"}, objeto direto). Trate um a um e
  transforme erro em `ValidationError` com mensagem em português.
- [ ] **Assinatura**: a documentação oficial pode divergir do que o gateway
  aceita — quando houver implementação de referência que funciona,
  espelhe-a; guarde no driver como depurar (a AiSWEI devolve o
  string-to-sign do servidor num header de erro).
- [ ] **Segredos**: credenciais nunca em log, resposta de API ou mensagem de
  erro; as rotas já mascaram via `_publica()` — mantenha assim.

## O que você NÃO precisa fazer

Dedupe por `tmstp`, gravação com `collected_at` da medição, janela de
online por intervalo, backfill em job, filtro das telas, seletor de série
dos grupos: tudo isso é do coletor/repository e funciona igual para
qualquer driver. Se estiver mexendo fora de `app/solar/`, provavelmente o
desenho está errado.
