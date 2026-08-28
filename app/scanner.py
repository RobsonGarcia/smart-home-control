"""
Varredura da LAN: onde cada aparelho Tuya está agora.

O que este módulo NÃO faz, apesar do nome: descobrir aparelhos novos. A
`local_key` não vem no broadcast — o tinytuya a resolve por `tuyaLookup()`,
casando o `gwId` recebido contra o `devices.json` do diretório. Sem chave não
há como falar com o aparelho, então a entrada é ignorada. Na prática o scan
serve para uma coisa só, e é uma coisa útil: **atualizar o IP de quem já está
no inventário**, que muda sozinho num DHCP.

A armadilha que custou 13 dispositivos duplicados: `tinytuya.deviceScan()`
devolve o dicionário chaveado por ENDEREÇO IP (o parâmetro `byID` é False por
padrão). O código antigo percorria `.items()` tratando a chave como se fosse o
id Tuya e gravava o IP como chave primária de `devices` — enquanto o id de
verdade vinha logo ali, no `gwId` do próprio payload. Identidade de aparelho
não pode depender do endereço que o roteador resolveu dar hoje.
"""

import json
import logging

from app.errors import ValidationError
from app.repository import insert_discovery_log, registrar_descoberta

logger = logging.getLogger(__name__)


def _id_forte(device_info: dict):
    """
    O id Tuya do aparelho, ou None.

    `gwId` é o identificador do gateway/dispositivo e é o que o Tuya usa como
    identidade em todo lugar; `id` é o mesmo valor, que o tinytuya copia
    quando falta. Não há terceiro palpite: sem um destes, a entrada é
    descartada. Cair no IP foi exatamente o bug.
    """
    for chave in ("gwId", "id"):
        valor = str(device_info.get(chave) or "").strip()
        if valor:
            return valor
    return None


def _protocolo(device_info: dict):
    """
    A versão do protocolo declarada no broadcast, ou None.

    O tinytuya normaliza para `version`; `ver` é o nome no formato serializado
    do snapshot.json. O código antigo lia só `ver`, nunca achava, e gravava
    '3.4' fixo — por isso todo aparelho escaneado virava 3.4, inclusive os
    3.3 e 3.5. None é melhor que um chute: o COALESCE do repositório preserva
    a versão que já estava lá, e um aparelho novo cai no DEFAULT da coluna.
    """
    bruto = device_info.get("version") or device_info.get("ver")
    if not bruto:
        return None
    try:
        return float(str(bruto))
    except (TypeError, ValueError):
        logger.debug("versão de protocolo ilegível no broadcast: %r", bruto)
        return None


def scan_network() -> dict:
    """
    Varre a LAN e atualiza o inventário.

    Devolve {'found', 'created', 'updated', 'ignored', 'errors'} — `ignored`
    são os aparelhos vistos na rede sem `local_key` conhecida, que é o
    resultado normal para quem ainda não está no devices.json.
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

    stats = {'found': 0, 'created': 0, 'updated': 0, 'ignored': 0, 'errors': 0}

    try:
        # deviceScan é um wrapper do tinytuya que faz broadcast e coleta respostas
        scan_results = tinytuya.deviceScan(verbose=False, maxretry=1)
    except Exception as e:
        logger.error(f"Erro ao executar scan: {e}")
        stats['errors'] = 1
        return stats

    # scan_results vem como {'<ip>': {...device_info...}, ...} — a chave é o
    # IP, NÃO o id do aparelho.
    if not isinstance(scan_results, dict):
        logger.warning(f"Resultado inesperado do scan: {type(scan_results)}")
        return stats

    for chave, device_info in scan_results.items():
        device_id = None
        try:
            if not isinstance(device_info, dict):
                logger.warning("Entrada de scan inesperada em %s, ignorada.", chave)
                stats['errors'] += 1
                continue

            device_id = _id_forte(device_info)
            if not device_id:
                logger.warning("Aparelho em %s não anunciou gwId, ignorado.", chave)
                stats['ignored'] += 1
                continue

            ip = (device_info.get('ip') or chave or '').strip()
            if not ip or ip == '0.0.0.0':
                logger.debug(f"Device {device_id} sem IP válido, ignorado.")
                stats['ignored'] += 1
                continue

            # A chave não vem do broadcast: o tinytuya a resolve contra o
            # devices.json. Sem ela não há conversa possível com o aparelho.
            local_key = (device_info.get('key') or '').strip() or None
            if not local_key:
                logger.info("Aparelho %s (%s) visto na rede, mas sem local_key "
                            "conhecida — importe o devices.json para monitorá-lo.",
                            device_id, ip)
                stats['ignored'] += 1
                continue

            nome = (device_info.get('name') or '').strip() or None
            criado = registrar_descoberta(
                device_id, ip,
                name=nome,
                local_key=local_key,
                protocol_version=_protocolo(device_info),
            )
            insert_discovery_log(device_id, ip, json.dumps(device_info))

            stats['found'] += 1
            stats['created' if criado else 'updated'] += 1
            logger.debug("%s: %s (%s)",
                         "Descoberto" if criado else "Atualizado",
                         nome or device_id, ip)

        except Exception as e:
            logger.error(f"Erro ao processar device {device_id or chave}: {e}")
            stats['errors'] += 1
            continue

    logger.info("Scan concluído: %d encontrado(s) — %d novo(s), %d atualizado(s), "
                "%d ignorado(s), %d erro(s)",
                stats['found'], stats['created'], stats['updated'],
                stats['ignored'], stats['errors'])
    return stats
