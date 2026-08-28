"""
Registro de drivers de vídeo e o serviço que as rotas usam.

Mesma forma de `app/solar/__init__.py`: uma tabela de drivers e funções que
resolvem qual usar. Câmera de marca nova = arquivo novo + uma linha aqui.
"""

import json
import logging
from typing import Dict, List, Optional, Type

from app.errors import NotFoundError, ValidationError
from .base import CAPACIDADES_VIDEO, CameraDescoberta, FonteVideo, Perfil
from .midia import ffmpeg_disponivel, snapshot_de_rtsp
from .onvif import OnvifDriver
from .onvif import descobrir as descobrir_onvif
from .rtsp import RtspDriver

logger = logging.getLogger(__name__)

DRIVERS_VIDEO: Dict[str, Type[FonteVideo]] = {
    OnvifDriver.id: OnvifDriver,
    RtspDriver.id: RtspDriver,
}


def get_driver_video(nome: str) -> Type[FonteVideo]:
    driver = DRIVERS_VIDEO.get((nome or "").strip().lower())
    if driver is None:
        raise ValidationError("Driver de vídeo desconhecido: %s. Disponíveis: "
                              "%s." % (nome, ", ".join(sorted(DRIVERS_VIDEO))))
    return driver


def lista_drivers() -> List[dict]:
    """O que o configurador precisa para montar o formulário sozinho."""
    return [{"id": d.id, "rotulo": d.rotulo,
             "campos": d.campos_credenciais,
             "capacidades": sorted(d.capacidades)}
            for d in DRIVERS_VIDEO.values()]


def _config_da_camera(camera: dict) -> dict:
    """A linha do banco + as credenciais, no formato que o driver espera."""
    config = {
        "host": camera.get("host"),
        "porta": camera.get("porta"),
        "perfil_token": camera.get("perfil_token"),
        "snapshot_uri": camera.get("snapshot_uri"),
        "stream_uri": camera.get("stream_uri"),
    }
    try:
        config.update(json.loads(camera.get("credenciais_json") or "{}"))
    except (ValueError, TypeError):
        logger.warning("credenciais ilegíveis da câmera %s",
                       camera.get("device_id"))
    return config


def driver_de(camera: dict) -> FonteVideo:
    if not camera:
        raise NotFoundError("Câmera não configurada.")
    return get_driver_video(camera.get("driver"))(_config_da_camera(camera))


def snapshot_de(camera: dict) -> bytes:
    """
    Uma foto agora, pelo caminho mais barato que existir.

    O snapshot ONVIF é HTTP puro e não custa processo nenhum; só quando a
    câmera não tem um é que o ffmpeg entra para tirar um quadro do vídeo.
    """
    driver = driver_de(camera)
    try:
        return driver.snapshot()
    except ValidationError:
        if not camera.get("stream_uri"):
            raise
        if not ffmpeg_disponivel():
            raise ValidationError(
                "Esta câmera não tem foto por ONVIF e o ffmpeg (que tiraria "
                "um quadro do vídeo) não está instalado.")
        return snapshot_de_rtsp(driver.url_stream())


__all__ = ["CAPACIDADES_VIDEO", "CameraDescoberta", "DRIVERS_VIDEO",
           "FonteVideo", "OnvifDriver", "Perfil", "RtspDriver",
           "descobrir_onvif", "driver_de", "ffmpeg_disponivel",
           "get_driver_video", "lista_drivers", "snapshot_de"]
