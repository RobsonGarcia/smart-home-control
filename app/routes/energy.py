import json
import logging
import sqlite3
from fastapi import APIRouter, Request, Query, Body, HTTPException
from fastapi.responses import HTMLResponse

from app.errors import NotFoundError, ValidationError
from app.repository import (
    # Sentinela: distingue "campo ausente" de "enviado como null" (null em
    # scope_local_id significa tornar o grupo geral).
    _NAO_INFORMADO as _MANTEM,
    get_all_comparison_groups,
    get_comparison_group,
    create_comparison_group,
    update_comparison_group,
    delete_comparison_group,
    add_series_to_group,
    remove_series_from_group,
    get_readings_for_series,
    get_all_devices,
    get_all_locais,
    get_devices_for_group_scope,
)
from app.dps_mapping import (
    get_common_dps_list,
    get_device_dps_list,
    get_friendly_name,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/energy", tags=["energy"])


@router.get("", response_class=HTMLResponse)
async def energy_groups(request: Request):
    """Lista de grupos comparativos de energia."""
    template = request.app.templates.get_template("energy/list.html")

    return template.render(
        request=request,
        groups=get_all_comparison_groups(),
        locais=get_all_locais()
    )


@router.get("/{group_id}", response_class=HTMLResponse)
async def energy_group_detail(group_id: int, request: Request):
    """Detalhe de um grupo comparativo."""
    template = request.app.templates.get_template("energy/group.html")

    group = get_comparison_group(group_id)
    if not group:
        # Rota HTML: pagina de erro, nao JSON.
        return HTMLResponse(
            "<h1>Grupo não encontrado</h1><p><a href=\"/energy\">Voltar</a></p>",
            status_code=404,
        )

    return template.render(
        request=request,
        group=group,
        locais=get_all_locais()
    )


@router.post("")
async def create_group(data: dict = Body(...)):
    """Cria um novo grupo comparativo."""
    name = data.get('name')
    description = data.get('description', '')
    # Ausente, null ou "" = grupo geral.
    scope_local_id = data.get('scope_local_id') or None

    if not name:
        raise ValidationError("Nome obrigatório")

    try:
        group_id = create_comparison_group(
            name, description,
            scope_local_id=int(scope_local_id) if scope_local_id else None)
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Já existe um grupo com esse nome neste escopo")
    return get_comparison_group(group_id)


@router.delete("/{group_id}")
async def delete_group(group_id: int):
    """Deleta um grupo comparativo e suas séries."""
    if not get_comparison_group(group_id):
        raise NotFoundError("Grupo %s não encontrado" % group_id)
    delete_comparison_group(group_id)
    return {"deleted": True}


@router.post("/{group_id}/series")
async def add_series(group_id: int, data: dict = Body(...)):
    """Adiciona uma série a um grupo."""
    device_id = data.get('device_id')
    dps_code = data.get('dps_code')
    label = data.get('label')

    if not all([device_id, dps_code, label]):
        raise ValidationError("device_id, dps_code e label obrigatórios")

    # Sem try/except de propósito: NotFoundError e ConflictError sobem para o
    # handler em main.py e viram 404/409 com a mensagem. Um `except Exception`
    # aqui engoliria justamente a recusa de série fora do local.
    series_id = add_series_to_group(group_id, device_id, dps_code, label)
    return {"id": series_id, "device_id": device_id, "dps_code": dps_code,
            "label": label}


@router.delete("/{group_id}/series/{series_id}")
async def remove_series(group_id: int, series_id: int):
    """Remove uma série de um grupo."""
    remove_series_from_group(series_id)
    return {"deleted": True}


@router.get("/api/device/{device_id}/dps")
async def get_device_dps(device_id: str):
    """Retorna os DPs disponíveis para um dispositivo específico."""
    device = get_all_devices()
    device_info = next((d for d in device if d['id'] == device_id), None)

    if not device_info:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    # Parsear mapping_json do device
    import json
    mapping = {}
    if device_info.get('mapping_json'):
        try:
            mapping = json.loads(device_info['mapping_json'])
        except (json.JSONDecodeError, TypeError):
            mapping = {}

    # Nome amigavel resolvido com o mapping E a categoria do aparelho. Antes
    # daqui saia o codigo cru do Tuya ("switch_1") como nome.
    available_dps = get_device_dps_list(mapping, device_info.get('category'))

    return {
        'device_id': device_id,
        'device_name': device_info.get('name'),
        'category': device_info.get('category'),
        'available_dps': available_dps,
        # Sugestoes para quando o aparelho nao tem mapping_json e o usuario
        # precisa digitar o codigo a mao.
        'sugestoes': get_common_dps_list() if not mapping else []
    }


@router.get("/api/groups/{group_id}/data")
async def get_group_data(group_id: int,
                        start: str = Query(None),
                        end: str = Query(None)):
    """Retorna dados de todas as séries de um grupo em um período."""
    group = get_comparison_group(group_id)
    if not group:
        raise NotFoundError("Grupo %s não encontrado" % group_id)

    data = {
        'group_id': group_id,
        'name': group['name'],
        'series': []
    }

    for series in group['series']:
        readings = get_readings_for_series(
            series['device_id'],
            series['dps_code'],
            start,
            end
        )

        data['series'].append({
            'id': series['id'],
            'label': series['label'],
            'device_id': series['device_id'],
            'dps_code': series['dps_code'],
            'data': readings
        })

    return data


@router.put("/{group_id}")
async def editar_grupo(group_id: int, data: dict = Body(...)):
    """
    Edita nome, descrição e escopo. Trocar o escopo não apaga série nenhuma —
    as que ficarem fora passam a vir marcadas, e a resposta diz quantas são.
    """
    scope = data['scope_local_id'] if 'scope_local_id' in data else _MANTEM
    if scope is not _MANTEM and scope is not None:
        scope = int(scope) if scope else None

    try:
        grupo = update_comparison_group(
            group_id,
            name=data.get('name'),
            description=data['description'] if 'description' in data else _MANTEM,
            scope_local_id=scope,
        )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="Já existe um grupo com esse nome neste escopo")

    return {
        "id": grupo['id'],
        "name": grupo['name'],
        "scope_local_id": grupo['scope_local_id'],
        "out_of_scope_count": grupo['fora_do_escopo'],
    }


@router.get("/api/groups/{group_id}/devices")
async def dispositivos_do_escopo(group_id: int):
    """
    Dispositivos que este grupo aceita — todos, se geral; só os do local, se
    tiver escopo. É o que alimenta o seletor de série já filtrado.
    """
    statuses = get_devices_for_group_scope(group_id)
    return {
        "group_id": group_id,
        "devices": [
            {
                "id": st['device']['id'],
                "name": st['device']['name'],
                "ip": st['device']['ip'],
                "local": st['local']['nome'] if st['local'] else None,
                "comodo": st['comodo']['nome'] if st['comodo'] else None,
                "is_online": st['is_online'],
                "monitorado": bool(st['config']['enabled']),
            }
            for st in statuses
        ],
    }
