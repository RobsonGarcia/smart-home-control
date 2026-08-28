# Como adicionar uma fonte de vídeo (câmera)

O painel fala com cada câmera através de um **driver** (`app/cameras/`), e o
resto do sistema — telas, sessão HLS, snapshot — só conhece a abstração.
Adicionar um caminho de vídeo novo **não muda** rotas, templates nem schema: é
um arquivo novo e uma linha de registro.

A primeira coisa a entender é o que este driver **não** faz.

## O driver de vídeo cuida só da imagem

Numa câmera Tuya, quase tudo já funciona sem driver nenhum: mover (PTZ),
sirene, holofote, visão noturna, privacidade e gravação são **DPs comuns**,
derivados pelo `app/capacidades.py` e enviados pelo `app/control/servico.py`,
com as mesmas barreiras de qualquer interruptor. O que o Tuya não entrega é a
**imagem** — e é só disso que `app/cameras/` trata.

Se o seu driver novo está querendo mexer em comando, o desenho está errado.

## Passo a passo

### 0. Antes de codificar: a sonda

```
python scripts/sonda_onvif.py --usuario admin --senha ****
```

Rode **na rede das câmeras**. Ela faz WS-Discovery, varre as portas típicas de
ONVIF e RTSP em cada IP e, com credenciais, tenta o ONVIF de verdade. O
resultado decide o caminho:

| A sonda mostra | Caminho |
|---|---|
| respondeu ONVIF, com perfis | driver `onvif` — nada a escrever |
| só porta RTSP aberta | driver `rtsp`, com a URL do fabricante |
| nada aberto | nuvem do fabricante — driver novo |

Copie a sonda como modelo se o seu protocolo tiver descoberta própria.

### 1. O arquivo do driver

Crie `app/cameras/<fonte>.py` com uma classe que estende `FonteVideo`:

```python
from .base import FonteVideo, Perfil

class MinhaFonteDriver(FonteVideo):
    id = "minhafonte"            # estável; é o que fica na coluna cameras.driver
    rotulo = "Minha Fonte"       # o que o usuário vê no configurador
    capacidades = ("perfis", "snapshot", "stream")
    campos_credenciais = [
        {"chave": "host", "rotulo": "IP da câmera", "secreto": False},
        {"chave": "senha", "rotulo": "Senha", "secreto": True,
         "opcional": False, "dica": "a senha do stream, não a do app"},
    ]
```

`campos_credenciais` é o que faz o formulário se montar sozinho —
`cameras/list.html` não precisa saber que a sua fonte existe. O driver nasce
com a configuração de UMA câmera (`self.config`), como o driver solar nasce com
as credenciais de uma conta.

### 2. Os quatro métodos

| Método | Devolve | Cuidados |
|---|---|---|
| `testar()` | dict com fabricante/modelo/firmware | é o botão "Testar câmera"; `ValidationError` com motivo legível aparece na tela |
| `perfis()` | `[Perfil]` | um por fluxo (principal/secundário); traga `snapshot_uri` e `stream_uri` já resolvidos |
| `snapshot()` | bytes de um JPEG | sem foto própria, levante `ValidationError` — o painel tira um quadro do vídeo sozinho |
| `url_stream()` | URL RTSP **com credenciais** | é o que o ffmpeg consome; nunca vai para tela, log ou JSON |

### 3. Registro

Em `app/cameras/__init__.py`:

```python
from .minhafonte import MinhaFonteDriver
DRIVERS_VIDEO[MinhaFonteDriver.id] = MinhaFonteDriver
```

Pronto: a fonte aparece no configurador, o snapshot e o HLS passam a funcionar.

### 4. Teste offline

As suítes usam **drivers falsos** registrados em `DRIVERS_VIDEO` e um arquivo
de vídeo sintético (`ffmpeg -f lavfi -i testsrc`) no lugar do fluxo RTSP —
dá para exercitar configurador, snapshot, playlist, segmentos e encerramento
por inatividade sem nenhuma câmera. Siga esse padrão.

## Checklist de armadilhas (todas aconteceram)

- [ ] **Autenticação dupla**: o padrão manda WS-Security UsernameToken, e boa
  parte das câmeras baratas ignora isso e quer HTTP Digest. Mande os dois:
  cabeçalho WS-Security sempre, e repita com digest num 401.
- [ ] **A senha da câmera não é a do app.** O usuário ONVIF costuma ser
  `admin` com senha própria, definida numa tela escondida do aplicativo do
  fabricante. Diga isso na dica do campo — é o erro nº 1 de configuração.
- [ ] **XAddr do serviço**: perguntar em `GetCapabilities` é o certo; parte das
  câmeras responde tudo no mesmo endpoint. Tenha um plano B.
- [ ] **Descoberta multicast sai por interface.** Numa máquina com Wi-Fi, cabo
  e adaptador de VM, mandar o Probe só pela rota padrão não acha nada. Mande
  por todas (veja `_ips_locais`).
- [ ] **Não achou no discovery ≠ não fala ONVIF.** Há firmware com o anúncio
  desligado; aceite host digitado à mão.
- [ ] **`-c:v copy` antes de transcodificar.** Reembalar H.264 custa quase nada
  de CPU; transcodificar sete câmeras derruba a máquina. Só transcodifique
  quando o fluxo não puder ser copiado (H.265), e lembre disso para a próxima.
- [ ] **Processo que não morre.** Uma sessão de vídeo por câmera, encerrada por
  inatividade. Sem isso, sete câmeras abertas uma vez viram sete ffmpegs
  eternos.
- [ ] **stderr em PIPE que ninguém lê enche e trava o ffmpeg.** Mande para
  arquivo — que de quebra é o diagnóstico do "não abriu".
- [ ] **hls.js antes de `canPlayType`.** O Chrome no desktop responde "maybe"
  para `application/vnd.apple.mpegurl` e depois não toca. Prefira hls.js
  quando ele existir; o caminho nativo é para Safari/iOS.
- [ ] **Segredos**: usuário e senha ficam em `cameras.credenciais_json` e nunca
  saem em log, resposta de API ou mensagem de erro. As rotas mascaram via
  `_publica()` — mantenha assim. A URL RTSP carrega a senha embutida: ela
  também nunca sai.

## O que você NÃO precisa fazer

Sessão HLS, encerramento por inatividade, cache de snapshot, proxy autenticado,
PTZ, sirene, holofote, tela de configuração, grade de miniaturas: tudo isso é
do painel e funciona igual para qualquer driver. Se estiver mexendo fora de
`app/cameras/`, provavelmente o desenho está errado.
