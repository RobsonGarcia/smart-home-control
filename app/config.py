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
