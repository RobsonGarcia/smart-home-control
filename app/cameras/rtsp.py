"""
Driver RTSP: a URL na mão, para câmera que transmite mas não fala ONVIF.

Existe porque é o caso mais comum de câmera Tuya: o firmware tem servidor
RTSP (em `:6554` ou `:8554`), sem ONVIF nenhum para descobrir nada. Sem
descoberta e sem perfis — o usuário cola a URL que o fabricante documenta, do
tipo `rtsp://192.168.3.9:6554/stream_0`, e o painel cuida do resto.
"""

from typing import List

from app.errors import ValidationError
from .base import CameraDescoberta, FonteVideo, Perfil
from .midia import snapshot_de_rtsp
from .onvif import com_credenciais


class RtspDriver(FonteVideo):
    id = "rtsp"
    rotulo = "RTSP (URL manual)"
    # Nem descoberta nem perfis: a URL é a configuração inteira.
    capacidades = ("snapshot", "stream")
    campos_credenciais = [
        {"chave": "stream_uri", "rotulo": "URL RTSP", "secreto": False,
         "dica": "ex.: rtsp://192.168.3.9:6554/stream_0"},
        {"chave": "usuario", "rotulo": "Usuário", "secreto": False,
         "opcional": True, "dica": "só se a câmera exigir"},
        {"chave": "senha", "rotulo": "Senha", "secreto": True,
         "opcional": True},
    ]

    def testar(self) -> dict:
        # O teste é buscar um quadro de verdade: URL que abre é URL que serve.
        # Qualquer coisa menos que isso deixaria o erro para a tela de vídeo.
        snapshot_de_rtsp(self.url_stream())
        return {"modelo": "Fluxo RTSP", "fabricante": ""}

    def perfis(self) -> List[Perfil]:
        return [Perfil(token="manual", nome="Fluxo RTSP",
                       stream_uri=str(self.config.get("stream_uri") or ""))]

    def snapshot(self) -> bytes:
        return snapshot_de_rtsp(self.url_stream())

    def url_stream(self) -> str:
        uri = str(self.config.get("stream_uri") or "").strip()
        if not uri:
            raise ValidationError("Informe a URL RTSP da câmera.")
        if not uri.lower().startswith("rtsp://"):
            raise ValidationError("A URL precisa começar com rtsp://")
        return com_credenciais(uri, str(self.config.get("usuario") or ""),
                               str(self.config.get("senha") or ""))
