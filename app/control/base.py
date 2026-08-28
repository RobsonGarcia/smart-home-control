"""
Transporte de comando: por onde um pedido chega até o aparelho.

A rota de comando não sabe se o dispositivo está na LAN ou só na nuvem — ela
pede um transporte para o dispositivo e manda. Trocar o caminho (LAN caiu,
credencial da nuvem apareceu) não mexe em rota, tela nem validação.

O que passa por aqui já foi validado: a `Acao` veio de
`app/capacidades.acoes_do_dispositivo` e o valor de `validar_valor`. Transporte
não decide o que pode ser acionado — decide como o pacote sai.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class Disponibilidade:
    """Se este transporte serve para este aparelho — e, se não serve, por quê."""
    ok: bool
    motivo: str = ""


class Transporte(ABC):
    id: str = ""
    rotulo: str = ""

    @abstractmethod
    def disponivel_para(self, device: dict) -> Disponibilidade:
        """Sem levantar exceção: a resposta negativa é informação para a tela."""

    @abstractmethod
    def enviar(self, device: dict, acao, valor) -> Optional[dict]:
        """
        Manda o comando. Devolve os DPs que o aparelho reportou na resposta,
        quando ele reporta algum — senão None, e quem chamou relê com `ler`.

        Levanta ComandoError com mensagem em português se o aparelho não
        confirmar.
        """

    @abstractmethod
    def ler(self, device: dict) -> Optional[dict]:
        """Estado atual (dict de DPs), ou None se não deu para ler."""
