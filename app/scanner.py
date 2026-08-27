import json
import logging

from app.config import SCAN_TIMEOUT_SECONDS
from app.errors import ValidationError
from app.repository import insert_or_update_device, insert_discovery_log

logger = logging.getLogger(__name__)


def scan_network() -> dict:
    """
    Executa um scan de rede com tinytuya.deviceScan()
    Retorna um dict com estatísticas: {'found': N, 'updated': N, 'errors': N}
    """
    logger.info("Iniciando varredura de rede...")

    # Import preguicoso de proposito: o tinytuya so e necessario para varrer a
    # LAN. Importando no topo, o painel inteiro deixava de subir sem ele — e o
    # painel precisa abrir fora da rede, para consultar o que ja foi coletado.
    try:
        import tinytuya
    except ImportError:
        raise ValidationError(
            "tinytuya não está instalado: o scan de rede precisa dele. "
            "Rode: pip install -r requirements.txt")

    try:
        # deviceScan é um wrapper do tinytuya que faz broadcast e coleta respostas
        scan_results = tinytuya.deviceScan(verbose=False, maxretry=1)
    except Exception as e:
        logger.error(f"Erro ao executar scan: {e}")
        return {'found': 0, 'updated': 0, 'errors': 1}

    stats = {'found': 0, 'updated': 0, 'errors': 0}

    # scan_results vem como {'<device_id>': {...device_info...}, ...}
    if not isinstance(scan_results, dict):
        logger.warning(f"Resultado inesperado do scan: {type(scan_results)}")
        return stats

    for device_id, device_info in scan_results.items():
        try:
            # Validar deviceId obrigatório
            if not device_id or device_id == '':
                logger.warning("Device sem ID, ignorado.")
                continue

            ip = device_info.get('ip')
            if not ip or ip == '0.0.0.0':
                logger.debug(f"Device {device_id} sem IP válido, ignorado.")
                continue

            # Validar local_key obrigatória
            local_key = device_info.get('key', '').strip()
            if not local_key:
                logger.warning(f"Device {device_id} sem local_key, ignorado.")
                continue

            # Detectar protocolo corretamente
            protocol = device_info.get('ver')
            if not protocol:
                # Default para 3.4 se não detectado
                protocol = '3.4'
            else:
                # Garantir que é string e tenta converter para float
                try:
                    float(str(protocol))
                except ValueError:
                    protocol = '3.4'

            # Formata como o esperado por insert_or_update_device
            device_data = {
                'id': device_id,
                'name': device_info.get('name', 'Desconhecido').strip(),
                'key': local_key,
                'category': device_info.get('category', ''),
                'product_name': device_info.get('product_name', ''),
                'model': device_info.get('model', ''),
                'mapping': device_info.get('mapping', {}),
                'sub': device_info.get('sub', False),
                'parent': device_info.get('parent'),
                'ip': ip.strip(),
                'version': protocol,
                'source': 'broadcast'
            }

            insert_or_update_device(device_data)
            insert_discovery_log(device_id, ip, json.dumps(device_info))

            stats['found'] += 1
            logger.debug(f"Encontrado/atualizado: {device_data['name']} ({ip})")

        except Exception as e:
            logger.error(f"Erro ao processar device {device_id}: {e}")
            stats['errors'] += 1
            continue

    logger.info(f"Scan concluído: {stats['found']} encontrados, "
               f"{stats['errors']} erros")
    return stats
