import logging
from fastapi import APIRouter, Request, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.concurrency import run_in_threadpool

from app.errors import ValidationError
from app.db import get_db
from app.repository import (
    assign_device_placement,
    get_all_device_statuses,
    get_all_locais,
    get_comodos_by_local,
    get_device_status,
    get_devices_grouped_by_local,
    update_monitor_config,
    get_or_create_monitor_config
)
from app.inventory import import_devices_from_json
from app.scanner import scan_network

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_class=HTMLResponse)
async def devices_list(request: Request):
    """Lista de todos os dispositivos com status."""
    template = request.app.templates.get_template("devices/list.html")

    # Uma query so, em vez de get_device_status() por dispositivo (que abria
    # 3-4 conexoes cada). Vem ordenado por local > comodo > nome.
    device_statuses = get_all_device_statuses()

    locais = get_all_locais()
    for local in locais:
        local['comodos'] = get_comodos_by_local(local['id'])

    return template.render(
        request=request,
        device_statuses=device_statuses,
        grupos=get_devices_grouped_by_local(),
        locais=locais
    )


@router.get("/{device_id}", response_class=HTMLResponse)
async def device_detail(device_id: str, request: Request):
    """Detalhe de um dispositivo específico."""
    from datetime import datetime, timedelta
    import json
    from app.dps_mapping import get_friendly_name

    template = request.app.templates.get_template("devices/detail.html")

    status = get_device_status(device_id)
    if not status:
        # Rota HTML: devolve pagina, nao JSON. O `return x, 404` que estava
        # aqui virava HTTP 200 com um array, porque FastAPI nao tem retorno
        # em tupla estilo Flask.
        return HTMLResponse(
            "<h1>Dispositivo não encontrado</h1>"
            "<p><a href=\"/devices\">Voltar</a></p>",
            status_code=404,
        )

    # mapping_json do dispositivo: fonte autoritativa dos nomes de DP.
    device_mapping = {}
    if status['device'].get('mapping_json'):
        try:
            device_mapping = json.loads(status['device']['mapping_json'])
        except (json.JSONDecodeError, TypeError):
            device_mapping = {}

    # Buscar últimas 100 leituras (últimas ~1.67 horas se coleta a cada minuto)
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT collected_at, dps_json FROM readings
            WHERE device_id = ? AND online = 1
            ORDER BY collected_at DESC
            LIMIT 100
        """, (device_id,))
        readings = cursor.fetchall()

    # Processar readings para extrair DPs quantitativos
    readings_list = [dict(row) for row in readings]
    readings_list.reverse()  # Ordem cronológica crescente

    # Extrair DPs únicos e seus valores
    dps_timeseries = {}
    for reading in readings_list:
        try:
            dps_dict = json.loads(reading['dps_json'])
            for dp_code, value in dps_dict.items():
                # Considerar quantitativo se é número
                if isinstance(value, (int, float)):
                    if dp_code not in dps_timeseries:
                        dps_timeseries[dp_code] = {
                            # Categoria e mapping do proprio aparelho: sem
                            # eles um DP numerico baixo e ambiguo (DP 1 e
                            # "Interruptor 1" na tomada e "Temperatura" no
                            # sensor) e o rotulo sairia errado.
                            'name': get_friendly_name(
                                dp_code,
                                status['device'].get('category'),
                                device_mapping),
                            'data': []
                        }
                    dps_timeseries[dp_code]['data'].append({
                        'timestamp': reading['collected_at'],
                        'value': value
                    })
        except (json.JSONDecodeError, TypeError):
            continue

    # Converter para formato Chart.js
    chart_data = []
    # Ordem numerica de verdade: sorted() de string poe "19" antes de "2".
    ordenados = sorted(dps_timeseries.items(),
                       key=lambda kv: (not kv[0].isdigit(), kv[0].zfill(4)))
    for idx, (dp_code, series) in enumerate(ordenados):
        chart_data.append({
            'code': dp_code,
            'label': series['name'],
            'data': series['data'],
        })

    return template.render(
        request=request,
        device_status=status,
        chart_data=json.dumps(chart_data)
    )


@router.post("/{device_id}/toggle")
async def toggle_monitor(device_id: str):
    """Ativa/desativa monitoramento de um device."""
    config = get_or_create_monitor_config(device_id)
    new_state = not (config['enabled'] == 1)
    update_monitor_config(device_id, enabled=new_state)

    return {"enabled": new_state}


@router.post("/{device_id}/poll_interval")
async def update_poll_interval(device_id: str, data: dict = Body(...)):
    """Atualiza intervalo de coleta para um device."""
    poll_interval = data.get('poll_interval_seconds', 60)

    if poll_interval < 10:
        poll_interval = 10

    update_monitor_config(device_id, poll_interval=poll_interval)
    return {"poll_interval_seconds": poll_interval}


@router.post("/scan/now")
async def scan_now():
    """Executa um scan de rede imediatamente (bloqueante)."""
    # Roda em threadpool para não bloquear o event loop
    stats = await run_in_threadpool(scan_network)
    return stats


@router.post("/import/devices-json")
async def import_from_devices_json():
    """Importa dispositivos de devices.json."""
    count = await run_in_threadpool(import_devices_from_json)
    return {"imported": count}


@router.post("/{device_id}/placement")
async def definir_placement(device_id: str, data: dict = Body(...)):
    """
    Atribui local e cômodo a um dispositivo.

    POST com 2 segmentos de propósito: `GET /devices/{device_id}` está
    declarada antes das rotas literais, então qualquer `GET /devices/<algo>`
    novo seria engolido por ela. Listagens por local ficam em /locais.
    """
    local_id = data.get('local_id')
    comodo_id = data.get('comodo_id')
    if local_id is None:
        raise ValidationError("local_id é obrigatório")

    assign_device_placement(device_id, int(local_id),
                            int(comodo_id) if comodo_id is not None else None)
    status = get_device_status(device_id)
    return {
        "device_id": device_id,
        "local": status['local'],
        "comodo": status['comodo'],
    }
