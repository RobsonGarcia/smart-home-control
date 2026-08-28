"""
Fonte de vídeo: como o painel chega na imagem de uma câmera.

Mesmo desenho de `app/solar/base.py`, pelo mesmo motivo: câmera de marca
diferente fala protocolo diferente, e nada disso pode vazar para a tela. O que
sai daqui é sempre `Perfil` — um nome, uma resolução, uma URL de foto e uma de
vídeo — não importa se por baixo era ONVIF, RTSP puro ou nuvem do fabricante.

Por que isto não é só "mais um driver solar": o Tuya entrega TUDO de uma câmera
menos a imagem. PTZ, sirene, holofote e visão noturna já são ações comuns
(`app/capacidades.py`) e vão pelo transporte de comando. Esta abstração cobre
exclusivamente o vídeo.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# O que um driver pode saber fazer. A tela se limita a isto, como no solar.
CAPACIDADES_VIDEO = ("descoberta", "perfis", "snapshot", "stream")


@dataclass
class CameraDescoberta:
    """Uma câmera que respondeu na rede — antes de qualquer credencial."""
    host: str
    porta: int = 80
    xaddr: str = ""
    fabricante: str = ""
    modelo: str = ""
    nome: str = ""
    device_id: Optional[str] = None   # preenchido se casar com o inventário

    def to_dict(self) -> dict:
        return {"host": self.host, "porta": self.porta, "xaddr": self.xaddr,
                "fabricante": self.fabricante, "modelo": self.modelo,
                "nome": self.nome, "device_id": self.device_id}


@dataclass
class Perfil:
    """Um fluxo da câmera: a principal e a secundária são perfis diferentes."""
    token: str
    nome: str = ""
    largura: Optional[int] = None
    altura: Optional[int] = None
    snapshot_uri: str = ""
    stream_uri: str = ""

    @property
    def resolucao(self) -> str:
        if self.largura and self.altura:
            return "%dx%d" % (self.largura, self.altura)
        return ""

    def to_dict(self) -> dict:
        return {"token": self.token, "nome": self.nome,
                "resolucao": self.resolucao, "snapshot_uri": self.snapshot_uri,
                "stream_uri": self.stream_uri}


class FonteVideo(ABC):
    """
    Contrato de um driver de vídeo.

    Instanciado com a configuração de UMA câmera (host, porta, usuário, senha
    e o que a descoberta já tiver resolvido) — igual ao driver solar, que
    nasce com as credenciais de uma conta.
    """

    id: str = ""
    rotulo: str = ""
    # Campos do formulário, para o configurador se montar sozinho quando
    # entrar um driver novo. Mesmo formato de FonteSolar.campos_credenciais.
    campos_credenciais: List[dict] = []
    capacidades: tuple = ()

    def __init__(self, config: Dict):
        self.config = dict(config or {})

    def tem(self, capacidade: str) -> bool:
        return capacidade in self.capacidades

    @abstractmethod
    def testar(self) -> dict:
        """
        Confirma que dá para falar com esta câmera. Devolve o que descobriu
        (fabricante, modelo, firmware) e levanta ValidationError com motivo
        legível se não der — é a mensagem que aparece no configurador.
        """

    @abstractmethod
    def perfis(self) -> List[Perfil]:
        """Os fluxos disponíveis, com snapshot e stream já resolvidos."""

    @abstractmethod
    def snapshot(self) -> bytes:
        """Um JPEG agora. Levanta ValidationError se não vier imagem."""

    @abstractmethod
    def url_stream(self) -> str:
        """
        A URL RTSP que o ffmpeg vai consumir, COM credenciais embutidas.

        Nunca mostre isto na tela nem em log: a senha da câmera está dentro.
        """
