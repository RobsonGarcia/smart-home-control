"""
Seção Câmeras: foto, vídeo ao vivo e os controles que já são ações Tuya.

Divisão de responsabilidade que vale ter clara ao ler este arquivo: PTZ,
sirene, holofote e visão noturna NÃO passam por driver de vídeo — são DPs
Tuya como qualquer interruptor e vão pelo `app/control/servico.py`, com as
mesmas quatro barreiras. Aqui só mora o que o Tuya não entrega: a imagem.

Nada de credencial sai daqui: `_publica()` remove `credenciais_json` de toda
resposta, e a URL RTSP (que carrega usuário e senha embutidos) nunca vira
JSON — o navegador recebe HLS servido pelo próprio painel.
"""

import json
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, Body, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.cameras import (
    descobrir_onvif,
    driver_de,
    ffmpeg_disponivel,
    get_driver_video,
    lista_drivers,
    snapshot_de,
)
from app.cameras.midia import (
    arquivo_da_sessao,
    encerrar as encerrar_sessao,
    marcar_acesso,
    playlist_de,
    sessoes_ativas,
)
from app.capacidades import acoes_do_dispositivo, enriquecer_status
from app.config import SNAPSHOT_TTL_SEGUNDOS
from app.errors import NotFoundError, ValidationError
from app.repository import (
    delete_camera,
    get_all_device_statuses,
    get_camera,
    get_cameras,
    get_device,
    get_device_status,
    upsert_camera,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cameras", tags=["cameras"])

# Nome de arquivo que a sessão HLS pode servir. Fora disto, 404 — o caminho
# vem da URL e vira acesso a disco.
_ARQUIVO_HLS = re.compile(r"^(index\.m3u8|seg\d+\.ts)$")

# Cache curtíssimo da foto: a grade pede sete imagens de uma vez, e cada
# snapshot é uma ida à câmera.
_cache_foto = {}


def _publica(camera: Optional[dict]) -> Optional[dict]:
    """A câmera como ela pode aparecer numa resposta: sem credenciais."""
    if not camera:
        return None
    limpa = {k: v for k, v in camera.items() if k != "credenciais_json"}
    limpa["tem_credenciais"] = bool(camera.get("credenciais_json"))
    return limpa


def _exige_camera(device_id: str) -> dict:
    camera = get_camera(device_id)
    if not camera:
        raise NotFoundError("Câmera %s ainda não foi configurada." % device_id)
    return camera


def _cameras_da_tela():
    """
    Toda câmera do inventário, configurada ou não.

    A tela precisa das duas: as que já dão imagem e as que estão esperando
    configuração — senão o usuário não descobre que pode configurá-las.
    """
    configuradas = {c["device_id"]: c for c in get_cameras()}
    saida = []
    for st in enriquecer_status(get_all_device_statuses()):
        if st["tipo"] != "camera":
            continue
        st["camera"] = _publica(configuradas.get(st["device"]["id"]))
        st["acoes"] = [a.to_dict() for a in acoes_do_dispositivo(st["device"])]
        saida.append(st)
    return saida


# --------------------------------------------------------------- telas

@router.get("", response_class=HTMLResponse)
async def lista(request: Request):
    template = request.app.templates.get_template("cameras/list.html")
    cameras = _cameras_da_tela()
    return template.render(
        request=request,
        cameras=cameras,
        configuradas=[c for c in cameras if c["camera"]],
        drivers=lista_drivers(),
        tem_ffmpeg=ffmpeg_disponivel(),
    )


# Rotas literais ANTES de qualquer /{device_id}: a de detalhe engoliria
# "/api/descobrir" como se fosse um id.

@router.get("/api/drivers")
async def api_drivers():
    return {"drivers": lista_drivers(), "ffmpeg": ffmpeg_disponivel()}


@router.get("/api/sessoes")
async def api_sessoes():
    """O que está transmitindo agora — diagnóstico de processo pendurado."""
    return {"sessoes": sessoes_ativas()}


@router.post("/api/descobrir")
async def api_descobrir(data: dict = Body(None)):
    """
    WS-Discovery na LAN, com o resultado já casado com o inventário por IP.

    Roda em threadpool: são segundos de escuta em socket, e o event loop não
    pode ficar parado nisso.
    """
    timeout = float((data or {}).get("timeout") or 4.0)
    encontradas = await run_in_threadpool(descobrir_onvif, timeout)

    por_ip = {}
    for st in enriquecer_status(get_all_device_statuses()):
        if st["device"].get("ip"):
            por_ip[st["device"]["ip"]] = st["device"]

    saida = []
    for camera in encontradas:
        device = por_ip.get(camera.host)
        if device:
            camera.device_id = device["id"]
            camera.nome = camera.nome or device["name"]
        saida.append(camera.to_dict())
    return {"encontradas": saida, "total": len(saida)}


@router.post("/api/testar")
async def api_testar(data: dict = Body(...)):
    """
    Passo 1 do configurador: fala com a câmera e devolve o que ela oferece.

    As credenciais chegam aqui, são usadas e não voltam na resposta — só os
    perfis, que é o que a tela precisa para o usuário escolher.
    """
    driver_id = (data or {}).get("driver") or "onvif"
    config = dict((data or {}).get("config") or {})
    driver = get_driver_video(driver_id)(config)

    def _sondar():
        # perfis() e' obrigatorio no contrato: o driver RTSP devolve o unico
        # fluxo que ele conhece, o ONVIF devolve o que a camera declarar.
        info = driver.testar()
        return info, [p.to_dict() for p in driver.perfis()]

    info, perfis = await run_in_threadpool(_sondar)
    return {"driver": driver_id, "info": info, "perfis": perfis}


@router.get("/{device_id}/ao-vivo", response_class=HTMLResponse)
async def ao_vivo(device_id: str, request: Request):
    status = get_device_status(device_id)
    if not status:
        return HTMLResponse("<h1>Dispositivo não encontrado</h1>"
                            "<p><a href=\"/cameras\">Voltar</a></p>",
                            status_code=404)

    st = enriquecer_status([status])[0]
    template = request.app.templates.get_template("cameras/detail.html")
    return template.render(
        request=request,
        st=st,
        camera=_publica(get_camera(device_id)),
        acoes=[a.to_dict() for a in acoes_do_dispositivo(status["device"])],
        tem_ffmpeg=ffmpeg_disponivel(),
    )


# --------------------------------------------------------------- imagem

@router.get("/{device_id}/snapshot.jpg")
async def snapshot(device_id: str):
    camera = _exige_camera(device_id)

    agora = time.monotonic()
    guardado = _cache_foto.get(device_id)
    if guardado and agora - guardado[0] < SNAPSHOT_TTL_SEGUNDOS:
        imagem = guardado[1]
    else:
        imagem = await run_in_threadpool(snapshot_de, camera)
        _cache_foto[device_id] = (agora, imagem)

    return Response(content=imagem, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/{device_id}/hls/{arquivo}")
async def hls(device_id: str, arquivo: str):
    """
    Playlist e segmentos da sessão de vídeo.

    Pedir o index é o que MANTÉM a sessão viva: o player volta aqui a cada
    poucos segundos, e é isso que a faxina por inatividade observa. Fechou a
    aba, o ffmpeg morre sozinho em ~30 s.
    """
    if not _ARQUIVO_HLS.match(arquivo):
        raise NotFoundError("Arquivo de vídeo inválido.")

    camera = _exige_camera(device_id)

    if arquivo == "index.m3u8":
        driver = driver_de(camera)
        caminho = await run_in_threadpool(playlist_de, device_id,
                                          driver.url_stream())
        tipo = "application/vnd.apple.mpegurl"
    else:
        marcar_acesso(device_id)
        caminho = arquivo_da_sessao(device_id, arquivo)
        tipo = "video/mp2t"

    if not caminho:
        raise NotFoundError("Sessão de vídeo encerrada. Recarregue a página.")
    return FileResponse(str(caminho), media_type=tipo,
                        headers={"Cache-Control": "no-store"})


# --------------------------------------------------------- configuração

@router.post("/{device_id}")
async def configurar(device_id: str, data: dict = Body(...)):
    """
    Passo 2: guarda o vínculo de vídeo do dispositivo.

    O que vem da tela é driver + config + perfil escolhido; o que fica no
    banco separa endereço (aberto) de credencial (nunca devolvida).
    """
    if not get_device(device_id):
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    driver_id = data.get("driver") or "onvif"
    get_driver_video(driver_id)          # valida o nome antes de gravar
    config = dict(data.get("config") or {})

    credenciais = {k: config.get(k, "") for k in ("usuario", "senha")}
    tem_credencial = any(credenciais.values())

    camera = upsert_camera(
        device_id,
        driver=driver_id,
        host=config.get("host") or None,
        porta=int(config["porta"]) if config.get("porta") else None,
        credenciais_json=json.dumps(credenciais) if tem_credencial else None,
        perfil_token=data.get("perfil_token") or None,
        perfil_nome=data.get("perfil_nome") or None,
        snapshot_uri=data.get("snapshot_uri") or config.get("snapshot_uri"),
        stream_uri=data.get("stream_uri") or config.get("stream_uri"),
        enabled=bool(data.get("enabled", True)),
    )
    # Configuração nova invalida o que estava tocando com a antiga.
    encerrar_sessao(device_id)
    _cache_foto.pop(device_id, None)
    return _publica(camera)


@router.delete("/{device_id}")
async def remover(device_id: str):
    """Tira só o vídeo. O dispositivo e a coleta de DPs continuam de pé."""
    _exige_camera(device_id)
    encerrar_sessao(device_id)
    _cache_foto.pop(device_id, None)
    delete_camera(device_id)
    return {"device_id": device_id, "removida": True}


@router.post("/{device_id}/ptz")
async def ptz(device_id: str, data: dict = Body(...)):
    """
    Mover a câmera. Não é vídeo: é o DP `ptz_control` indo pelo transporte de
    comando, com opt-in e validação de faixa como qualquer outro acionamento.
    """
    direcao = str((data or {}).get("direcao", "")).strip()
    if not direcao:
        raise ValidationError("Informe a direção.")

    device = get_device(device_id)
    if not device:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    dp = next((a.dp for a in acoes_do_dispositivo(device)
               if a.code == "ptz_control"), None)
    if dp is None:
        raise ValidationError("Esta câmera não tem controle de movimento.")

    from app.control.servico import executar_comando
    return await run_in_threadpool(executar_comando, device_id, dp, direcao)
