import logging

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse

from app.errors import NotFoundError
from app.repository import (
    # Sentinela do repository: deixa o PUT distinguir "campo ausente" de
    # "campo enviado como null" (null em descricao/rede_cidr significa limpar).
    _NAO_INFORMADO as _MANTEM,
    create_comodo,
    create_local,
    delete_comodo,
    delete_local,
    get_all_locais,
    get_comodos_by_local,
    get_devices_grouped_by_local,
    get_local,
    update_comodo,
    update_local,
)

logger = logging.getLogger(__name__)

# Prefixo proprio de proposito: em /devices a rota GET /devices/{device_id}
# esta declarada antes das literais e engoliria qualquer /devices/<algo> novo.
router = APIRouter(prefix="/locais", tags=["locais"])


# --- IMPORTANTE: /api vem ANTES de /{local_id}. A tipagem int ja faria "api"
# cair fora, mas a ordem e a garantia que nao depende do conversor. ---
@router.get("/api")
async def locais_api():
    """Locais com seus cômodos — alimenta os seletores das telas."""
    locais = get_all_locais()
    for local in locais:
        local['comodos'] = get_comodos_by_local(local['id'])
    return {"locais": locais}


@router.get("", response_class=HTMLResponse)
async def locais_list(request: Request):
    """Painel de locais e cômodos."""
    template = request.app.templates.get_template("locais/list.html")
    return template.render(
        request=request,
        grupos=get_devices_grouped_by_local(),
    )


@router.get("/{local_id}", response_class=HTMLResponse)
async def local_detail(local_id: int, request: Request):
    """Um local: seus cômodos e os dispositivos de cada um."""
    local = get_local(local_id)
    if not local:
        return HTMLResponse(
            "<h1>Local não encontrado</h1><p><a href=\"/locais\">Voltar</a></p>",
            status_code=404,
        )

    grupos = get_devices_grouped_by_local(local_id=local_id)
    template = request.app.templates.get_template("locais/detail.html")
    return template.render(
        request=request,
        local=local,
        grupo=grupos[0] if grupos else None,
    )


@router.get("/{local_id}/devices")
async def local_devices(local_id: int):
    """Dispositivos de um local. Usado pelo seletor de série com escopo."""
    if not get_local(local_id):
        raise NotFoundError("Local %s não encontrado" % local_id)
    grupos = get_devices_grouped_by_local(local_id=local_id)
    if not grupos:
        return {"local_id": local_id, "devices": []}
    devices = []
    for bloco in grupos[0]['comodos']:
        devices.extend(bloco['devices'])
    devices.extend(grupos[0]['sem_comodo'])
    return {
        "local_id": local_id,
        "devices": [
            {
                "id": st['device']['id'],
                "name": st['device']['name'],
                "ip": st['device']['ip'],
                "mapping_json": st['device'].get('mapping_json'),
                "comodo": st['comodo']['nome'] if st['comodo'] else None,
                "is_online": st['is_online'],
                "monitorado": bool(st['config']['enabled']),
            }
            for st in devices
        ],
    }


@router.post("", status_code=201)
async def criar_local(data: dict = Body(...)):
    local_id = create_local(
        nome=data.get('nome'),
        descricao=data.get('descricao'),
        rede_cidr=data.get('rede_cidr'),
    )
    return get_local(local_id)


@router.put("/{local_id}")
async def editar_local(local_id: int, data: dict = Body(...)):
    update_local(
        local_id,
        nome=data.get('nome'),
        descricao=data['descricao'] if 'descricao' in data else _MANTEM,
        rede_cidr=data['rede_cidr'] if 'rede_cidr' in data else _MANTEM,
        sort_order=data.get('sort_order'),
    )
    return get_local(local_id)


@router.delete("/{local_id}")
async def excluir_local(local_id: int):
    delete_local(local_id)
    return {"deleted": True}


@router.post("/{local_id}/comodos", status_code=201)
async def criar_comodo(local_id: int, data: dict = Body(...)):
    comodo_id = create_comodo(
        local_id,
        nome=data.get('nome'),
        sort_order=data.get('sort_order', 0),
    )
    return {"id": comodo_id, "local_id": local_id, "nome": data.get('nome')}


@router.put("/{local_id}/comodos/{comodo_id}")
async def editar_comodo(local_id: int, comodo_id: int, data: dict = Body(...)):
    update_comodo(
        comodo_id,
        nome=data.get('nome'),
        sort_order=data.get('sort_order'),
        local_id=data.get('local_id'),
    )
    return {"id": comodo_id, "updated": True}


@router.delete("/{local_id}/comodos/{comodo_id}")
async def excluir_comodo(local_id: int, comodo_id: int):
    """Não apaga dispositivo: os que estavam nele voltam para 'sem cômodo'."""
    delete_comodo(comodo_id)
    return {"deleted": True}
