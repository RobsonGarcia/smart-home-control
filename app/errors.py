"""
Erros de domínio, levantados pelo repository e traduzidos em HTTP no main.py.

O repository não conhece FastAPI — ele levanta estes; o handler registrado em
app/main.py faz o mapeamento para status code. Assim a validação pode morar
junto da query que a garante, sem espalhar try/except por todas as rotas.
"""


class DomainError(Exception):
    """Base. Carrega detalhes extras que viram campos na resposta JSON."""

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = message
        self.details = details


class NotFoundError(DomainError):
    """Registro inexistente -> HTTP 404."""


class ConflictError(DomainError):
    """Operação recusada pelo estado atual dos dados -> HTTP 409."""


class ValidationError(DomainError):
    """Entrada inválida -> HTTP 400."""


class ComandoError(DomainError):
    """
    O pedido era válido, mas o aparelho não obedeceu -> HTTP 502.

    Separado de ValidationError de propósito: "você mandou um valor inválido"
    e "o aparelho não respondeu" pedem reações opostas do usuário — corrigir
    o pedido num caso, checar a rede/energia no outro.
    """


STATUS_POR_ERRO = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 400,
    ComandoError: 502,
}


def status_para(exc: DomainError) -> int:
    return STATUS_POR_ERRO.get(type(exc), 400)
