import json
import logging
from pathlib import Path

from app.config import DEVICES_JSON_PATH
from app.repository import insert_or_update_device

logger = logging.getLogger(__name__)


def import_devices_from_json(json_path: Path = None) -> int:
    """
    Importa dispositivos de devices.json para a tabela devices.
    Reusa o mesmo padrão de leitura de coletar.py:13-40.
    Retorna o número de dispositivos importados/atualizados.
    """
    if json_path is None:
        json_path = DEVICES_JSON_PATH

    if not json_path.exists():
        logger.error(f"Arquivo {json_path} não encontrado.")
        return 0

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            devices_list = json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Erro ao decodificar {json_path}: {e}")
        return 0

    # devices.json é um array direto (não um objeto com chave 'devices')
    if isinstance(devices_list, dict):
        devices_list = devices_list.get('devices', [])

    if not isinstance(devices_list, list):
        logger.error("Formato inesperado em devices.json")
        return 0

    count = 0
    skipped = 0
    for device_data in devices_list:
        try:
            # Validar campos obrigatórios
            device_id = device_data.get('id', '').strip()
            name = device_data.get('name', '').strip()
            key = device_data.get('key', '').strip()

            if not device_id:
                logger.warning(f"Device sem ID, ignorado: {name}")
                skipped += 1
                continue

            if not key:
                logger.warning(f"Device {device_id} sem local_key, ignorado")
                skipped += 1
                continue

            # Protocolo padrão
            protocol = device_data.get('version') or device_data.get('ver')
            if not protocol:
                device_data['version'] = 3.4
                protocol = 3.4

            insert_or_update_device(device_data)
            count += 1
            logger.info(f"Importado/atualizado: {name} ({device_id}) - Protocolo: {protocol}")
        except Exception as e:
            logger.error(f"Erro ao importar device {device_data.get('id')}: {e}")
            skipped += 1
            continue

    logger.info(f"Total importado: {count} dispositivos")
    if skipped > 0:
        logger.warning(f"Total pulado: {skipped} dispositivos (sem ID ou key)")
    return count
