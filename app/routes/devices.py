import logging
from fastapi import APIRouter, Request, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.concurrency import run_in_threadpool

from app.errors import NotFoundError, ValidationError
from app.db import get_db
from app.repository import (
    assign_device_placement,
    get_all_device_statuses,
    get_all_locais,
    get_comodos_by_local,
    get_device_status,
    get_devices_grouped_by_local,
    update_monitor_config,
    get_or_create_monitor_config,
    ultimos_comandos,
)
from app.capacidades import (
    TIPOS,
    acoes_do_dispositivo,
    enriquecer_grupos,
    enriquecer_status,
    tipos_presentes,
)
from app.control import transportes_de
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
    # enriquecer_status acrescenta tipo e capacidades — derivadas na leitura,
    # do mapping que ja esta no banco (nao ha chamada de rede aqui).
    device_statuses = enriquecer_status(get_all_device_statuses())

    locais = get_all_locais()
    for local in locais:
        local['comodos'] = get_comodos_by_local(local['id'])

    return template.render(
        request=request,
        device_statuses=device_statuses,
        tipos_presentes=tipos_presentes(device_statuses),
        grupos=enriquecer_grupos(get_devices_grouped_by_local()),
        locais=locais
    )


def _dados_do_dispositivo(device_id: str):
    """
    Tudo que a tela de um dispositivo mostra, numa estrutura so.

    Existe porque a mesma coisa e servida de duas formas: HTML na primeira
    visita e JSON a cada atualizacao automatica. Duplicar o preparo seria
    duplicar a chance de as duas divergirem.

    Devolve None quando o dispositivo nao existe.
    """
    import json
    from app.dps_mapping import get_dp_info, unidade_exibivel

    status = get_device_status(device_id)
    if not status:
        return None

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
                        # Categoria e mapping do proprio aparelho: sem
                        # eles um DP numerico baixo e ambiguo (DP 1 e
                        # "Interruptor 1" na tomada e "Temperatura" no
                        # sensor) e o rotulo sairia errado.
                        info = get_dp_info(
                            dp_code,
                            status['device'].get('category'),
                            device_mapping)
                        dps_timeseries[dp_code] = {
                            'name': info['name'],
                            # A unidade viaja junto do nome porque a leitura
                            # ja esta escalada: sem ela o numero certo
                            # aparece sem dizer do que e.
                            'unit': unidade_exibivel(info),
                            'scale': info.get('scale') or 0,
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
    for dp_code, series in ordenados:
        chart_data.append({
            'code': dp_code,
            'label': series['name'],
            'unit': series['unit'],
            # Casas decimais = o proprio `scale` do Tuya, que na pratica e a
            # precisao do aparelho. Sem escala declarada nao ha precisao a
            # afirmar, e a tela decide pelo valor.
            'casas': series['scale'] or None,
            'data': series['data'],
        })

    # Capacidades: derivadas agora, do mapping deste aparelho. O valor atual
    # de cada uma vem da ultima leitura -- e o que faz o controle nascer na
    # posicao certa em vez de "desligado" por padrao.
    st = enriquecer_status([status])[0]
    ultimos_dps = {}
    if status['reading'] and status['reading'].get('dps_json'):
        try:
            ultimos_dps = json.loads(status['reading']['dps_json']) or {}
        except (json.JSONDecodeError, TypeError):
            ultimos_dps = {}

    acoes = []
    for acao in acoes_do_dispositivo(status['device']):
        dados = acao.to_dict()
        dados['valor'] = ultimos_dps.get(acao.dp)
        acoes.append(dados)

    return {'status': st, 'acoes': acoes, 'chart_data': chart_data}


@router.get("/{device_id}", response_class=HTMLResponse)
async def device_detail(device_id: str, request: Request):
    """Detalhe de um dispositivo específico."""
    import json

    dados = _dados_do_dispositivo(device_id)
    if dados is None:
        # Rota HTML: devolve pagina, nao JSON. O `return x, 404` que estava
        # aqui virava HTTP 200 com um array, porque FastAPI nao tem retorno
        # em tupla estilo Flask.
        return HTMLResponse(
            "<h1>Dispositivo não encontrado</h1>"
            "<p><a href=\"/devices\">Voltar</a></p>",
            status_code=404,
        )

    st = dados['status']
    template = request.app.templates.get_template("devices/detail.html")
    return template.render(
        request=request,
        device_status=st,
        acoes=dados['acoes'],
        tipos=TIPOS,
        transportes=[{'id': t.id, 'rotulo': t.rotulo, 'ok': v.ok,
                      'motivo': v.motivo}
                     for t, v in transportes_de(st['device'])],
        comandos=ultimos_comandos(device_id, 8),
        chart_data=json.dumps(dados['chart_data'])
    )


@router.get("/{device_id}/api/estado")
async def device_estado(device_id: str):
    """
    O mesmo conteudo da tela de detalhe, em JSON, para a atualizacao
    automatica repintar sem recarregar a pagina.

    O ritmo de quem chama sai daqui junto: um aparelho acionavel muda de
    estado por ACAO e precisa de resposta rapida; um que so mede nao tem nada
    de novo a dizer antes do proximo ciclo do coletor.
    """
    dados = _dados_do_dispositivo(device_id)
    if dados is None:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    st = dados['status']
    return {
        'device_id': device_id,
        'online': bool(st.get('is_online')),
        'reading': st.get('reading'),
        'acionavel': st.get('acionavel'),
        'intervalo_sugerido': (
            10 if st.get('acionavel')
            else (st['config']['poll_interval_seconds'] or 60)),
        'acoes': dados['acoes'],
        'chart_data': dados['chart_data'],
        'comandos': ultimos_comandos(device_id, 8),
    }


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


@router.post("/{device_id}/comando")
async def enviar_comando(device_id: str, data: dict = Body(...)):
    """
    Aciona um dispositivo: `{"dp": "1", "valor": true}`.

    Toda a política (opt-in, ação declarada, faixa de valores) está em
    app/control/servico.py — inclusive para quem chamar esta rota direto, sem
    passar pela tela. Roda em threadpool porque falar com o aparelho é I/O
    bloqueante de vários segundos no pior caso.
    """
    if 'dp' not in data:
        raise ValidationError("Informe o 'dp' do comando.")

    from app.control.servico import executar_comando
    return await run_in_threadpool(executar_comando, device_id,
                                   str(data['dp']), data.get('valor'))


@router.post("/{device_id}/acionavel")
async def alternar_acionavel(device_id: str, data: dict = Body(None)):
    """
    Liga/desliga o opt-in de acionamento. Sem corpo, alterna o valor atual.
    """
    from app.repository import get_device, set_acionavel
    atual = get_device(device_id)
    if not atual:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    novo = (not atual.get('acionavel')) if not data or 'acionavel' not in data \
        else bool(data['acionavel'])
    device = set_acionavel(device_id, novo)
    return {"device_id": device_id, "acionavel": bool(device['acionavel'])}


@router.post("/{device_id}/confirmar_acao")
async def alternar_confirmacao(device_id: str, data: dict = Body(None)):
    """Pedir ou não confirmação antes de cada comando deste dispositivo."""
    from app.repository import get_device, set_confirmar_acao
    atual = get_device(device_id)
    if not atual:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    novo = (not atual.get('confirmar_acao')) \
        if not data or 'confirmar_acao' not in data \
        else bool(data['confirmar_acao'])
    device = set_confirmar_acao(device_id, novo)
    return {"device_id": device_id,
            "confirmar_acao": bool(device['confirmar_acao'])}


@router.post("/{device_id}/tipo")
async def definir_tipo(device_id: str, data: dict = Body(...)):
    """Fixa o tipo à mão. `{"tipo": null}` volta para o derivado."""
    from app.capacidades import TIPOS, tipo_do_dispositivo
    from app.repository import set_tipo_manual

    tipo = data.get('tipo') or None
    if tipo is not None and tipo not in TIPOS:
        raise ValidationError("Tipo desconhecido: %s. Aceitos: %s."
                              % (tipo, ", ".join(sorted(TIPOS))))

    device = set_tipo_manual(device_id, tipo)
    return {"device_id": device_id, "tipo_manual": device['tipo_manual'],
            "tipo": tipo_do_dispositivo(device)}
