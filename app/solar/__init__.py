"""
Registro de drivers de fontes solares.

Fabricante novo: implementar FonteSolar (base.py), importar aqui e
adicionar UMA entrada em DRIVERS. Nada mais muda — configurador, coletor e
telas descobrem o driver por este registro.
"""

from typing import Dict, Type

from ..errors import ValidationError
from .base import CANAIS_SOLAR, FonteSolar  # noqa: F401  (reexporta)
from .solplanet import SolPlanetDriver

DRIVERS: Dict[str, Type[FonteSolar]] = {
    SolPlanetDriver.id: SolPlanetDriver,
}


def get_driver(nome: str) -> Type[FonteSolar]:
    """A classe do driver; ValidationError se o nome não estiver registrado."""
    driver = DRIVERS.get((nome or "").strip().lower())
    if driver is None:
        raise ValidationError(
            "driver solar desconhecido: %r (registrados: %s)"
            % (nome, ", ".join(sorted(DRIVERS))))
    return driver


def lista_drivers() -> list:
    """Para o formulário do configurador: drivers com campos e níveis."""
    return [{
        "id": d.id,
        "rotulo": d.rotulo,
        "campos_credenciais": d.campos_credenciais,
        "niveis_acesso": [
            {"valor": n["valor"], "rotulo": n["rotulo"],
             "descricao": n.get("descricao", ""),
             "disponivel": bool(n.get("disponivel", True)),
             "capacidades": sorted(n.get("capacidades") or ())}
            for n in d.niveis_acesso
        ],
    } for d in DRIVERS.values()]


def capacidades_de(driver_nome: str, nivel_valor: str) -> set:
    """Capacidades de um nível sem precisar instanciar com credenciais."""
    driver = get_driver(driver_nome)
    valor = (nivel_valor or "").strip().lower()
    for nivel in driver.niveis_acesso:
        if nivel["valor"] == valor:
            return set(nivel.get("capacidades") or ())
    return set(driver.niveis_acesso[0].get("capacidades") or ())
