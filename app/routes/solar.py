"""
Seção Solar: integrações com fabricantes de inversores e seus equipamentos.

O inversor É uma linha em devices (source='solar') — grupos de energia e
gráficos funcionam de graça — mas mora aqui, fora das telas de
dispositivos/locais. Toggle e intervalo reusam as rotas de /devices, que
operam em monitor_configs por device_id, indiferentes à fonte.

Regra de ouro deste arquivo: credenciais_json NUNCA sai numa resposta nem
num log. As integrações passam por _publica() antes de qualquer return.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Body, Request
from fastapi.responses import HTMLResponse

from app.config import SOLAR_POLL_INTERVAL_SECONDS
from app.dps_mapping import get_dp_info
from app.errors import ValidationError
from app.repository import (
    create_solar_integracao,
    criar_inversor_solar,
    delete_solar_integracao,
    get_all_device_statuses,
    get_all_locais,
    get_config_coleta_solar,
    get_device_status,
    get_readings_for_series,
    get_solar_integracao,
    get_solar_integracoes,
    get_solar_inversores,
    update_solar_integracao,
)
from app.solar import capacidades_de, get_driver, lista_drivers
from app.solar.base import CANAIS_SOLAR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/solar", tags=["solar"])

_ORDEM_CANONICA = {codigo: i for i, codigo in enumerate(CANAIS_SOLAR)}

# Canais que valem um gráfico no detalhe: potência total e por canal MPPT.
_CANAIS_GRAFICO = ["potencia_ca"] + ["potencia_mppt_%d" % n for n in range(1, 7)]


def _publica(integracao: dict) -> dict:
    """A integração sem o campo de credenciais — o único jeito de sair daqui."""
    return {k: v for k, v in integracao.items() if k != "credenciais_json"}


def _driver_de(integracao: dict):
    credenciais = json.loads(integracao["credenciais_json"])
    credenciais["planta_apikey"] = integracao.get("planta_apikey") or ""
    credenciais["nivel_acesso"] = integracao.get("nivel_acesso") or ""
    return get_driver(integracao["driver"])(credenciais)


def _descobrir_e_criar(integracao: dict) -> int:
    """
    Auto-discover: lista os inversores da planta, descobre os canais reais de
    cada um e materializa tudo (device + mapping + monitor_config). Falha ao
    ler canais não derruba o discover — o inversor entra sem mapping e um
    re-discover depois completa.
    """
    driver = _driver_de(integracao)
    if not driver.tem("inversores"):
        return 0
    criados = 0
    for inversor in driver.descobrir_inversores():
        mapping_json = None
        try:
            canais = (driver.descobrir_canais(inversor.sn)
                      if driver.tem("canais") else [])
            if canais:
                mapping_json = json.dumps(
                    {c["code"]: {"code": c["code"]} for c in canais})
        except Exception as exc:
            logger.warning("canais do inversor %s indisponíveis agora: %s",
                           inversor.sn, exc)
        criar_inversor_solar(
            integracao["id"], inversor.sn,
            mapping_json=mapping_json, psn=inversor.psn)
        criados += 1
    return criados


def _leituras_do_status(st: dict) -> dict:
    """dps_json da última leitura, como dict — {} quando não há leitura."""
    if not st or not st.get("reading") or not st["reading"].get("dps_json"):
        return {}
    try:
        return json.loads(st["reading"]["dps_json"])
    except ValueError:
        return {}


# --- rotas de API literais primeiro; /{param} sempre por último ------------

@router.get("/api/drivers")
async def drivers_api():
    """Fabricantes suportados — monta o formulário do configurador."""
    return {"drivers": lista_drivers()}


@router.post("/api/testar")
async def testar_credenciais(data: dict = Body(...)):
    """
    Passo 1 do configurador: valida as credenciais no fabricante e devolve as
    plantas da conta para o usuário escolher.
    """
    driver_cls = get_driver(data.get("driver"))
    credenciais = dict(data.get("credenciais") or {})
    credenciais["nivel_acesso"] = (data.get("nivel_acesso") or "").strip()
    driver = driver_cls(credenciais)
    driver.exigir_nivel_disponivel()
    driver.testar()
    plantas = driver.descobrir_plantas()
    return {"plantas": [{
        "apikey": p.apikey, "nome": p.nome, "potencia_kw": p.potencia_kw,
        "cidade": p.cidade, "status": p.status,
    } for p in plantas]}


@router.get("", response_class=HTMLResponse)
async def solar_list(request: Request):
    """A seção Solar: integrações, inversores e o configurador."""
    integracoes = [_publica(i) for i in get_solar_integracoes()]
    vinculos = get_solar_inversores()
    statuses = {
        st["device"]["id"]: st
        for st in get_all_device_statuses(incluir_solar=True)
        if (st["device"].get("source") == "solar")
    }

    for integracao in integracoes:
        integracao["capacidades"] = sorted(capacidades_de(
            integracao["driver"], integracao.get("nivel_acesso")))
        inversores = []
        for v in vinculos:
            if v["integracao_id"] != integracao["id"]:
                continue
            st = statuses.get(v["device_id"])
            leituras = _leituras_do_status(st)
            inversores.append({
                "vinculo": v,
                "st": st,
                "potencia": leituras.get("potencia_ca"),
                "geracao_hoje": leituras.get("geracao_hoje"),
            })
        integracao["inversores"] = inversores

    return request.app.templates.get_template("solar/list.html").render(
        request=request,
        integracoes=integracoes,
        drivers=lista_drivers(),
        locais=get_all_locais(),
        intervalo_padrao=SOLAR_POLL_INTERVAL_SECONDS,
    )


@router.post("/integracoes", status_code=201)
async def criar_integracao(data: dict = Body(...)):
    """
    Passo 2 do configurador: cria a integração e roda o auto-discover dos
    inversores. As credenciais já foram validadas no /api/testar — mas o
    discover falhar aqui não pode deixar integração órfã, então ele roda com
    um driver montado ANTES de gravar qualquer coisa.
    """
    driver_nome = data.get("driver")
    get_driver(driver_nome)  # valida o nome antes de qualquer efeito
    credenciais = data.get("credenciais") or {}
    if not any((v or "").strip() for v in credenciais.values()):
        raise ValidationError("Informe as credenciais do fabricante")

    nivel_acesso = (data.get("nivel_acesso") or "pro").strip().lower()
    driver_meta = get_driver(driver_nome)({"nivel_acesso": nivel_acesso})
    driver_meta.exigir_nivel_disponivel()

    planta_apikey = (data.get("planta_apikey") or "").strip() or None
    local_id = data.get("local_id") or None
    try:
        poll_interval = max(60, int(data.get("poll_interval_seconds")
                                    or SOLAR_POLL_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        raise ValidationError("Intervalo de coleta inválido")

    integracao_id = create_solar_integracao(
        driver_nome,
        data.get("nome"),
        json.dumps(credenciais),
        planta_apikey=planta_apikey,
        planta_nome=(data.get("planta_nome") or "").strip() or None,
        local_id=local_id,
        poll_interval_seconds=poll_interval,
        nivel_acesso=nivel_acesso,
    )
    integracao = get_solar_integracao(integracao_id)
    try:
        criados = _descobrir_e_criar(integracao)
    except Exception:
        # Sem inversor não há o que coletar: desfaz para não deixar uma
        # integração fantasma que o usuário não consegue completar pela UI.
        delete_solar_integracao(integracao_id)
        raise

    return {"id": integracao_id, "inversores": criados}


@router.post("/integracoes/{integracao_id}/descobrir")
async def redescobrir(integracao_id: int):
    """Re-discover: inversor trocado ou adicionado na planta."""
    integracao = get_solar_integracao(integracao_id)
    criados = _descobrir_e_criar(integracao)
    return {"inversores": criados}


@router.get("/api/integracoes/{integracao_id}/resumo")
async def resumo_integracao(integracao_id: int):
    """
    Cartão de resumo da planta, direto da cloud do fabricante. É uma chamada
    de rede — a página carrega sem ela e preenche via JS quando responder.
    """
    integracao = get_solar_integracao(integracao_id)
    driver = _driver_de(integracao)
    if not driver.tem("resumo"):
        raise ValidationError("O nível de acesso desta integração não "
                              "fornece o resumo da planta")
    resumo = driver.resumo_planta()
    return {
        "potencia_w": resumo.potencia_w,
        "geracao_hoje_kwh": resumo.geracao_hoje_kwh,
        "geracao_mes_kwh": resumo.geracao_mes_kwh,
        "geracao_total_kwh": resumo.geracao_total_kwh,
        "ultima_atualizacao": resumo.ultima_atualizacao,
        "status": resumo.status,
    }


@router.put("/integracoes/{integracao_id}")
async def editar_integracao(integracao_id: int, data: dict = Body(...)):
    """Parâmetros da integração; local e intervalo cascateiam aos inversores."""
    kwargs = {
        "nome": data.get("nome"),
        "enabled": data.get("enabled"),
        "poll_interval_seconds": data.get("poll_interval_seconds"),
    }
    if "local_id" in data:
        kwargs["local_id"] = data.get("local_id") or None
    update_solar_integracao(integracao_id, **kwargs)
    return _publica(get_solar_integracao(integracao_id))


@router.delete("/integracoes/{integracao_id}")
async def excluir_integracao(integracao_id: int):
    resultado = delete_solar_integracao(integracao_id)
    return {"deleted": True, **resultado}


@router.get("/inversores/{device_id}", response_class=HTMLResponse)
async def inversor_detail(device_id: str, request: Request):
    """Detalhe do inversor: leituras por canal e o gráfico do dia."""
    st = get_device_status(device_id)
    if not st or st["device"].get("source") != "solar":
        return HTMLResponse(
            "<h1>Inversor não encontrado</h1>"
            "<p><a href=\"/solar\">Voltar</a></p>", status_code=404)

    vinculo = get_config_coleta_solar(device_id) or {}
    leituras_brutas = _leituras_do_status(st)

    leituras = []
    for codigo in sorted(leituras_brutas,
                         key=lambda c: _ORDEM_CANONICA.get(c, 999)):
        if codigo == "tmstp":
            continue
        info = get_dp_info(codigo)
        leituras.append({
            "code": codigo,
            "name": info["name"],
            "unit": info.get("unit", ""),
            "valor": leituras_brutas[codigo],
        })

    # Séries de potência das últimas 24 h, prontas para o gráfico.
    inicio = (datetime.now(timezone.utc) - timedelta(hours=24)
              ).strftime("%Y-%m-%d %H:%M:%S")
    series = []
    for codigo in _CANAIS_GRAFICO:
        if codigo not in leituras_brutas and codigo != "potencia_ca":
            continue
        pontos = get_readings_for_series(device_id, codigo,
                                         start_timestamp=inicio)
        if pontos:
            series.append({
                "id": codigo,
                "label": get_dp_info(codigo)["name"],
                "data": pontos,
            })

    integracao = None
    if vinculo.get("integracao_id"):
        integracao = _publica(get_solar_integracao(vinculo["integracao_id"]))

    return request.app.templates.get_template("solar/detail.html").render(
        request=request,
        st=st,
        vinculo=vinculo,
        integracao=integracao,
        leituras=leituras,
        series=series,
    )
