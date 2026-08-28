# Quick Start — 5 minutos para começar

## ⚡ Setup Rápido

### Passo 1: Instalar dependências (1 min)

```bash
cd d:\dev\tinyTuya
pip install -r requirements.txt
```

### Passo 2: Inicializar banco (30s)

```bash
python -c "from app.db import init_db; from app.inventory import import_devices_from_json; init_db(); import_devices_from_json(); print('Feito!')"
```

Deve imprimir algo como:
```
INFO:app.inventory:Total importado: 27 dispositivos
Feito!
```

### Passo 3: Rodar painel (em Terminal 1)

```bash
python run_web.py
```

Acesse: **http://localhost:8000**

### Passo 4: Rodar collector (em Terminal 2) — OPCIONAL MAS RECOMENDADO

```bash
python run_collector.py
```

**Pronto!** Painel e coleta rodando.

---

## 🎮 Seu Primeiro Comparativo em 2 Minutos

### 1. Listar dispositivos (painel já aberto)

- Vá para aba **Dispositivos**
- Veja a lista de todos seus aparelhos
- Se não aparecer nenhum, clique **"Importar devices.json"**

### 2. Ativar monitoramento

- Marque o checkbox **"Monitorar"** para 2 dispositivos
- Ex: Freezer + Geladeira (se tiver)
- Espere ~1 min (primeira coleta)

### 3. Criar grupo comparativo

- Vá para aba **Energia**
- Clique **"Novo grupo comparativo"**
- Nome: `Freezer vs Geladeira`
- Clique **"Criar"**

### 4. Adicionar séries

- Abra o grupo que criou
- Clique **"Adicionar série"**

**Série 1 — Freezer:**
- Dispositivo: Freezer
- DP/Código: `cur_power`
- Rótulo: `Freezer - Watts`
- Clique **"Adicionar"**

**Série 2 — Geladeira:**
- Dispositivo: Geladeira
- DP/Código: `cur_power`
- Rótulo: `Geladeira - Watts`
- Clique **"Adicionar"**

### 5. Ver gráfico

- Gráfico apareça automaticamente
- Se vazio: aguarde 2-3 minutos (collector precisa coletar dados)
- Mude período no seletor (Última hora / 24h / etc)

---

## 🔍 Descobrindo DPs do Seu Device

Se não sabe qual DP usar:

1. **Ir para Dispositivos → Detalhe**
2. **Clique em "Ver DPs brutos"**
3. **Procure números como:**

| DP | Significado |
|----|------------|
| `1` | Switch/Power (true/false) |
| `17` | Energia acumulada (kWh) |
| `18` | Corrente (mA) |
| `19` | Potência instantânea (W) ← Mais comum |
| `20` | Voltagem (V) |

**Para dispositivos multi-canal** (ex: PJ1103-C):
- `cur_power_1`, `cur_power_2`
- `add_ele_1`, `add_ele_2`
- Etc (suffixo `_1`, `_2`, etc)

---

## ❌ Problemas Comuns & Soluções

### "Painel abre mas não mostra nenhum dispositivo"

**Causa:** `devices.json` não foi importado ainda.

**Solução:**
1. Vá para **Dispositivos**
2. Clique **"Importar devices.json"**
3. Aguarde ~2s
4. Recarregue a página

---

### "Tenho 27 dispositivos, mas collector está offline"

**Causa:** Collector não está rodando.

**Solução:**
1. Abra **Terminal 2**
2. Digite: `python run_collector.py`
3. Você vai ver logs como:
   ```
   INFO:app.collector:Sincronizando configurações...
   INFO:app.collector:Job agendado para device XXX: 60s
   ```

---

### "Collector está rodando, mas nenhuma leitura"

**Causa:** Nenhum device está marcado como "Monitorar".

**Solução:**
1. Vá para **Dispositivos**
2. Marque checkbox "Monitorar" para pelo menos 1 device
3. Collector vai detectar em ≤30s e começar coleta
4. Veja logs no terminal do collector:
   ```
   INFO:app.collector:Job agendado para device eb0000000000000000xxxx: 60s
   ```

---

### "Gráfico não aparece"

**Causa:** Sem dados históricos ainda.

**Solução:**
1. Aguarde 3-5 minutos (collector precisa coletar)
2. Recarregue gráfico (F5)
3. Se ainda vazio, debugar:

```bash
# Terminal 3
python -c "
from app.repository import get_latest_reading
reading = get_latest_reading('eb0000000000000000xxxx')
print('Última leitura:', reading)
"
```

Se `None`, device NÃO foi coletado ainda.

---

### "Collector diz: Dispositivo não respondeu"

**Causa:** Device pode estar:
- Offline (desligado)
- IP mudou
- Protocolo errado

**Solução:**
1. Clique **"Escanear rede agora"** no painel
   - Isso atualiza IP e protocolo detectados
2. Aguarde ~20s
3. Collector vai tentar de novo em seu intervalo

---

### "Tenho um medidor de 2 canais, como monitorar ambos?"

**Exemplo: PJ1103-C com canais A e B**

Criar um comparativo:

1. **Grupo:** "Meu Medidor - Ambos os canais"
2. **Série 1:**
   - Device: PJ1103-C
   - DP: `cur_power_1`
   - Label: `Canal A - Watts`
3. **Série 2:**
   - Device: PJ1103-C
   - DP: `cur_power_2`
   - Label: `Canal B - Watts`

Agora o gráfico mostra ambos os canais lado a lado.

---

### "Quero exportar dados do gráfico"

**Atualmente:** Copiar valores manualmente (Click direito → Inspect → copiar dados JSON)

**Alternativa:** Consultar banco direto:

```bash
# Terminal 3
sqlite3 data/app.db "SELECT collected_at, dps_json FROM readings WHERE device_id='eb0000000000000000xxxx' ORDER BY collected_at DESC LIMIT 10;"
```

---

## 🎛️ Ligar uma luz pelo painel

Nada é acionável até você liberar — de propósito.

1. **Dispositivos** → abra o aparelho (ou filtre por *Tipo → Interruptor*).
2. No cartão **Controles**, ligue a chave *Permitir acionamento* e confirme.
3. Os botões acendem. O redondo com o símbolo de energia liga e desliga; o
   toggle azul ao lado continua sendo só a **coleta de dados** — são coisas
   diferentes, e por isso têm formatos diferentes.
4. Em bomba d'água e afins, deixe *Pedir confirmação* ligada.

Só funciona com o painel rodando na mesma rede do aparelho (ou com a nuvem
Tuya configurada em `tuya.local.json`).

## 📹 Ver uma câmera

1. Rode a sonda **na rede das câmeras** para saber o caminho:
   `python scripts/sonda_onvif.py --usuario admin --senha ****`
2. **Câmeras** → *Configurar vídeo* na câmera desejada.
3. Escolha **ONVIF** (se a sonda achou) ou **RTSP (URL manual)**, preencha o
   IP/URL e o usuário e senha **da câmera**, e clique em *Testar câmera*.
4. Escolha o perfil e salve. A miniatura aparece na grade; *Ao vivo* abre o
   vídeo, com PTZ e sirene ao lado.

## ☀️ Energia Solar em 1 Minuto

Tem inversor SolPlanet/AiSWEI? Menu **Solar → Nova integração**:

1. Escolha o nível (**Comum** para conta do cloud.solplanet.net; **Pro** se
   o fabricante te deu um token Business) e a região (**ap** para contas do
   Brasil).
2. Informe App Key + App Secret (perfil → *Account and security*) e, sem
   token, o **API Key da planta** (*Plant* → detalhes).
3. "Testar e listar plantas" → escolha a planta → **Criar**.

O coletor descobre os inversores, importa 30 dias de histórico e passa a
ler a cada 5 min — total e por canal MPPT. Os canais entram nos grupos de
energia como qualquer tomada Tuya. Detalhes no README; fabricante novo em
`docs/NOVO_FABRICANTE_SOLAR.md`.

## 📊 Próximos Passos

### Entender melhor a arquitetura

Leia **ARCHITECTURE.md** (documento técnico detalhado).

### Customizar intervalo de coleta

**Interface:** Vá para **Dispositivos** → altere campo "Intervalo (s)"

**Direto no BD:**
```bash
sqlite3 data/app.db "UPDATE monitor_configs SET poll_interval_seconds = 30 WHERE device_id = 'seu_device_id';"
```

Collector detecta em ≤30s e reagenda job.

### Rodar collector como serviço Windows (AVANÇADO)

Use `py2exe` ou `pyinstaller`:

```bash
pip install pyinstaller
pyinstaller --onefile run_collector.py
# Usar dist/run_collector.exe em Task Scheduler
```

### Expor painel na rede local (AVANÇADO)

Edit `run_web.py`:
```python
uvicorn.run("app.main:app", host="0.0.0.0", port=8000)  # Expõe para toda rede
```

**⚠️ Cuidado:** Sem autenticação. Use firewall do Windows para restringir.

### Adicionar novo tipo de gráfico

1. Edite `app/templates/energy/group.html`
2. Mude `Chart(..., {type: 'line', ...})` para `{type: 'bar'}` ou outro
3. Recarregue painel

---

## 📞 Suporte

Se encontrou um problema NÃO listado acima:

1. **Verifique os logs:**
   - Painel: Terminal 1
   - Collector: Terminal 2
   - Procure por `ERROR` ou `Exception`

2. **Teste conexão manual:**

```bash
python -c "
import tinytuya
dev = tinytuya.OutletDevice('seu_device_id', '192.168.x.x', 'sua_local_key')
dev.set_version(3.4)
status = dev.status()
print('Status:', status)
"
```

3. **Reimporte devices.json:**

```bash
python -c "from app.inventory import import_devices_from_json; import_devices_from_json()"
```

4. **Reset do banco (⚠️ deleta histórico):**

```bash
rm data/app.db
python -c "from app.db import init_db; from app.inventory import import_devices_from_json; init_db(); import_devices_from_json()"
```

---

## ✅ Checklist de Verificação

- [ ] `pip install -r requirements.txt` rodou sem erros
- [ ] `data/app.db` foi criado
- [ ] `python run_web.py` abre painel em localhost:8000
- [ ] Painel mostra ≥1 dispositivo (após import)
- [ ] `python run_collector.py` inicia sem erros
- [ ] Collector mostra "Job agendado" para ≥1 device
- [ ] Marcar checkbox "Monitorar" funciona
- [ ] Gráfico mostra dados após 5 minutos

Se todos os itens ✅, você está pronto!

---

**Tempo total esperado:** 5-10 minutos  
**Próximo passo:** Ler README.md para contexto completo
