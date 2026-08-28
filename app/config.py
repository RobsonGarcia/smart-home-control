import os
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

DATA_DIR.mkdir(exist_ok=True)

DEFAULT_POLL_INTERVAL_SECONDS = 60
SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS = 30
SCAN_TIMEOUT_SECONDS = 15

# Um dispositivo conta como online se a ultima leitura bem-sucedida cabe nesta
# janela. Comparado em UTC, que e o fuso do CURRENT_TIMESTAMP do SQLite.
ONLINE_WINDOW_MINUTES = 5

# Local criado pela migracao inicial, para onde vao todos os dispositivos que
# ja existiam no inventario. Eles ficam SEM comodo, na caixa de entrada do
# local, ate serem atribuidos.
DEFAULT_LOCAL_NAME = "Local Principal"

# Energia solar. O equipamento gera telemetria a cada ~5 min; polling mais
# rapido so queima requisicao (o coletor deduplica pelo tmstp de qualquer
# forma). O backfill puxa o historico da cloud ao configurar um inversor.
SOLAR_POLL_INTERVAL_SECONDS = 300
SOLAR_BACKFILL_DIAS = 30

DEVICES_JSON_PATH = BASE_DIR / "devices.json"
SNAPSHOT_JSON_PATH = BASE_DIR / "snapshot.json"

# Nuvem Tuya (opcional): caminho de comando para quem nao tem IP na LAN e
# unico caminho possivel para os controles infravermelho. Gitignorado.
TUYA_LOCAL_JSON_PATH = BASE_DIR / "tuya.local.json"

# Cameras. O painel nao guarda video: o snapshot e servido sob demanda e o
# HLS vive num diretorio temporario enquanto alguem estiver assistindo.
HLS_DIR = DATA_DIR / "hls"
# Segundos sem ninguem pedir o m3u8 antes de encerrar o ffmpeg daquela camera.
HLS_INATIVIDADE_SEGUNDOS = 30
# Duracao de cada segmento e quantos ficam na lista (latencia ~ 3x isso).
HLS_SEGMENTO_SEGUNDOS = 1
HLS_SEGMENTOS_NA_LISTA = 4
# Cache do snapshot: a grade de cameras pede varias imagens de uma vez.
SNAPSHOT_TTL_SEGUNDOS = 2
# Portas que a sonda tenta ao procurar ONVIF/RTSP numa camera.
PORTAS_ONVIF = (80, 8000, 2020, 8080, 5000)
PORTAS_RTSP = (554, 6554, 8554)
