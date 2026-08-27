# Arquitetura do Painel Tuya

Documento técnico detalhando a arquitetura, fluxo de dados e decisões de design.

## 📐 Visão Geral

O sistema é composto por **três camadas independentes**:

```
┌─────────────────────────────────────────────────────────┐
│                    Painel Web (FastAPI)                 │
│  HTML Rendering + API JSON (routes/devices, energy)     │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│              Repository (CRUD)                          │
│     Abstração de acesso ao banco (repository.py)        │
└──────────────────┬──────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────┐
│              SQLite (data/app.db)                       │
│ devices | readings | monitor_configs | comparison_groups│
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│        Collector (Processo Independente)                │
│       collector.py + APScheduler (BlockingScheduler)    │
└──────────────────┬──────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
   ┌────▼────┐          ┌────▼────────┐
   │tinytuya  │          │  repository  │
   │OutletDev │          │   (insert)   │
   └──────────┘          └──────────────┘

┌─────────────────────────────────────────────────────────┐
│        Utilitários (One-time scripts)                    │
│  inventory.py (import devices.json)                      │
│  scanner.py (network scan via tinytuya.deviceScan)      │
└─────────────────────────────────────────────────────────┘
```

## 🗄️ Modelo de Dados

### `devices`
Inventário persistente de todos os dispositivos conhecidos.

```sql
CREATE TABLE devices (
    id TEXT PRIMARY KEY,               -- Tuya device ID (único identificador)
    name TEXT,                         -- Nome do device
    local_key TEXT,                    -- Chave local para criptografia (tinytuya)
    category TEXT,                     -- Categoria (sp=camera, cz=socket, etc)
    product_name TEXT,                 -- Nome comercial do produto
    model TEXT,                        -- Modelo específico
    mapping_json TEXT,                 -- JSON com DPs disponíveis (do devices.json)
    is_sub INTEGER,                    -- 1 se é sub-device (zigbee, IR remote)
    parent_id TEXT,                    -- ID do device pai (se sub-device)
    ip TEXT,                           -- IP descoberto (NULL até scan)
    protocol_version REAL,             -- Versão do protocolo Tuya (3.3, 3.4, 3.5)
    last_seen_at TIMESTAMP,            -- Última vez que respondeu
    source TEXT,                       -- 'cloud' ou 'broadcast' (de onde veio)
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Popul. inicial:** `inventory.py` lê `devices.json` e insere.  
**Atualiz. periód.:** `scanner.py` roda scan, detecta IPs e atualiza.

### `monitor_configs`
Controla qual device está sendo monitorado e com que frequência.

```sql
CREATE TABLE monitor_configs (
    device_id TEXT PRIMARY KEY,        -- FK → devices.id
    enabled INTEGER,                   -- 1 = monitorando, 0 = parado
    poll_interval_seconds INTEGER,     -- Intervalo entre coletas deste device
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

**Fluxo:**
1. Usuário marca checkbox "Monitorar" no painel → insere linha
2. Collector consulta a cada 30s e sincroniza jobs do APScheduler
3. Usuário muda intervalo no painel → atualiza este campo
4. Collector detecta mudança e reagenda job

### `readings`
Histórico de todas as leituras — O "coração" de dados históricos.

```sql
CREATE TABLE readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT,                    -- FK → devices.id
    collected_at TIMESTAMP,            -- Timestamp da coleta
    dps_json TEXT,                     -- JSON bruto retornado por device.status()
    online INTEGER                     -- 1 = sucesso, 0 = timeout/erro
);
```

**Exemplo de `dps_json` (para um smart socket com medição de energia):**
```json
{
  "1": true,
  "17": 1234,
  "18": 450,
  "19": 2200,
  "20": 2300
}
```

Onde:
- `1` = switch_1 (booleano — ligado/desligado)
- `17` = add_ele (integer — energia acumulada com scale 3)
- `18` = cur_current (integer — corrente em mA)
- `19` = cur_power (integer — potência em W com scale 1)
- `20` = cur_voltage (integer — voltagem em V com scale 1)

**Crítico:** Os DPs não são normalizados em colunas separadas — ficam como JSON. Isso permite:
- Suportar qualquer dispositivo sem redesenhar schema
- Armazenar DPs desconhecidos/novos automaticamente
- Parsing dinâmico no painel

### `discovery_log`
Auditoria de todos os scans realizados.

```sql
CREATE TABLE discovery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP,
    device_id TEXT,
    ip TEXT,
    raw_json TEXT                      -- Resposta completa do scan
);
```

Não é usado pelo painel, mas valioso para:
- Ver histórico de IPs por device (mudanças de rede)
- Auditar quando cada device foi visto por último
- Troubleshoot mudanças de configuração

### `comparison_groups`
Agrupa séries de comparação de energia.

```sql
CREATE TABLE comparison_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,                  -- "Freezer vs Geladeira"
    description TEXT,
    created_at TIMESTAMP
);
```

### `comparison_series`
Mapeia qual DP de qual device pertence a qual grupo.

```sql
CREATE TABLE comparison_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER,                  -- FK → comparison_groups.id
    device_id TEXT,                    -- FK → devices.id
    dps_code TEXT,                     -- Ex: "cur_power", "cur_power_1", "add_ele"
    label TEXT,                        -- Rótulo amigável ("Cozinha - Canal 1")
    sort_order INTEGER,                -- Ordem de exibição no gráfico
    created_at TIMESTAMP
);
```

**Exemplo de comparativo:**
```
Group: "Análise: Freezer vs Geladeira"
  Series 1: device=Freezer, dps="cur_power", label="Freezer - Potência (W)"
  Series 2: device=Geladeira, dps="cur_power", label="Geladeira - Potência (W)"
```

Ao buscar dados, faz:
```sql
SELECT readings.collected_at, readings.dps_json
FROM readings
  JOIN comparison_series ON comparison_series.device_id = readings.device_id
WHERE comparison_series.group_id = 1
ORDER BY readings.collected_at ASC
```

Depois parseia `dps_json` para extrair o valor do campo `cur_power`.

## 🔄 Fluxo de Coleta

### Inicialização

```
┌─────────────┐
│run_collector│
└──────┬──────┘
       │
       ▼
   init_db()    ← Cria tabelas (idempotent)
       │
       ▼
  Scheduler.add_job(sync_monitor_configs)    ← A cada 30s
       │
       ▼
  sync_monitor_configs()    ← Sincroniza jobs com DB
       │
       ▼
  Scheduler.start()         ← Bloqueia, roda jobs conforme agendado
```

### Sincronização (A cada 30s)

```
sync_monitor_configs()
    │
    ├─ Busca todos monitor_configs com enabled=1
    │
    ├─ Para cada device ativo:
    │   ├─ Se JÁ TEM JOB:
    │   │   └─ Se intervalo mudou: re-agenda job
    │   │
    │   └─ Se NÃO TEM JOB:
    │       └─ Cria novo job com esse intervalo
    │
    └─ Remove jobs para devices NÃO mais ativos
```

### Coleta de Device (Por job agendado)

```
collect_device_status(device_id)
    │
    ├─ Busca device do banco
    ├─ Valida: tem IP? tem local_key? protocolo conhecida?
    │
    ├─ Conecta: tinytuya.OutletDevice(id, ip, key)
    ├─ Set version, timeout, socketPersistent
    │
    ├─ Chama: device.status()
    │
    ├─ SE sucesso (dps retornou):
    │   └─ insert_reading(device_id, dps_json, online=True)
    │
    └─ SE falha (timeout/exceção):
        └─ insert_reading(device_id, {}, online=False)
```

**Reutiliza padrão de `coletar.py` original:**
- Mesmo `set_version()`, `set_socketTimeout(3)`, `set_socketPersistent(True)`
- Mesma estratégia de tratamento de exceção (log + insert com online=False)
- Mesmo timeout curto (3s) — não bloqueia otros jobs

## ☀️ Fontes de energia solar

A coleta solar é uma **segunda fonte de dados** ao lado do tinytuya, atrás de
uma abstração por fabricante:

```
app/solar/
  base.py       CANAIS_SOLAR (vocabulário canônico) + interface FonteSolar
  solplanet.py  driver AiSWEI: assinatura do gateway, escala, MAPA_CANAIS
  __init__.py   DRIVERS = {"solplanet": SolPlanetDriver}
```

Princípios:

- **O inversor é uma linha comum em `devices`** (`source='solar'`,
  `local_key` NULL): status, grupos de energia e gráficos funcionam sem
  bifurcação. A tabela `solar_integracoes` guarda a conta/planta (com os
  parâmetros de coleta: local e intervalo) e `solar_inversores` liga o device
  à integração.
- **Nomenclatura do fabricante não sai do driver.** Cada driver traduz seus
  campos para os códigos canônicos de `CANAIS_SOLAR` (ex.: AiSWEI `i1` ×0.01
  → `corrente_mppt_1` em ampères). Fabricante novo = driver novo +
  `MAPA_CANAIS`; grandeza inédita = uma linha nova em `CANAIS_SOLAR`.
- **O coletor despacha por `devices.source`**: `collect_device_status`
  (tinytuya) ou `collect_solar_status` (driver cloud). A leitura solar é
  gravada com o `collected_at` do EQUIPAMENTO (tmstp) e deduplicada — o
  inversor mede a cada ~5 min, independente do polling. O backfill de
  30 dias roda no coletor, nunca no request web.
- **Apresentação separada**: `source='solar'` fica fora das telas de
  dispositivos/locais (filtro `incluir_solar` no repository) e mora em
  `/solar`; só o seletor de série dos grupos de energia enxerga tudo.
- **Níveis de acesso com capacidades**: o driver declara, por nível
  (`niveis_acesso`), o que a conta consegue fornecer — `plantas`,
  `inversores`, `telemetria`, `canais`, `historico`, `resumo`. A flag
  `nivel_acesso` mora na integração; configurador, telas e coletor se
  limitam sozinhos ao que o nível tem (backfill de um nível sem histórico é
  marcado como feito, resumo some da tela, etc.).
- **Tempo é responsabilidade do driver**: `Telemetria.tmstp_ms` é epoch UTC
  REAL da medição (a AiSWEI, por exemplo, grava a hora local da planta como
  se fosse UTC+8 — o driver corrige). O coletor grava a leitura com esse
  instante em `collected_at` e deduplica pelo `tmstp`; os gráficos convertem
  UTC → hora local do navegador em um ponto único (`datasetDeSerie`).

O passo a passo para adicionar um fabricante está em
[docs/NOVO_FABRICANTE_SOLAR.md](docs/NOVO_FABRICANTE_SOLAR.md).

## 🌐 Fluxo Web

### GET /devices

```python
# routes/devices.py
@router.get("", response_class=HTMLResponse)
async def devices_list(request: Request):
    devices = get_all_devices()                    # SELECT * FROM devices
    device_statuses = [
        get_device_status(dev.id)                  # JOIN com readings + monitor_configs
        for dev in devices
    ]
    return template.render(device_statuses)        # Renderiza HTML
```

`get_device_status(device_id)` retorna:
```python
{
    'device': {...},                   # Row de devices
    'reading': {...},                  # Última linha de readings
    'config': {...},                   # Row de monitor_configs
    'is_online': bool                  # online=True se leitura < 5 min atrás
}
```

### POST /devices/{device_id}/toggle

```python
@router.post("/{device_id}/toggle")
async def toggle_monitor(device_id: str, request: Request):
    config = get_or_create_monitor_config(device_id)
    new_state = not config.enabled
    update_monitor_config(device_id, enabled=new_state)
    # Próximo sync_monitor_configs() (em ≤30s) vai:
    # - Se new_state=True: criar job para este device
    # - Se new_state=False: remover job deste device
    return {"enabled": new_state}
```

### GET /energy/api/groups/{group_id}/data

```python
# Rota que plota dados no gráfico Chart.js
@router.get("/api/groups/{group_id}/data")
async def get_group_data(group_id: int, start: str, end: str):
    group = get_comparison_group(group_id)
    for series in group.series:
        readings = get_readings_for_series(
            series.device_id,
            series.dps_code,
            start,
            end
        )
        # readings é lista de {timestamp, value}
        # value extraído do dps_json[dps_code]
    return {
        'series': [
            {
                'label': 'Freezer - Potência',
                'data': [{timestamp: '...', value: 2100}, ...]
            },
            ...
        ]
    }
```

Aí o JavaScript faz:

```javascript
new Chart(ctx, {
    type: 'line',
    data: {
        datasets: [
            {label: 'Freezer', data: [...], borderColor: '#FF6384'},
            {label: 'Geladeira', data: [...], borderColor: '#36A2EB'}
        ]
    }
});
```

## 🔌 Integração tinytuya

### OutletDevice

```python
import tinytuya
device = tinytuya.OutletDevice(
    dev_id="ebd65a07b2543ff36fhklt",
    address="192.168.3.72",
    local_key="<local_key do aparelho>"
)
device.set_version(3.4)
device.set_socketTimeout(3)
device.set_socketPersistent(True)

status = device.status()
# Retorna:
# {
#     'dps': {'1': True, '19': 2200, '20': 2300, ...},
#     'code': 0,
#     'cid': '...',
#     't': 1234567890
# }
```

**Importante:** Sempre reutilizar `OutletDevice` pois é agnóstico a tipo de device.

### deviceScan

```python
results = tinytuya.deviceScan(verbose=False, maxretry=1)
# Retorna:
# {
#     'ebd65a07b2543ff36fhklt': {
#         'name': 'Camera Garagem 1',
#         'ip': '192.168.3.72',
#         'ver': 3.4,
#         'key': '<local_key do aparelho>',
#         ...
#     },
#     ...
# }
```

Dispatcher `scanner.py` converte isso em formato que `repository.insert_or_update_device()` espera.

## 🎯 Decisões de Design

### Por que JSON para DPs em vez de tabela normalizada?

**Alternativa 1 (rejeitada): Tabela normalizada**
```sql
CREATE TABLE device_readings_numeric (
    id, device_id, collected_at,
    dp_1, dp_17, dp_18, dp_19, dp_20  -- Uma coluna por DP
);
```

**Problema:** Novo device novo com DPs diferentes = ALTER TABLE sempre. Suportar N mil tipos de device = impraticável.

**Alternativa 2 (eleita): JSON**
```sql
CREATE TABLE readings (
    id, device_id, collected_at,
    dps_json TEXT  -- {"1": true, "17": 1234, ...}
);
```

**Vantagem:** Schema é fixo. Qualquer DP novo é suportado automaticamente. Parsing no painel (JavaScript) extrai o valor que quer.

### Por que APScheduler em vez de cron/Task Scheduler?

**Alternativa 1 (rejeitada): Task Scheduler**
```
Task Scheduler → Python script (executa uma vez) → sai
```

**Problema:** Sem estado — a cada execução teria que re-buscar config do BD, criar conexões, etc. Lento.

**Alternativa 2 (eleita): APScheduler**
```
Python contínuo → APScheduler (memoria resident jobs) → coletas rápidas
```

**Vantagem:** Estado em memória, sincronização via BD a cada 30s, jobs reagendam sem reiniciar.

### Por que FastAPI em vez de Streamlit?

**Alternativa 1 (rejeitada): Streamlit**
```python
import streamlit as st
st.write("Dispositivos")
for dev in devices:
    st.write(f"{dev.name}: {dev.status}")
```

**Problema:** Streamlit é para apps simples — dificuldade em criar modal de "criar grupo" com validações, gráficos interativos sincronizados, suportar múltiplas abas sem reload.

**Alternativa 2 (eleita): FastAPI + Jinja2**
```python
# HTML renderizado server-side
# JavaScript para interatividade (modais, gráficos Chart.js)
# API JSON para dados dinâmicos
```

**Vantagem:** Controle total de UI, escalável, padrão web clássico.

## 🔒 Segurança

### Proteção de Dados

- **Local-only:** Sem API pública. Escuta apenas em `127.0.0.1:8000` (localhost)
- **Credenciais:** `tinytuya.json` + `devices.json` em `.gitignore` — nunca versionam
- **Read-only:** Nunca envia comandos aos devices — apenas `.status()`

### Se expor na rede (AVANÇADO)

Se quiser acessar de fora do localhost:

1. **Reverse proxy com autenticação:**
   ```nginx
   location / {
       auth_basic "Painel Tuya";
       auth_basic_user_file /etc/nginx/.htpasswd;
       proxy_pass http://localhost:8000;
   }
   ```

2. **HTTPS:** Use Let's Encrypt + nginx

3. **Firewall:** Restrinja porta 8000 ao máximo

## 📈 Escalabilidade

### Números testados

- ✅ 27 dispositivos no inventário (seu case)
- ✅ 5 devices monitorados continuamente
- ✅ Interval 60s → ~700 leituras/hora por device
- ✅ SQLite comporta-se bem com ~100k leituras

### Limites do SQLite

- Não é ideal para > 1M registros sem índices/vacuum
- Se escalar muito (anos de dados), considere migração para PostgreSQL

**Índices presentes:**
```sql
idx_readings_device_collected  -- Crítico para query de séries temporais
idx_discovery_log_device_timestamp
idx_comparison_series_group
```

## 🧪 Testabilidade

### Teste de Banco

```python
from app.db import init_db
from app.inventory import import_devices_from_json
from app.repository import get_all_devices

init_db()
import_devices_from_json()
devices = get_all_devices()
assert len(devices) == 27
```

### Teste de Coleta

```python
# Manualmente, marcar 1 device para monitorar
from app.repository import get_or_create_monitor_config
config = get_or_create_monitor_config("ebd65a07b2543ff36fhklt", 60)

# Rodar collector por 2 minutos
python run_collector.py  # Ctrl+C após 2 min

# Verificar dados
from app.repository import get_latest_reading
reading = get_latest_reading("ebd65a07b2543ff36fhklt")
assert reading is not None
assert reading['online'] == 1
```

### Teste de Painel

```bash
python run_web.py
# Visitar http://localhost:8000/devices
# Verificar lista carrega
# Clique em "Ver" de um device
# Deve mostrar details + últimos DPs
```

---

**Versão do documento:** 1.0  
**Última atualização:** 2026-08-27
