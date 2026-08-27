"""
Driver SolPlanet (fabricante AiSWEI) — API "AISWEICloud".

Fontes: docs/Aiswei api-Business-singapore.pdf (família Pro, /pro/*) e o par
de PDFs EU V3 "Business User" / "End User" (obtidos do repositório público
PatMan6889/AISWEI-Solplanet-Cloud-API, que também serviu de referência viva
para a assinatura). Três coisas que este driver isola do resto do sistema:

  1. A ASSINATURA. A API fica atrás de um Alibaba Cloud API Gateway. O que o
     gateway da AiSWEI aceita (verificado contra a implementação de
     referência) é mais simples que o PDF sugere: assina-se apenas o header
     X-Ca-Key, com string-to-sign
         GET \n Accept \n \n Content-Type \n \n X-Ca-Key:{appkey} \n
         {path?query ordenada}
     e X-Ca-Signature = Base64(HMAC-SHA256(AppSecret, string-to-sign)).
     Quando a assinatura falha, o gateway devolve o string-to-sign DELE no
     header X-Ca-Error-Message — é o único jeito de depurar.

  2. A ESCALA. Os valores de telemetria chegam como STRING escalada:
     "v1": "4203" são 420,3 V; "i1": "185" são 1,85 A. A tabela MAPA_CANAIS
     carrega campo → (código canônico, fator) e a conversão acontece aqui.
     Já o overview da planta traz unidade junto ("KW", "MWh") — a conversão
     é por unidade.

  3. A NOMENCLATURA. `pac`, `etd`, `v1`… não saem deste arquivo. O que sai
     são os códigos canônicos de CANAIS_SOLAR (base.py).

Os DOIS níveis de acesso usam o mesmo gateway e as mesmas credenciais de
assinatura (App Key + Secret), mas famílias de endpoint diferentes:

  - Pro (Business): /pro/getPlanListPro, /pro/getLastTsDataPro… com o
    token de usuário Pro. Envelope {"status": 200, "data": ...}.
  - Comum (End User, a conta do cloud.solplanet.net): /planlist,
    /devicelist, /getInverterData, /getPlantOverview… O token (quando o
    suporte fornece) só é necessário para LISTAR as plantas; o resto usa o
    API Key da planta. Envelopes variados ({"data": ...}, {"code": 200} ou
    objeto direto) — tratados um a um.

A telemetria por inversor tem os MESMOS campos e escalas nas duas famílias;
no nível comum a "última leitura" é o ponto mais recente de uma janela curta
de getInverterData, porque não existe um getLastTsData sem sufixo Pro.
"""

import base64
import hashlib
import hmac
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Tuple

from ..errors import ValidationError
from .base import (CANAIS_SOLAR, CAPACIDADES, FonteSolar, Inversor, Planta,
                   ResumoPlanta, Telemetria)

# O "genergal" é assim mesmo — é o domínio registrado pela AiSWEI.
HOSTS = {
    "eu": "https://eu-api-genergal.aisweicloud.com",
    "ap": "https://ap-southeast-1-api-genergal.aisweicloud.com",
}
REGIAO_PADRAO = "eu"
TIMEOUT_SEGUNDOS = 20
TAMANHO_PAGINA_HISTORICO = 200
# Janela para "última leitura" no nível comum (o equipamento mede a cada
# ~5 min; 45 min cobrem atrasos de upload do coletor PMU).
JANELA_ULTIMA_LEITURA_MIN = 45
# O limite documentado é 100 req/min, mas o gateway corta RAJADAS bem
# menores com um 403 seco (sem headers X-Ca) — visto ao vivo quando o
# coletor dispara os jobs dos inversores e o backfill no mesmo instante.
# Por isso as chamadas do processo são SERIALIZADAS (_TRAVA) com um
# espaçamento mínimo, e 403 ganha retry com backoff.
PAUSA_ENTRE_CHAMADAS_S = 1.2
ESPACAMENTO_MINIMO_S = 0.8
TENTATIVAS_403 = 3
_TRAVA = threading.Lock()
_ULTIMA_CHAMADA = [0.0]

# O backend da AiSWEI grava o tmstp como o epoch da HORA LOCAL DA PLANTA
# interpretada em UTC+8 (China). Verificado ao vivo numa planta em UTC-3:
# tim=17:52:33 (hora local real) veio com tmstp equivalente a 09:52:33 UTC —
# exatamente 17:52:33 - 8 h. O exemplo do PDF mascara o defeito porque a
# planta é chinesa (lá hora local - 8 h É o UTC). A correção: voltar o tmstp
# para a hora local da planta (+8 h) e reinterpretá-la no fuso REAL da
# planta — assumido igual ao da máquina, já que este é um painel doméstico
# que roda na casa que tem as placas no telhado.
_FUSO_BACKEND = timedelta(hours=8)

# Mensagens para os códigos do envelope Pro {"status": ..., "info": ...}.
ERROS_API = {
    10001: "a API recusou os parâmetros da consulta",
    10012: "a planta ou o inversor não existe na conta AiSWEI",
    10403: "o token não tem permissão para ver esta planta",
    20006: "usuário não encontrado — confira o token",
}

ESTADOS_PLANTA = {0: "offline", 1: "normal", 2: "alerta", 3: "erro"}
ESTADOS_INVERSOR = {0: "offline", 1: "normal", 2: "cache"}

# --------------------------------------------------------------------------
# Campo da AiSWEI -> (código canônico, fator de escala). A precisão vem das
# tabelas dos PDFs (idênticas nas duas famílias): 0.1 V, 0.01 A, 0.1 kWh etc.
# --------------------------------------------------------------------------
MAPA_CANAIS: Dict[str, Tuple[str, float]] = {
    "pac": ("potencia_ca", 1.0),
    "etd": ("geracao_hoje", 0.1),
    "eto": ("geracao_total", 0.1),
    "fac": ("frequencia_ca", 0.01),
    "cf": ("temperatura", 0.1),
}
for _n in range(1, 7):
    MAPA_CANAIS["v%d" % _n] = ("tensao_mppt_%d" % _n, 0.1)
    MAPA_CANAIS["i%d" % _n] = ("corrente_mppt_%d" % _n, 0.01)
for _n in range(1, 11):
    MAPA_CANAIS["s%d" % _n] = ("corrente_string_%d" % _n, 0.1)
for _n in range(1, 4):
    MAPA_CANAIS["va%d" % _n] = ("tensao_ca_%d" % _n, 0.1)
    MAPA_CANAIS["ia%d" % _n] = ("corrente_ca_%d" % _n, 0.1)
del _n

# A ordem canônica para listagens (dict do Python preserva inserção).
_ORDEM_CANONICA = {codigo: i for i, codigo in enumerate(CANAIS_SOLAR)}


def _epoch_utc_real(tmstp_ms: int) -> int:
    """tmstp da AiSWEI (hora da planta fingindo UTC+8) -> epoch UTC real."""
    if not tmstp_ms:
        return 0
    hora_da_planta = (datetime.fromtimestamp(tmstp_ms / 1000.0,
                                             tz=timezone.utc).replace(tzinfo=None)
                      + _FUSO_BACKEND)
    fuso_local = datetime.now().astimezone().tzinfo
    return int(hora_da_planta.replace(tzinfo=fuso_local).timestamp() * 1000)

# Multiplicadores para valores que chegam COM unidade (overview da planta).
_FATOR_POTENCIA = {"w": 1.0, "kw": 1000.0, "mw": 1e6}
_FATOR_ENERGIA = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}


def _converter(bruto: dict) -> dict:
    """
    Um registro cru de telemetria -> dict canônico em unidade real.

    Campos null/vazios não entram (um inversor de 2 canais devolve v3 null);
    potência por canal MPPT não vem pronta — é sintetizada de V × I, que é a
    série que interessa para comparar strings de placas.
    """
    valores = {}
    for campo, (codigo, fator) in MAPA_CANAIS.items():
        v = bruto.get(campo)
        if v is None or v == "":
            continue
        try:
            valores[codigo] = round(float(v) * fator, 3)
        except (TypeError, ValueError):
            continue

    for n in range(1, 7):
        tensao = valores.get("tensao_mppt_%d" % n)
        corrente = valores.get("corrente_mppt_%d" % n)
        if tensao is not None and corrente is not None:
            valores["potencia_mppt_%d" % n] = round(tensao * corrente, 1)

    return valores


def _valor_com_unidade(item, fatores, padrao=1.0) -> float:
    """{"unit": "MWh", "value": 54.65} -> 54650.0 (na unidade canônica)."""
    if not isinstance(item, dict):
        return 0.0
    try:
        v = float(item.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0
    unidade = str(item.get("unit") or "").strip().lower()
    return v * fatores.get(unidade, padrao)


class SolPlanetDriver(FonteSolar):
    id = "solplanet"
    rotulo = "SolPlanet (AiSWEI)"
    # A conta comum do cloud.solplanet.net fornece App Key (9 dígitos) +
    # API Secret (perfil -> Account and security -> Account settings) e o
    # API Key da planta (Plant -> detalhes). O token vem do suporte; no
    # nível comum ele só serve para listar as plantas da conta.
    campos_credenciais = [
        {"chave": "appkey", "rotulo": "App Key", "secreto": False},
        {"chave": "appsecret", "rotulo": "App Secret", "secreto": True},
        {"chave": "token", "rotulo": "Token de usuário", "secreto": True,
         "opcional": True,
         "dica": "Pro: obrigatório. Comum: só para listar as plantas — sem "
                 "ele, informe o API Key da planta."},
        {"chave": "planta_apikey", "rotulo": "API Key da planta",
         "secreto": False, "opcional": True,
         "dica": "cloud.solplanet.net → Plant → detalhes da planta. "
                 "Obrigatório quando não há token."},
        {"chave": "regiao", "rotulo": "Região", "secreto": False,
         "opcional": True,
         "dica": "eu (Europa, padrão) ou ap (Ásia — as contas do Brasil "
                 "ficam neste gateway)."},
    ]
    # Mesmo gateway, duas famílias de endpoint — ver o cabeçalho do módulo.
    niveis_acesso = [
        {
            "valor": "pro", "rotulo": "Pro (Business)",
            "descricao": "Família /pro/* com token de usuário: lista as "
                         "plantas da conta, telemetria por inversor e "
                         "canal, histórico e resumo.",
            "disponivel": True,
            "capacidades": set(CAPACIDADES),
        },
        {
            "valor": "comum", "rotulo": "Comum (conta cloud.solplanet.net)",
            "descricao": "Família End User: mesmos dados de telemetria por "
                         "canal e histórico; a listagem de plantas exige o "
                         "token — sem ele, informe o API Key da planta.",
            "disponivel": True,
            "capacidades": set(CAPACIDADES),
        },
    ]

    # -- infraestrutura de request ----------------------------------------

    def _e_comum(self) -> bool:
        return self.nivel_acesso()["valor"] == "comum"

    def _credencial(self, chave: str) -> str:
        valor = (self.credenciais.get(chave) or "").strip()
        if not valor:
            raise ValidationError("credencial '%s' não informada" % chave)
        return valor

    def _token(self) -> str:
        return (self.credenciais.get("token") or "").strip()

    def _apikey_planta(self) -> str:
        valor = (self.credenciais.get("planta_apikey") or "").strip()
        if not valor:
            raise ValidationError(
                "a integração ainda não tem planta definida (planta_apikey)")
        return valor

    def _host(self) -> str:
        regiao = (self.credenciais.get("regiao") or REGIAO_PADRAO).strip().lower()
        host = HOSTS.get(regiao)
        if not host:
            raise ValidationError(
                "região desconhecida: %r (aceitas: %s)"
                % (regiao, ", ".join(sorted(HOSTS))))
        return host

    def _get(self, caminho: str, params: dict) -> dict:
        """
        GET assinado; devolve o JSON parseado (o envelope varia por endpoint
        e é tratado no chamador). Erro de gateway, envelope Pro com status
        != 200, envelope End User com code != 200, ou falha de rede viram
        ValidationError com mensagem legível.
        """
        try:
            import requests
        except ImportError:
            raise ValidationError(
                "requests não está instalado: a integração solar precisa dele. "
                "Rode: pip install -r requirements.txt")

        # Um nível declarado-mas-não-implementado nunca chega na rede.
        self.exigir_nivel_disponivel()
        appkey = self._credencial("appkey")
        appsecret = self._credencial("appsecret")

        # Assinatura verificada contra a implementação de referência: só o
        # X-Ca-Key entra no bloco de headers; Content-MD5 e Date ficam
        # vazios; a query é ordenada e assinada SEM url-encode (o gateway
        # decodifica antes de recalcular).
        accept = "application/json"
        content_type = "application/json; charset=UTF-8"
        query_ordenada = "&".join(
            "%s=%s" % (k, params[k]) for k in sorted(params))
        url_assinada = caminho + ("?" + query_ordenada if query_ordenada else "")
        string_to_sign = "GET\n%s\n\n%s\n\nX-Ca-Key:%s\n%s" % (
            accept, content_type, appkey, url_assinada)
        assinatura = base64.b64encode(
            hmac.new(appsecret.encode("utf-8"), string_to_sign.encode("utf-8"),
                     hashlib.sha256).digest()).decode("ascii")

        headers = {
            "User-Agent": "app 1.0",
            "Accept": accept,
            "Content-Type": content_type,
            "X-Ca-Key": appkey,
            "X-Ca-Signature": assinatura,
            "X-Ca-Signature-Headers": "X-Ca-Key",
        }

        resposta = None
        for tentativa in range(TENTATIVAS_403):
            # Uma chamada por vez no processo inteiro, com espaçamento — os
            # jobs do coletor (2 inversores + backfill) partem juntos e o
            # gateway não tolera a rajada.
            with _TRAVA:
                espera = _ULTIMA_CHAMADA[0] + ESPACAMENTO_MINIMO_S - time.monotonic()
                if espera > 0:
                    time.sleep(espera)
                try:
                    resposta = requests.get(self._host() + caminho,
                                            params=params, headers=headers,
                                            timeout=TIMEOUT_SEGUNDOS)
                except requests.RequestException as exc:
                    raise ValidationError(
                        "falha de rede ao falar com a AiSWEI: %s" % exc)
                finally:
                    _ULTIMA_CHAMADA[0] = time.monotonic()
            if resposta.status_code != 403:
                break
            if tentativa < TENTATIVAS_403 - 1:
                time.sleep(3.0 * (tentativa + 1))

        if resposta.status_code >= 400:
            partes = [resposta.headers.get("X-Ca-Error-Code", "").strip(),
                      resposta.headers.get("X-Ca-Error-Message", "").strip()]
            detalhe = " ".join(x for x in partes if x)
            raise ValidationError(
                "a API AiSWEI recusou o request (HTTP %d%s)"
                % (resposta.status_code,
                   ": %s" % detalhe if detalhe else ""))

        try:
            corpo = resposta.json()
        except ValueError:
            raise ValidationError("a API AiSWEI devolveu uma resposta que não é JSON")

        if isinstance(corpo, dict):
            status = corpo.get("status")
            if isinstance(status, int) and status != 200:
                motivo = ERROS_API.get(status,
                                       corpo.get("info") or "erro desconhecido")
                raise ValidationError("AiSWEI respondeu %s: %s" % (status, motivo))
            codigo = corpo.get("code")
            if isinstance(codigo, int) and codigo != 200:
                raise ValidationError(
                    "AiSWEI respondeu code=%s: %s"
                    % (codigo, corpo.get("msg") or corpo.get("info")
                       or "erro desconhecido"))
        return corpo

    # -- a interface FonteSolar -------------------------------------------

    def testar(self) -> None:
        # Com a planta informada, valida direto nela — o caminho da conta
        # sem token, que não pode listar plantas.
        apikey = (self.credenciais.get("planta_apikey") or "").strip()
        if self._e_comum():
            if apikey:
                self._get("/getPlantOverview", {"key": apikey})
            elif self._token():
                self._get("/planlist", {"token": self._token(),
                                        "page": 1, "size": 1})
            else:
                raise ValidationError(
                    "Informe o token OU o API Key da planta "
                    "(cloud.solplanet.net → Plant → detalhes)")
            return
        if apikey:
            self._get("/pro/getPlantOverviewPro",
                      {"token": self._credencial("token"), "apikey": apikey})
        else:
            self._get("/pro/getPlanListPro",
                      {"token": self._credencial("token"),
                       "order": 1, "pageNum": 1, "pageSize": 1})

    def descobrir_plantas(self) -> List[Planta]:
        apikey = (self.credenciais.get("planta_apikey") or "").strip()

        if self._e_comum():
            if self._token():
                corpo = self._get("/planlist", {"token": self._token(),
                                                "page": 1, "size": 100})
                lista = ((corpo.get("data") or {}).get("list")) or []
                plantas = [Planta(
                    apikey=str(p.get("apikey") or ""),
                    nome=str(p.get("name") or "").strip() or "Planta sem nome",
                    potencia_kw=float(p.get("totalpower") or 0),
                    cidade=str(p.get("city") or "").strip(),
                    status=ESTADOS_PLANTA.get(p.get("status"), ""),
                ) for p in lista]
                if plantas:
                    return plantas
            if apikey:
                return [self._planta_do_overview(apikey)]
            raise ValidationError(
                "Sem token a conta não lista plantas — informe o API Key "
                "da planta")

        if apikey:
            return [self._planta_do_overview(apikey)]
        data = self._get("/pro/getPlanListPro",
                         {"token": self._credencial("token"),
                          "order": 1, "pageNum": 1, "pageSize": 100}
                         ).get("data") or {}
        plantas = []
        for p in data.get("result") or []:
            plantas.append(Planta(
                apikey=str(p.get("apikey") or ""),
                nome=str(p.get("name") or "").strip() or "Planta sem nome",
                potencia_kw=float(p.get("totalpower") or 0),
                cidade=str(p.get("city") or "").strip(),
                status=ESTADOS_PLANTA.get(p.get("status"), ""),
            ))
        return plantas

    def _planta_do_overview(self, apikey: str) -> Planta:
        """Planta informada à mão, enriquecida pelo overview dela."""
        resumo = self._resumo(apikey)
        return Planta(apikey=apikey, nome="Planta %s…" % apikey[:8],
                      potencia_kw=0.0, status=resumo.status)

    def descobrir_inversores(self) -> List[Inversor]:
        if self._e_comum():
            corpo = self._get("/devicelist", {"key": self._apikey_planta()})
            pmus = ((corpo.get("data") or {}).get("list")) or []
        else:
            corpo = self._get("/pro/getDeviceListPro",
                              {"token": self._credencial("token"),
                               "apikey": self._apikey_planta()})
            data = corpo.get("data")
            pmus = data if isinstance(data, list) else []

        inversores = []
        for pmu in pmus:
            psn = str(pmu.get("psn") or "")
            for inv in pmu.get("inverters") or []:
                sn = str(inv.get("isn") or "").strip()
                if not sn:
                    continue
                inversores.append(Inversor(
                    sn=sn,
                    estado=ESTADOS_INVERSOR.get(inv.get("istate"), ""),
                    ultima_comunicacao=str(inv.get("ludt") or ""),
                    psn=psn,
                ))
        return inversores

    def descobrir_canais(self, sn: str) -> List[dict]:
        telemetria = self.ultima_telemetria(sn)
        codigos = sorted(telemetria.valores,
                         key=lambda c: _ORDEM_CANONICA.get(c, 999))
        return [{"code": c,
                 "name": CANAIS_SOLAR[c]["name"],
                 "unit": CANAIS_SOLAR[c]["unit"]}
                for c in codigos if c in CANAIS_SOLAR]

    def ultima_telemetria(self, sn: str) -> Telemetria:
        if self._e_comum():
            # A família End User não tem getLastTsData: a última leitura é o
            # ponto mais recente de uma janela curta do getInverterData. As
            # bordas starttime/endtime são em hora LOCAL da planta
            # (verificado ao vivo: a mesma janela pedida em UTC volta vazia).
            fim = datetime.now()
            inicio = fim - timedelta(minutes=JANELA_ULTIMA_LEITURA_MIN)
            mais_recente = None
            for t in self._historico_comum(sn, inicio, fim):
                if mais_recente is None or t.tmstp_ms > mais_recente.tmstp_ms:
                    mais_recente = t
            if mais_recente is None:
                return Telemetria(tmstp_ms=0, valores={}, online=False)
            return mais_recente

        corpo = self._get("/pro/getLastTsDataPro",
                          {"token": self._credencial("token"), "isnos": sn})
        data = corpo.get("data")
        for registro in data if isinstance(data, list) else []:
            # o exemplo do PDF traz o sn com espaços em volta
            if str(registro.get("sn") or "").strip() == sn:
                return Telemetria(
                    tmstp_ms=_epoch_utc_real(int(registro.get("tmstp") or 0)),
                    valores=_converter(registro),
                    online=True,
                )
        return Telemetria(tmstp_ms=0, valores={}, online=False)

    def historico(self, sn: str, inicio_utc: str,
                  fim_utc: str) -> Iterator[Telemetria]:
        # Cada ponto carrega o próprio tmstp (epoch), então a folga de fuso
        # nas bordas do período pedido é irrelevante para o backfill.
        formato = "%Y-%m-%d %H:%M:%S"
        if self._e_comum():
            # O contrato da interface é UTC; a família End User quer hora
            # local da planta. Converte as bordas com o fuso da máquina e
            # deixa 1 h de margem de cada lado — o dedupe por tmstp no
            # coletor absorve a sobreposição.
            fuso_local = datetime.now().astimezone().tzinfo
            def local(texto):
                return (datetime.strptime(texto, formato)
                        .replace(tzinfo=timezone.utc)
                        .astimezone(fuso_local).replace(tzinfo=None))
            yield from self._historico_comum(
                sn,
                local(inicio_utc) - timedelta(hours=1),
                local(fim_utc) + timedelta(hours=1))
            return

        pagina = 1
        while True:
            if pagina > 1:
                time.sleep(PAUSA_ENTRE_CHAMADAS_S)
            corpo = self._get("/pro/getInverterDataPagePro", {
                "token": self._credencial("token"),
                "apikey": self._apikey_planta(),
                "isnos": sn,
                "startDate": inicio_utc,
                "endDate": fim_utc,
                "pageNum": pagina,
                "pageSize": TAMANHO_PAGINA_HISTORICO,
            })
            data = corpo.get("data") or {}
            for grupo in data.get("result") or []:
                for registro in grupo.get("dataList") or []:
                    tmstp = int(registro.get("tmstp") or 0)
                    if not tmstp:
                        continue
                    yield Telemetria(tmstp_ms=_epoch_utc_real(tmstp),
                                     valores=_converter(registro))
            total_paginas = int(data.get("totalPages") or 0)
            if pagina >= total_paginas:
                return
            pagina += 1

    def _historico_comum(self, sn: str, inicio: datetime,
                         fim: datetime) -> Iterator[Telemetria]:
        """
        getInverterData da família End User: sem paginação, e com um limite
        NÃO documentado descoberto ao vivo: janelas de 24 h ou mais voltam
        403 seco (24 h exatas falham; 23h59m passa). Fatias de 12 h ficam
        bem abaixo do limite — 60 chamadas num backfill de 30 dias, com o
        espaçamento de _TRAVA, são ~2 min por inversor.
        """
        formato = "%Y-%m-%d %H:%M:%S"
        cursor = inicio
        primeira = True
        while cursor < fim:
            if not primeira:
                time.sleep(PAUSA_ENTRE_CHAMADAS_S)
            primeira = False
            fatia_fim = min(cursor + timedelta(hours=12), fim)
            corpo = self._get("/getInverterData", {
                "apikey": self._apikey_planta(),
                "sn": sn,
                "starttime": cursor.strftime(formato),
                "endtime": fatia_fim.strftime(formato),
            })
            data = corpo.get("data")
            for grupo in data if isinstance(data, list) else []:
                if str(grupo.get("isno") or "").strip() not in ("", sn):
                    continue
                for registro in grupo.get("dataList") or []:
                    tmstp = int(registro.get("tmstp") or 0)
                    if not tmstp:
                        continue
                    yield Telemetria(tmstp_ms=_epoch_utc_real(tmstp),
                                     valores=_converter(registro))
            cursor = fatia_fim

    def _resumo(self, apikey: str) -> ResumoPlanta:
        if self._e_comum():
            # Resposta SEM envelope: o objeto do resumo vem direto.
            corpo = self._get("/getPlantOverview", {"key": apikey})
            data = corpo if "Power" in corpo else (corpo.get("data") or {})
        else:
            corpo = self._get("/pro/getPlantOverviewPro",
                              {"token": self._credencial("token"),
                               "apikey": apikey})
            data = corpo.get("data") or {}

        status = str(data.get("status") or "")
        return ResumoPlanta(
            potencia_w=_valor_com_unidade(data.get("Power"), _FATOR_POTENCIA),
            geracao_hoje_kwh=_valor_com_unidade(data.get("E-Today"),
                                                _FATOR_ENERGIA),
            geracao_mes_kwh=_valor_com_unidade(data.get("E-Month"),
                                               _FATOR_ENERGIA),
            geracao_total_kwh=_valor_com_unidade(data.get("E-Total"),
                                                 _FATOR_ENERGIA),
            ultima_atualizacao=str(data.get("ludt") or ""),
            status=ESTADOS_PLANTA.get(int(status), "")
                   if status.isdigit() else "",
        )

    def resumo_planta(self) -> ResumoPlanta:
        return self._resumo(self._apikey_planta())
