"""
Transporte nuvem: comando pela API da Tuya, para o que não está na LAN.

É o caminho de quem não tem IP (a tomada da geladeira, a bomba que não
apareceu no scan) e o único caminho possível para os controles infravermelho,
que fisicamente não existem na rede — quem existe é o hub.

**Estado: implementado, não verificado com conta real.** As credenciais do
projeto na Tuya IoT Platform não estavam disponíveis quando isto foi escrito;
`disponivel_para` devolve o motivo e a tela se limita sozinha. Quando o
`tuya.local.json` aparecer, a validação começa pela sonda, não pela interface.

Formato de `tuya.local.json` (na raiz, gitignorado):

    {"apiKey": "...", "apiSecret": "...", "apiRegion": "us",
     "apiDeviceID": "<id de qualquer dispositivo da conta>"}
"""

import json
import logging
from typing import Optional

from app.config import TUYA_LOCAL_JSON_PATH
from app.errors import ComandoError
from .base import Disponibilidade, Transporte

logger = logging.getLogger(__name__)

_CAMPOS = ("apiKey", "apiSecret", "apiRegion", "apiDeviceID")


def credenciais_cloud() -> Optional[dict]:
    """As credenciais do arquivo local, ou None se não dá para usar a nuvem."""
    try:
        if not TUYA_LOCAL_JSON_PATH.exists():
            return None
        with open(TUYA_LOCAL_JSON_PATH, encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("tuya.local.json ilegível: %s", exc)
        return None
    if not all(dados.get(campo) for campo in _CAMPOS):
        return None
    return dados


class TransporteCloud(Transporte):
    id = "cloud"
    rotulo = "Nuvem Tuya"

    def disponivel_para(self, device: dict) -> Disponibilidade:
        if (device or {}).get("source") == "solar":
            return Disponibilidade(False, "Inversor solar não recebe comando.")
        if credenciais_cloud() is None:
            return Disponibilidade(
                False, "Nuvem Tuya não configurada — falta um tuya.local.json "
                       "com apiKey, apiSecret, apiRegion e apiDeviceID.")
        return Disponibilidade(True)

    def _cliente(self):
        try:
            import tinytuya
        except ImportError:
            raise ComandoError("tinytuya não está instalado: rode "
                               "pip install -r requirements.txt")
        c = credenciais_cloud()
        if c is None:
            raise ComandoError("Nuvem Tuya não configurada.")
        return tinytuya.Cloud(apiRegion=c["apiRegion"], apiKey=c["apiKey"],
                              apiSecret=c["apiSecret"],
                              apiDeviceID=c["apiDeviceID"])

    def _falhou(self, resposta) -> Optional[str]:
        if not isinstance(resposta, dict):
            return "resposta inesperada da nuvem"
        if resposta.get("success"):
            return None
        return str(resposta.get("msg") or resposta.get("Error")
                   or "a nuvem recusou o comando")

    def enviar(self, device: dict, acao, valor) -> Optional[dict]:
        # A nuvem fala por CÓDIGO ("switch_1"), não pelo número do DP — é a
        # diferença de vocabulário que este transporte existe para esconder.
        comando = {"commands": [{"code": acao.code, "value": valor}]}
        try:
            resposta = self._cliente().sendcommand(device["id"], comando)
        except Exception as exc:
            raise ComandoError("Falha ao falar com a nuvem Tuya: %s" % exc)

        erro = self._falhou(resposta)
        if erro:
            raise ComandoError("A nuvem recusou o comando para %s: %s"
                               % (device.get("name") or device["id"], erro))
        return None

    def ler(self, device: dict) -> Optional[dict]:
        try:
            resposta = self._cliente().getstatus(device["id"])
        except Exception as exc:
            logger.debug("status na nuvem de %s falhou: %s", device["id"], exc)
            return None
        if self._falhou(resposta):
            return None
        # A nuvem devolve [{code, value}]; o painel guarda {code: value}.
        return {item["code"]: item["value"]
                for item in resposta.get("result") or []
                if isinstance(item, dict) and "code" in item}
