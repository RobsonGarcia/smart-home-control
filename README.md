# Painel de Monitoramento de Dispositivos Tuya Locais

Um sistema completo para descobrir, inventariar e monitorar dispositivos Tuya conectados em sua rede local, com painel web interativo para visualizar status em tempo real e criar comparativos de consumo de energia.

## 🎯 Características

- **Descoberta automática de dispositivos** via scan de rede local (broadcast)
- **Inventário persistente** de todos os dispositivos descobertos
- **Painel web** com duas seções:
  - **Dispositivos**: Lista de todos os aparelhos, status online/offline, última leitura de dados, controle de monitoramento
  - **Energia**: Grupos comparativos para análise de consumo — combine qualquer canal de qualquer dispositivo e visualize tendências em gráfico
- **Coleta periódica em background** via APScheduler — sem precisar de Task Scheduler do Windows
- **Banco de dados local** (SQLite) — sem dependência de cloud
- **Somente leitura** — nunca executa comandos de atuação (ligar/desligar) nos dispositivos

## 📋 Pré-requisitos

- Python 3.8+
- `devices.json` obtido do seu painel Tuya Cloud (via `tinytuya wizard`)
- Dispositivos Tuya compatíveis com protocolo local LAN

## 🚀 Instalação e Setup

### 1. Preparar arquivo de credenciais (se ainda não tem)

Se ainda não tem um `devices.json`, gere-o uma única vez:

```bash
cd d:\dev\tinyTuya
python -m tinytuya wizard
```

Siga o assistente para criar `tinytuya.json` com suas credenciais Tuya (API key, secret, região). Ele gerará um `devices.json` com todos os seus dispositivos.

### 2. Instalar dependências

```bash
cd d:\dev\tinyTuya
pip install -r requirements.txt
```

### 3. Inicializar banco de dados

```bash
python -c "from app.db import init_db; from app.inventory import import_devices_from_json; init_db(); import_devices_from_json()"
```

Isso vai:
- Criar a estrutura SQLite em `data/app.db`
- Importar todos os dispositivos do `devices.json` para o banco

### 4. Rodar o painel web

Em uma aba de terminal:

```bash
python run_web.py
```

O painel estará disponível em: **http://localhost:8000**

### 5. (Opcional) Rodar coleta em background

Em outra aba de terminal:

```bash
python run_collector.py
```

Isso inicia um processo contínuo que:
- A cada 30 segundos, sincroniza quais dispositivos estão sendo monitorados
- Coleta status de cada device monitorado no intervalo configurado
- Grava todas as leituras no banco para consultas e gráficos

**Importante:** Se você NÃO rodar o collector, a seção de Energia não terá dados históricos.

## 📁 Estrutura de Arquivos

```
tinyTuya/
├── app/
│   ├── __init__.py
│   ├── config.py              # Caminhos e defaults
│   ├── db.py                  # Conexão e inicialização do SQLite
│   ├── schema.sql             # Definição de tabelas
│   ├── repository.py          # Funções CRUD de acesso ao banco
│   ├── inventory.py           # Importar devices.json
│   ├── scanner.py             # Scan de rede com tinytuya
│   ├── collector.py           # Coleta periódica com APScheduler
│   ├── main.py                # App FastAPI
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── devices.py         # Rotas /devices
│   │   └── energy.py          # Rotas /energy
│   ├── templates/
│   │   ├── base.html          # Layout base
│   │   ├── devices/
│   │   │   ├── list.html      # Lista de dispositivos
│   │   │   └── detail.html    # Detalhe de um device
│   │   └── energy/
│   │       ├── list.html      # Lista de grupos comparativos
│   │       └── group.html     # Visualização de um grupo + gráfico
│   └── static/
│       ├── app.css            # Estilos
│       └── app.js             # JavaScript global
├── data/
│   └── app.db                 # Banco SQLite (criado automaticamente)
├── devices.json               # Export dos seus dispositivos Tuya Cloud
├── tinytuya.json              # Credenciais Tuya (gerado pelo wizard)
├── requirements.txt           # Dependências Python
├── run_web.py                 # Entrypoint do painel web
├── run_collector.py           # Entrypoint do processo de coleta
└── README.md                  # Este arquivo
```

## 🏗️ Arquitetura

### Banco de Dados (SQLite)

| Tabela | Propósito |
|--------|-----------|
| `devices` | Inventário de todos os dispositivos (ID, nome, IP, protocolo, versão, etc.) |
| `monitor_configs` | Configuração de monitoramento por device (ativado/desativado, intervalo em segundos) |
| `readings` | Histórico de leituras — cada coleta gera uma linha com timestamp, DPs brutos e status online |
| `discovery_log` | Log de cada scan (auditoria de quando cada IP foi visto) |
| `comparison_groups` | Grupos comparativos de energia (nome, descrição) |
| `comparison_series` | Séries dentro de um grupo (qual device, qual DP, rótulo amigável) |

### Componentes

#### `inventory.py`
- Lê `devices.json` (export da conta Tuya Cloud)
- Popula a tabela `devices` com metadados (ID, nome, categoria, protocolo padrão, mapping de DPs)
- Pode ser reexecutado para reimportar

#### `scanner.py`
- Chama `tinytuya.deviceScan()` para fazer broadcast na rede local
- Encontra quais IPs estão ativos
- Atualiza a tabela `devices` com IP real e protocolo detectado
- Registra descoberta em `discovery_log`
- Disparado via botão "Escanear rede agora" no painel

#### `collector.py`
- Processo contínuo (BlockingScheduler do APScheduler)
- A cada 30 segundos sincroniza `monitor_configs` com os jobs agendados
- Cada device monitorado roda seu próprio job em intervalo próprio
- Conecta via `tinytuya.OutletDevice`, chama `.status()`, grava em `readings`
- Reutiliza exatamente o padrão de `coletar.py` original (versão, timeout, persistent)

#### `main.py` + Routers
- FastAPI com Jinja2Templates
- Rotas `/devices` (GET lista, POST toggle/intervalo, scan, import)
- Rotas `/energy` (GET grupos, POST criar, DELETE deletar, API dados históricos)
- Retorna HTML renderizado (painel web) e JSON (APIs)

### Fluxo de Dados

```
devices.json
    ↓
inventory.py → devices (tabela)
               ↓
            (painel lista todos)
               
scanner.py → deviceScan() → devices (atualiza IP/proto)
             ↓
          (disparado pelo botão "Escanear")

collector.py → OutletDevice.status() → readings (tabela)
    ↓                                      ↓
Roda contínuo      (disparado por monitor_configs)
(cada N segundos)                          ↓
                             (painel mostra status online/offline)
                             (energia plota gráficos)
```

## 🎮 Usando o Painel

### Seção Dispositivos

1. **Importar dispositivos**: Clique em "Importar devices.json" para carregar todos os aparelhos da sua conta
2. **Escanear rede**: Clique em "Escanear rede agora" para descobrir IPs ativos (leva ~15 segundos)
3. **Ativar monitoramento**: Marque o checkbox "Monitorar" para cada device que quer acompanhar
4. **Configurar intervalo**: Altere o campo "Intervalo (s)" para definir quantos segundos entre coletas
5. **Ver detalhe**: Clique em "Ver" para abrir a página do dispositivo com últimos DPs e status

### Seção Energia

1. **Criar grupo**: Clique em "Novo grupo comparativo"
   - Dê um nome (ex: "Comparativo: Freezer vs Geladeira")
   - Descrição opcional
2. **Adicionar séries**: Abra o grupo e clique em "Adicionar série"
   - Escolha device (ex: Freezer)
   - Digite o DP/código (ex: `cur_power` para potência, `add_ele` para energia acumulada)
   - Dê um rótulo amigável (ex: "Freezer - Potência")
3. **Ver gráfico**: O gráfico mostra todas as séries do grupo ao longo do tempo
   - Selecione período no seletor (1h, 6h, 24h, 7d, 30d)
   - Clique na legenda para mostrar/ocultar série
4. **Remover série ou grupo**: Use os botões "Remover" ou "Deletar"

## 🔌 DPs Comuns em Medidores de Energia

Se você tem um medidor multifase como o **PJ1103-C** (com 2 canais de energia), procure por:

- `cur_power` / `cur_power_1`, `cur_power_2` — Potência instantânea (Watts)
- `cur_current` / `cur_current_1`, `cur_current_2` — Corrente (mA)
- `cur_voltage` — Voltagem
- `add_ele` — Energia acumulada (kWh) — reseta quando você quiser

Para descobrir os DPs exatos do seu device:
1. Vá para a seção Dispositivos → detalhe do device
2. Abra "Ver DPs brutos" — mostra exatamente o que o device retorna
3. Use os nomes das chaves como `dps_code` ao criar uma série

## ⚙️ Configuração Avançada

### Variáveis em `app/config.py`

```python
DEFAULT_POLL_INTERVAL_SECONDS = 60          # Intervalo padrão entre coletas (segundos)
SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS = 30  # Frequência de sincronização do collector
SCAN_TIMEOUT_SECONDS = 15                   # Timeout máximo de um scan de rede
```

### Reiniciar o Collector

Se você parou o collector (`Ctrl+C`) e quer reiniciar:

```bash
python run_collector.py
```

Ele carrega automaticamente qual estava o estado anterior de monitoramentos e retoma.

## 🐛 Troubleshooting

### "Nenhum dispositivo encontrado" após escanear

- Verifique se seus devices Tuya estão na mesma rede WiFi
- Alguns devices podem estar no padrão de nuvem e não respondarem a broadcast local
- Tente reimportar `devices.json` para garantir que tem as credenciais corretas

### Collector roda mas nenhuma leitura é gravada

- Verifique se você tem pelo menos um device com checkbox de monitoramento marcado
- Confirme que a tabela `monitor_configs` tem alguma linha com `enabled = 1`
- Procure por erros no console do collector (pode ser problema de protocolo ou timeout de conexão)

### Painel de energia não mostra gráfico

- Aguarde alguns minutos com o collector rodando — precisa de histórico de leituras
- Verifique que pelo menos 2 leituras foram coletadas: `SELECT COUNT(*) FROM readings;`
- Confirme que o DP escolhido realmente existe no device — veja "Ver DPs brutos" na seção Dispositivos

### "Dispositivo não respondeu" no collector

- IP pode ter mudado — clique "Escanear rede agora" para atualizar
- Device pode estar offline — verá ✓ como "Offline" no painel
- Protocolo errado — tente 3.3, 3.4 ou 3.5 no scan, às vezes não detecta corretamente

## 📊 Exemplos de Comparativos de Energia

### Comparar dois medidores
- **Grupo:** "Freezer vs Geladeira"
- **Série 1:** Freezer, DP `cur_power`, Rótulo "Freezer - Potência (W)"
- **Série 2:** Geladeira, DP `cur_power`, Rótulo "Geladeira - Potência (W)"
- **Visualizar:** Vê lado a lado qual consome mais em cada momento

### Canais do mesmo medidor (PJ1103-C com 2 canais)
- **Grupo:** "Carga de casa: Lado A vs Lado B"
- **Série 1:** PJ1103-C, DP `cur_power_1`, Rótulo "Painel A"
- **Série 2:** PJ1103-C, DP `cur_power_2`, Rótulo "Painel B"
- **Visualizar:** Compara carga entre circuitos

### Energia acumulada ao longo do dia
- **Grupo:** "Consumo do dia - Freezer"
- **Série 1:** Freezer, DP `add_ele`, Rótulo "kWh acumulado"
- **Visualizar:** Vê curva crescente conforme o dia passa

## 🔐 Segurança

- **Local only:** Tudo roda em `localhost:8000` — não há servidor web público
- **No authentication:** Painel não tem login. Se quiser proteger, coloque atrás de reverse proxy (nginx + básico auth)
- **Credentials:** `tinytuya.json` e `devices.json` ficam em `.gitignore` — nunca comente no Git
- **Read-only:** Nunca envia comandos aos devices — apenas lê status

## 📝 Logs

- **Painel web:** Logs aparecem no console onde você rodou `python run_web.py`
- **Collector:** Logs aparecem no console onde você rodou `python run_collector.py` — mostra cada coleta realizada

## 🛠️ Desenvolvimento

Para adicionar novos recursos:

1. **Novo DP a monitorar** → Apenas use-o na série de comparação (não precisa código)
2. **Nova tabela** → Edite `app/schema.sql`, delete `data/app.db`, rode `init_db()` de novo
3. **Nova rota** → Crie em `app/routes/`, importe em `app/main.py`
4. **Novo template** → Crie em `app/templates/`, renderize com `template.render(request=request, ...)`

## 📞 Suporte

Se encontrar problemas:
1. Verifique os logs (console do painel e collector)
2. Confirme que os dispositivos estão online no app Tuya original
3. Tente reimportar `devices.json`
4. Reinicie ambos os processos (painel + collector)

---

**Versão:** 1.0  
**Última atualização:** 2026-08-27  
**Stack:** FastAPI + SQLite + APScheduler + tinytuya
