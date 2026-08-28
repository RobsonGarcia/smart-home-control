"""
Transporte LAN: comando direto no aparelho, pelo tinytuya.

Duas decisões que valem explicação:

1. **Conexão curta.** O coletor pode estar com um socket persistente aberto no
   mesmo aparelho — dispositivos Tuya aceitam poucas conexões simultâneas.
   Abrir, mandar e fechar convive melhor com o coletor do que disputar um
   socket persistente com ele.
2. **Duas tentativas.** Com protocolo 3.4/3.5 o primeiro pacote depois de um
   handshake novo se perde com alguma frequência. Uma repetição resolve; mais
   do que isso é a rede ou o aparelho, e aí a mensagem tem que aparecer.
"""

import logging
import time
from typing import Optional

from app.errors import ComandoError
from .base import Disponibilidade, Transporte

logger = logging.getLogger(__name__)

TENTATIVAS = 2
PAUSA_ENTRE_TENTATIVAS_S = 0.6
TIMEOUT_S = 5


class TransporteLan(Transporte):
    id = "lan"
    rotulo = "LAN (tinytuya)"

    def disponivel_para(self, device: dict) -> Disponibilidade:
        if (device or {}).get("source") == "solar":
            return Disponibilidade(False, "Inversor solar não recebe comando.")
        ip = (device or {}).get("ip")
        if not ip or ip == "0.0.0.0":
            return Disponibilidade(
                False, "Sem IP na LAN — o aparelho precisa aparecer num scan "
                       "da rede (ou usar a nuvem Tuya).")
        if not (device or {}).get("local_key"):
            return Disponibilidade(
                False, "Sem local_key — reimporte o inventário do Tuya Cloud.")
        return Disponibilidade(True)

    # -- interno ----------------------------------------------------------

    def _conectar(self, device: dict):
        # Import tardio pelo mesmo motivo de app/scanner.py: o painel tem que
        # subir e mostrar histórico numa máquina sem tinytuya instalado.
        try:
            import tinytuya
        except ImportError:
            raise ComandoError("tinytuya não está instalado: rode "
                               "pip install -r requirements.txt")

        aparelho = tinytuya.OutletDevice(device["id"], device["ip"],
                                         device["local_key"])
        aparelho.set_version(float(device.get("protocol_version") or 3.4))
        aparelho.set_socketTimeout(TIMEOUT_S)
        aparelho.set_socketPersistent(False)
        aparelho.set_socketRetryLimit(1)
        return aparelho

    def _erro_de(self, resposta) -> Optional[str]:
        """tinytuya devolve erro no dicionário, não por exceção."""
        if isinstance(resposta, dict) and "Err" in resposta:
            return str(resposta.get("Error") or resposta.get("Err"))
        return None

    # -- contrato ---------------------------------------------------------

    def enviar(self, device: dict, acao, valor) -> Optional[dict]:
        ultimo_erro = "sem resposta do aparelho"
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                aparelho = self._conectar(device)
                resposta = aparelho.set_value(acao.dp, valor)
                erro = self._erro_de(resposta)
                if erro is None:
                    logger.info("comando %s=%s enviado a %s (tentativa %d)",
                                acao.code, valor, device["name"], tentativa)
                    if isinstance(resposta, dict) and resposta.get("dps"):
                        return resposta["dps"]
                    return None
                ultimo_erro = erro
            except Exception as exc:  # rede, socket, protocolo
                ultimo_erro = str(exc)
            if tentativa < TENTATIVAS:
                time.sleep(PAUSA_ENTRE_TENTATIVAS_S)

        raise ComandoError("%s não respondeu ao comando (%s)."
                           % (device.get("name") or device["id"], ultimo_erro))

    def ler(self, device: dict) -> Optional[dict]:
        try:
            resposta = self._conectar(device).status()
        except Exception as exc:
            logger.debug("releitura de %s falhou: %s", device["id"], exc)
            return None
        if self._erro_de(resposta) is not None:
            return None
        return (resposta or {}).get("dps")
