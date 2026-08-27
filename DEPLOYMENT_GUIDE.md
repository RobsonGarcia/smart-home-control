# Guia de Implantação — Setup em Produção Local

Este guia cobre como colocar o painel Tuya rodando de forma robusta em sua máquina local (Windows 11).

---

## 📋 Pré-requisitos

- Python 3.8+ (testado com 3.13)
- `devices.json` (gerado via `tinytuya wizard`)
- ~500MB de espaço em disco (banco + app)
- Porta 8000 disponível (painel)

---

## 🚀 Instalação Passo a Passo

### 1. Preparar Ambiente Virtual (Recomendado)

```bash
cd d:\dev\tinyTuya

# Criar venv
python -m venv venv

# Ativar
.\venv\Scripts\activate

# Upgrade pip
python -m pip install --upgrade pip
```

### 2. Instalar Dependências

```bash
pip install -r requirements.txt
```

Verifica saída para warnings — OK se houver apenas "websockets.exe not in PATH" (ignore).

### 3. Verificar devices.json

```bash
# Confirme que o arquivo existe
ls -la devices.json

# Se não existir, gerar uma vez:
# python -m tinytuya wizard
# (Siga o wizard para gerar tinytuya.json e devices.json)
```

### 4. Inicializar Banco

```bash
python -c "from app.db import init_db; from app.inventory import import_devices_from_json; init_db(); n = import_devices_from_json(); print(f'Importados {n} dispositivos')"
```

Esperado: `Importados 27 dispositivos` (ou quantos você tiver)

---

## ▶️ Rodar em Produção Local

### Opção A: Dois Terminais (Recomendado)

**Terminal 1 — Painel Web:**
```bash
cd d:\dev\tinyTuya
.\venv\Scripts\activate
python run_web.py
```

Esperado:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**Terminal 2 — Collector:**
```bash
cd d:\dev\tinyTuya
.\venv\Scripts\activate
python run_collector.py
```

Esperado:
```
INFO:app.collector:Iniciando collector...
INFO:app.collector:Sincronização de configs agendada a cada 30s
```

### Opção B: Um Terminal com &

(Não recomendado no Windows, mas possível via bash):

```bash
python run_web.py &
python run_collector.py
```

---

## 🔧 Configuração Avançada

### Mudar Porta do Painel

Edit `run_web.py`:
```python
uvicorn.run("app.main:app", host="0.0.0.0", port=8001)  # Mude 8000 → 8001
```

### Expor para Toda Rede Local

Edit `run_web.py`:
```python
uvicorn.run("app.main:app", host="0.0.0.0", port=8000)  # Já é assim!
```

Agora acesse de qualquer máquina na rede:
```
http://192.168.x.x:8000
```

### Modificar Intervalo Padrão de Coleta

Edit `app/config.py`:
```python
DEFAULT_POLL_INTERVAL_SECONDS = 30  # 30s em vez de 60s
```

Coleta mais frequente = mais dados, mais I/O local.

### Logs Detalhados

Edit `run_collector.py`:
```python
logging.basicConfig(level=logging.DEBUG)  # Ver todos os logs
```

---

## 📊 Monitorando Saúde

### Verificar Banco de Dados

```bash
# Ver quantas leituras foram coletadas
python -c "
from app.db import get_db
with get_db() as conn:
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM readings')
    print('Total de leituras:', cursor.fetchone()[0])
"
```

### Verificar Jobs Ativos

No terminal do collector, você verá linhas como:
```
INFO:app.collector:Job agendado para device ebd65a07b2543ff36fhklt: 60s
```

Cada linha = um device sendo monitorado.

### Testar Coleta Manual

```bash
python -c "
from app.collector import collect_device_status
collect_device_status('ebd65a07b2543ff36fhklt')  # Seu device ID
print('Coleta manual OK')
"
```

---

## 🛡️ Segurança

### Painel Protegido com Senha (via nginx)

Se expor para rede, adicione autenticação:

1. **Instale nginx** (Windows)
2. **Configure reverse proxy** com basic auth:

```nginx
server {
    listen 80;
    server_name 192.168.x.x;

    location / {
        auth_basic "Painel Tuya";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://localhost:8000;
    }
}
```

3. **Crie arquivo .htpasswd:**
```bash
# Via htpasswd (apt install apache2-utils)
htpasswd -c /etc/nginx/.htpasswd seu_usuario
# Digite senha quando solicitado
```

### Firewall do Windows

Para bloquear acesso externo ao painel:

1. Vá para **Windows Defender Firewall** → **Regras de Entrada**
2. Crie regra: Bloquear qualquer conexão na porta 8000 de fora do localhost

---

## 🔄 Reinicialização

### Se Painel Travou

```bash
# Terminal 1: Pressione Ctrl+C
# Aguarde 2-3s
# Execute novamente:
python run_web.py
```

### Se Collector Perdeu Sincronização

```bash
# Terminal 2: Pressione Ctrl+C
# Aguarde 2-3s
# Execute novamente:
python run_collector.py
```

Collector vai recarregar config do banco automaticamente.

### Reinicialização Completa

```bash
# Parar ambos os terminais (Ctrl+C)
# Aguarde 5s
# Terminal 1:
python run_web.py
# Terminal 2:
python run_collector.py
```

---

## 🐛 Troubleshooting de Implantação

### "Port 8000 already in use"

**Causa:** Outro processo está na porta 8000.

**Solução 1 (rápida):** Mude porta em `run_web.py` (veja Configuração Acima)

**Solução 2 (limpar processo):**
```bash
# Encontre PID
netstat -ano | findstr :8000

# Mate processo
taskkill /PID <PID> /F
```

### "ModuleNotFoundError: No module named 'app'"

**Causa:** Rodando do diretório errado.

**Solução:**
```bash
cd d:\dev\tinyTuya
python run_web.py  # Não `cd app; python ...`
```

### "devices.json not found"

**Causa:** Arquivo não foi criado.

**Solução:**
```bash
python -m tinytuya wizard
# Siga o assistente
# Vai gerar tinytuya.json + devices.json
```

### "Collector roda mas DB não tem readings"

**Causa:** Nenhum device está marcado como "Monitorar".

**Solução:**
1. Acesse painel: http://localhost:8000/devices
2. Marque checkbox "Monitorar" para ≥1 device
3. Aguarde ≤30s (sincronização)
4. Collector vai começar coleta

Verifique logs do collector — deve haver linhas como:
```
INFO:app.collector:Job agendado para device ...
```

---

## 📈 Performance & Manutenção

### Limpeza de Dados Antigos

SQLite funciona bem até ~1M de registros. Se quiser limpar:

```bash
python -c "
from app.db import get_db

# Deletar leituras mais antigas que 30 dias
with get_db() as conn:
    cursor = conn.cursor()
    cursor.execute('''
        DELETE FROM readings 
        WHERE collected_at < datetime('now', '-30 days')
    ''')
    print(f'Deletados {cursor.rowcount} registros antigos')
"
```

### Vacuum para Recuperar Espaço

```bash
python -c "
from app.db import get_db
with get_db() as conn:
    conn.execute('VACUUM')
print('Banco compactado')
"
```

### Monitorar Tamanho do Banco

```bash
# Windows PowerShell
(Get-Item data/app.db).Length / 1MB  # Retorna tamanho em MB
```

---

## 🎯 Rotina de Manutenção Mensal

1. **Verificar logs** para erros recorrentes
2. **Listar jobs ativos** (contar quantos devices monitoram)
3. **Limpar dados** com >30 dias (opcional)
4. **Fazer backup** de data/app.db
5. **Atualizar dependências** (opcionalmente): `pip install -r requirements.txt --upgrade`

---

## 📞 Suporte em Produção

### Logs Importantes

**Painel web (Terminal 1):**
```
ERROR...  → Erro de rota / request
INFO...   → Request normal
```

**Collector (Terminal 2):**
```
ERROR...  → Device não respondeu / conexão falhou
INFO...   → Job agendado / leitura gravada
```

### Diagnóstico Rápido

Rode este script para verificar estado geral:

```bash
python -c "
from app.db import get_db
from app.repository import get_all_devices, get_all_monitor_configs, get_latest_reading

devices = get_all_devices()
configs = get_all_monitor_configs()
online_count = sum(1 for d in devices if get_latest_reading(d['id']) and get_latest_reading(d['id'])['online'] == 1)

print(f'Dispositivos: {len(devices)}')
print(f'Monitorados: {len(configs)}')
print(f'Online agora: {online_count}')

with get_db() as conn:
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM readings')
    readings = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM readings WHERE online = 0')
    offline = cursor.fetchone()[0]

print(f'Total leituras: {readings}')
print(f'Falhas (offline): {offline}')
"
```

---

## 🚀 Próximos Passos

- [ ] Ler QUICKSTART.md (uso básico)
- [ ] Ler README.md (referência completa)
- [ ] Criar primeiro comparativo de energia
- [ ] Configurar interval de coleta
- [ ] Monitorar por 24h e revisar dados

---

**Versão:** 1.0  
**Data:** 2026-08-27  
**Público:** DevOps / Administrador Local
