"""
Registro de transportes de comando.

Mesma forma do `app/solar/`: uma lista de implementações e uma função que
escolhe. A ordem importa — LAN primeiro, porque comando local não depende de
internet, não tem latência de nuvem e não conta cota de API.
"""

from typing import List, Optional, Tuple

from app.errors import ValidationError
from .base import Disponibilidade, Transporte
from .cloud import TransporteCloud
from .lan import TransporteLan

TRANSPORTES: List[Transporte] = [TransporteLan(), TransporteCloud()]


def transportes_de(device: dict) -> List[Tuple[Transporte, Disponibilidade]]:
    """Todos os transportes com o veredito de cada um — é o que a tela mostra."""
    return [(t, t.disponivel_para(device)) for t in TRANSPORTES]


def transporte_para(device: dict) -> Transporte:
    """
    O primeiro transporte que serve para este aparelho.

    Sem nenhum, levanta ValidationError juntando os motivos: "não dá para
    acionar" sem dizer por quê é o tipo de erro que faz o usuário abrir o log.
    """
    motivos = []
    for transporte, veredito in transportes_de(device):
        if veredito.ok:
            return transporte
        if veredito.motivo and veredito.motivo not in motivos:
            motivos.append(veredito.motivo)

    raise ValidationError(
        "Nenhum caminho para acionar '%s'. %s"
        % (device.get("name") or device.get("id"), " ".join(motivos)))


__all__ = ["TRANSPORTES", "Transporte", "Disponibilidade", "transporte_para",
           "transportes_de", "TransporteLan", "TransporteCloud"]
