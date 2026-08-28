"""
O caminho de um comando, do clique ao aparelho.

Vive aqui, e não na rota, porque tem mais de um chamador: a tela de
dispositivo manda `switch_1`, a tela de câmera manda `ptz_control`. É o mesmo
percurso — e é onde a política de segurança acontece de verdade.

A ordem das barreiras é deliberada:

  1. o dispositivo existe;
  2. o dono LIBEROU o acionamento dele (opt-in, coluna `acionavel`);
  3. a ação está entre as que o aparelho declara (o que exclui, de graça,
     tudo que é bloqueado — fechadura nunca chega aqui);
  4. o valor cabe no tipo e na faixa que o próprio aparelho publicou.

Só então existe pacote na rede. A tela não é a proteção: um POST direto na API
passa exatamente pelas mesmas quatro portas.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.capacidades import acao_por_dp, validar_valor
from app.config import ONLINE_WINDOW_MINUTES
from app.errors import ComandoError, NotFoundError, ValidationError
from app.repository import (
    get_device,
    get_latest_reading,
    insert_reading,
    registrar_comando,
)
from . import transporte_para

logger = logging.getLogger(__name__)


def _quadro_recente(device_id: str) -> dict:
    """
    Os DPs da última leitura, SE ela ainda for recente.

    Serve de base para o merge: a resposta de um comando costuma trazer só o
    DP que mudou, e gravar isso sozinho faria o resto dos valores sumir da
    tela. Mas o corte de idade não é preciosismo — a leitura nova nasce com
    `collected_at` de AGORA, então mesclar um quadro de três horas atrás
    carimbaria potência velha como se fosse atual e sujaria os gráficos de
    energia. Quadro velho: melhor gravar só o que sabemos de verdade.
    """
    leitura = get_latest_reading(device_id)
    if not leitura or not leitura.get("online"):
        return {}

    try:
        idade = datetime.now(timezone.utc).replace(tzinfo=None) - \
            datetime.strptime(leitura["collected_at"][:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return {}
    if idade > timedelta(minutes=ONLINE_WINDOW_MINUTES):
        return {}

    try:
        dados = json.loads(leitura["dps_json"])
        return dados if isinstance(dados, dict) else {}
    except (ValueError, TypeError):
        return {}


def executar_comando(device_id: str, dp: str, valor, origem: str = "painel"):
    """
    Aciona um dispositivo e devolve o estado resultante.

    Levanta NotFoundError (404), ValidationError (400) ou ComandoError (502) —
    o handler de `app/main.py` traduz cada um.
    """
    device = get_device(device_id)
    if not device:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)

    if not device.get("acionavel"):
        raise ValidationError(
            "'%s' não está liberado para acionamento. Ligue a chave de "
            "acionamento na tela do dispositivo antes." % device["name"])

    acao = acao_por_dp(device, dp)
    if acao is None:
        raise ValidationError(
            "'%s' não aceita o comando '%s'." % (device["name"], dp))

    valor = validar_valor(acao, valor)
    transporte = transporte_para(device)

    try:
        dps_resposta = transporte.enviar(device, acao, valor)
    except ComandoError as exc:
        registrar_comando(device_id, acao.dp, acao.code, valor,
                          transporte.id, ok=False, erro=str(exc.message),
                          origem=origem)
        raise

    registrar_comando(device_id, acao.dp, acao.code, valor, transporte.id,
                      ok=True, origem=origem)

    # Releitura: o painel tem que mostrar o estado NOVO agora, sem esperar o
    # próximo ciclo do coletor. A resposta do próprio comando às vezes já traz
    # os DPs; quando não traz, vale uma leitura completa.
    dps = dps_resposta or transporte.ler(device)
    estado = _quadro_recente(device_id)
    if dps:
        estado.update(dps)
    else:
        estado[str(acao.dp)] = valor
        logger.info("comando em %s enviado sem confirmação de estado; "
                    "assumindo %s=%s", device_id, acao.dp, valor)

    if estado:
        insert_reading(device_id, json.dumps(estado), True)

    return {
        "device_id": device_id,
        "dp": acao.dp,
        "code": acao.code,
        "nome": acao.nome,
        "valor": valor,
        "transporte": transporte.id,
        "confirmado": bool(dps),
        "dps": estado,
    }
