"""
Tipos de dispositivo e catálogo canônico de AÇÕES.

Este arquivo é para comandos o que `app/solar/base.py` é para grandezas: o
vocabulário do sistema, não o do fabricante. A tela nunca vê `switch_1` cru nem
um DP numérico — vê "Interruptor 1", o tipo do controle e a faixa de valores.

De onde vêm as capacidades: do `mapping_json` que já está no banco. Ele é a
especificação que o Tuya Cloud publica sobre AQUELE aparelho (código, tipo,
faixa), então descobrir o que um dispositivo sabe fazer não custa uma chamada de
rede — custa uma tabela de tradução. `app/dps_mapping.get_dp_info` já resolve
DP numérico -> código nomeado; aqui decidimos o que desse conjunto é acionável.

A regra de segurança é de exclusão, não de inclusão: o que não está em `ACOES`
não vira botão, e o que está em `BLOQUEADAS` (ou pertence a uma fechadura) não
vira botão nem que esteja em `ACOES`. Quem valida é o servidor — a tela é só a
consequência.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.dps_mapping import (DPS_POR_CATEGORIA, get_dp_info,
                             mapping_do_device)
from app.errors import ValidationError
from app.escala import escalar_faixa, reverter_valor

# --------------------------------------------------------------------------
# Tipos: o que o aparelho É, em português, com o ícone que o representa.
# --------------------------------------------------------------------------
TIPOS: Dict[str, dict] = {
    "interruptor":    {"rotulo": "Interruptor",   "icone": "interruptor"},
    "tomada":         {"rotulo": "Tomada",        "icone": "tomada"},
    "luz":            {"rotulo": "Luz",           "icone": "luz"},
    "camera":         {"rotulo": "Câmera",        "icone": "camera"},
    "sensor":         {"rotulo": "Sensor",        "icone": "sensor"},
    "medidor":        {"rotulo": "Medidor",       "icone": "medidor"},
    "infravermelho":  {"rotulo": "Infravermelho", "icone": "infravermelho"},
    "fechadura":      {"rotulo": "Fechadura",     "icone": "fechadura"},
    "robo":           {"rotulo": "Robô",          "icone": "robo"},
    "hub":            {"rotulo": "Hub",           "icone": "hub"},
    "inversor":       {"rotulo": "Inversor solar", "icone": "solar"},
    "desconhecido":   {"rotulo": "Dispositivo",   "icone": "generico"},
}

TIPO_POR_CATEGORIA: Dict[str, str] = {
    # Interruptores de parede e relés
    "tdq": "interruptor", "kg": "interruptor", "tgkg": "interruptor",
    "qjdcz": "interruptor",
    # Tomadas e filtros de linha
    "cz": "tomada", "pc": "tomada", "wkcz": "tomada",
    # Iluminação
    "dd": "luz", "dj": "luz", "fwd": "luz", "xdd": "luz", "tgq": "luz",
    # Câmeras
    "sp": "camera", "videohd": "camera",
    # Sensores
    "wsdcg": "sensor", "ldcg": "sensor", "mcs": "sensor", "pir": "sensor",
    "sj": "sensor", "rqbj": "sensor", "ywbj": "sensor",
    # Medidores dedicados
    "zndb": "medidor", "aqcz": "medidor",
    # Fechaduras
    "ms": "fechadura", "jtmspro": "fechadura", "bxx": "fechadura",
    # Robôs
    "sd": "robo",
    # Hubs / gateways / controles IR
    "wg2": "hub", "wnykq": "hub", "wxkg": "hub",
    # Energia solar (nosso, não do Tuya)
    "solar": "inversor",
}

# --------------------------------------------------------------------------
# Ações: o catálogo canônico, chaveado pelo código NOMEADO do Tuya.
#
# classe:
#   atuacao   - liga, desliga, dispara. É o que dói se acionado por engano.
#   ajuste    - muda uma configuração do aparelho (modo, sensibilidade, volume).
#   movimento - move fisicamente a câmera; não tem "estado" para exibir.
# --------------------------------------------------------------------------
# `tipo` é o controle que o catálogo espera; quando o mapping do aparelho
# declara o dele, o do aparelho vence (é ele que traz a faixa real).
ACOES: Dict[str, dict] = {
    # ---- interruptores, tomadas, luzes -----------------------------------
    "switch":     {"nome": "Interruptor", "tipo": "boolean", "classe": "atuacao"},
    "switch_1":   {"nome": "Interruptor 1", "tipo": "boolean", "classe": "atuacao"},
    "switch_2":   {"nome": "Interruptor 2", "tipo": "boolean", "classe": "atuacao"},
    "switch_3":   {"nome": "Interruptor 3", "tipo": "boolean", "classe": "atuacao"},
    "switch_4":   {"nome": "Interruptor 4", "tipo": "boolean", "classe": "atuacao"},
    "switch_5":   {"nome": "Interruptor 5", "tipo": "boolean", "classe": "atuacao"},
    "switch_6":   {"nome": "Interruptor 6", "tipo": "boolean", "classe": "atuacao"},
    "switch_led": {"nome": "Luz", "tipo": "boolean", "classe": "atuacao"},
    "bright_value": {"nome": "Brilho", "tipo": "integer", "classe": "ajuste"},
    "temp_value":   {"nome": "Temperatura da luz", "tipo": "integer", "classe": "ajuste"},
    "work_mode":    {"nome": "Modo da luz", "tipo": "enum", "classe": "ajuste"},
    "countdown_1":  {"nome": "Temporizador 1", "tipo": "integer", "classe": "ajuste"},
    "countdown_2":  {"nome": "Temporizador 2", "tipo": "integer", "classe": "ajuste"},
    "countdown_3":  {"nome": "Temporizador 3", "tipo": "integer", "classe": "ajuste"},
    "countdown_4":  {"nome": "Temporizador 4", "tipo": "integer", "classe": "ajuste"},
    "relay_status": {"nome": "Estado após queda de energia", "tipo": "enum",
                     "classe": "ajuste",
                     "rotulos": {"0": "Desligado", "1": "Ligado",
                                 "2": "Último estado", "off": "Desligado",
                                 "on": "Ligado", "memory": "Último estado"}},
    "child_lock":   {"nome": "Trava de segurança", "tipo": "boolean", "classe": "ajuste"},

    # ---- câmeras ---------------------------------------------------------
    "ptz_control": {"nome": "Mover câmera", "tipo": "enum", "classe": "movimento",
                    "rotulos": {"0": "Cima", "1": "Cima/direita",
                                "2": "Direita", "3": "Baixo/direita",
                                "4": "Baixo", "5": "Baixo/esquerda",
                                "6": "Esquerda", "7": "Cima/esquerda"}},
    "ptz_stop":          {"nome": "Parar movimento", "tipo": "boolean",
                          "classe": "movimento"},
    "siren_switch":      {"nome": "Sirene", "tipo": "boolean", "classe": "atuacao"},
    "floodlight_switch": {"nome": "Holofote", "tipo": "boolean", "classe": "atuacao"},
    "basic_private":     {"nome": "Modo privacidade", "tipo": "boolean",
                          "classe": "atuacao"},
    "record_switch":     {"nome": "Gravação", "tipo": "boolean", "classe": "atuacao"},
    "nightvision_mode":  {"nome": "Visão noturna", "tipo": "enum", "classe": "ajuste",
                          "rotulos": {"auto": "Automática",
                                      "ir_mode": "Infravermelho",
                                      "color_mode": "Colorida"}},
    "record_mode":       {"nome": "Modo de gravação", "tipo": "enum", "classe": "ajuste",
                          "rotulos": {"1": "Só com evento", "2": "Contínua"}},
    "motion_switch":     {"nome": "Detecção de movimento", "tipo": "boolean",
                          "classe": "ajuste"},
    "motion_tracking":   {"nome": "Seguir movimento", "tipo": "boolean",
                          "classe": "ajuste"},
    "motion_sensitivity": {"nome": "Sensibilidade de movimento", "tipo": "enum",
                           "classe": "ajuste",
                           "rotulos": {"0": "Baixa", "1": "Média",
                                       "2": "Alta"}},
    "decibel_switch":    {"nome": "Detecção de som", "tipo": "boolean",
                          "classe": "ajuste"},
    "basic_flip":        {"nome": "Imagem invertida", "tipo": "boolean",
                          "classe": "ajuste"},
    "basic_osd":         {"nome": "Marca d'água", "tipo": "boolean", "classe": "ajuste"},
    "basic_indicator":   {"nome": "LED indicador", "tipo": "boolean", "classe": "ajuste"},
    "basic_device_volume": {"nome": "Volume", "tipo": "integer", "classe": "ajuste"},
    "basic_anti_flicker": {"nome": "Anticintilância", "tipo": "enum", "classe": "ajuste",
                           "rotulos": {"0": "Desligada", "1": "50 Hz",
                                       "2": "60 Hz"}},

    # ---- robôs aspiradores ----------------------------------------------
    "power":     {"nome": "Ligar", "tipo": "boolean", "classe": "atuacao"},
    "power_go":  {"nome": "Limpar", "tipo": "boolean", "classe": "atuacao"},
    "seek":      {"nome": "Localizar (bipe)", "tipo": "boolean", "classe": "atuacao"},
    "break_clean": {"nome": "Pausar limpeza", "tipo": "boolean", "classe": "atuacao"},
    "dust_collection": {"nome": "Coletar pó", "tipo": "boolean", "classe": "atuacao"},
    "mode":      {"nome": "Modo", "tipo": "enum", "classe": "ajuste",
                  "rotulos": {"standby": "Parado", "smart": "Inteligente",
                              "random": "Aleatório", "mop": "Passar pano",
                              "wall_follow": "Seguir parede",
                              "spiral": "Espiral", "chargego": "Voltar à base",
                              "auto": "Automático"}},
    "suction":   {"nome": "Sucção", "tipo": "enum", "classe": "ajuste"},

    # ---- controles infravermelho (hub IR) --------------------------------
    # Aqui a chave do mapping JÁ é o código; não há DP numérico.
    "temp": {"nome": "Temperatura", "tipo": "enum", "classe": "ajuste"},
    "wind": {"nome": "Ventilação", "tipo": "enum", "classe": "ajuste"},
}

# Nunca vira ação, nem que apareça no mapping. Destrancar porta, formatar
# cartão, reiniciar e apagar mapa não são coisas que um painel de leitura
# oferece num botão.
BLOQUEADAS = {
    "unlock_fingerprint", "unlock_password", "unlock_temporary",
    "unlock_dynamic", "unlock_card", "unlock_app", "unlock_request",
    "reply_unlock_request", "unlock_face", "unlock_key", "manual_lock",
    "alarm_lock", "hijack", "doorbell",
    "sd_format", "device_restart", "reset_map", "factory_reset",
    "remote_add", "remote_list", "request_map", "command_trans",
}

# Tipos cujo aparelho inteiro fica fora do acionamento, aconteça o que
# acontecer com o mapping.
TIPOS_SEM_ACAO = {"fechadura"}

# Tipos do mapping que viram controle na tela. String/Raw/Bitmap ficam de fora:
# não há widget honesto para eles e o risco de escrever lixo no aparelho é real.
_TIPOS_CONTROLAVEIS = {"boolean", "integer", "enum", "value"}


@dataclass
class Acao:
    """
    Uma capacidade acionável de um dispositivo, já em vocabulário nosso.

    `valores` (min/max/step) vem em unidade REAL, igual ao que a leitura
    mostra — quem converte para o inteiro cru do aparelho é `validar_valor`,
    na última barreira antes do pacote sair. Uma faixa crua ao lado de um
    valor escalado faria o campo nascer fora dos próprios limites.
    """
    dp: str                       # a chave que vai no comando (DP ou código IR)
    code: str                     # código canônico ("switch_1")
    nome: str                     # rótulo em português
    tipo: str                     # boolean | integer | enum
    classe: str                   # atuacao | ajuste | movimento
    valores: Dict[str, Any] = field(default_factory=dict)
    opcoes: List[dict] = field(default_factory=list)   # [{valor, rotulo}]
    escala: int = 0               # expoente do `scale` do Tuya (0 = sem escala)
    unidade: str = ""

    def to_dict(self) -> dict:
        return {"dp": self.dp, "code": self.code, "nome": self.nome,
                "tipo": self.tipo, "classe": self.classe,
                "valores": self.valores, "opcoes": self.opcoes,
                "escala": self.escala, "unidade": self.unidade}


def _mapping(device) -> dict:
    """
    A especificação de DPs do aparelho.

    Delega para `dps_mapping.mapping_do_device`, que resolve o `mapping_json`
    do Tuya e, quando ele vem vazio, o perfil por modelo de `app/modelos.py` —
    assim um aparelho que a nuvem não descreve ganha os mesmos controles que
    qualquer outro.
    """
    return mapping_do_device(device)


def _tipo_do_mapping(entrada: dict, code: str) -> str:
    """
    'Boolean'/'BOOLEAN'/'Integer'... -> nosso tipo, minúsculo.

    Sem tipo declarado (mapping derivado da categoria), vale o que o catálogo
    espera daquele código.
    """
    tipo = str((entrada or {}).get("type") or "").strip().lower()
    if tipo == "value":
        return "integer"
    return tipo or (ACOES.get(code) or {}).get("tipo") or ""


def _mapping_da_categoria(device) -> dict:
    """
    Mapping de emergência, montado da tabela por categoria.

    Um aparelho achado só no broadcast da LAN não traz a especificação do
    Tuya Cloud — sem isto ele ficaria sem nenhum botão, mesmo sendo um
    interruptor óbvio. A tabela de `dps_mapping` já sabe que o DP 1 de um
    `tdq` é `switch_1`; aqui isso vira ação, sem faixa de valores (que só o
    mapping de verdade tem).
    """
    categoria = str((device or {}).get("category") or "").strip().lower()
    por_categoria = DPS_POR_CATEGORIA.get(categoria) or {}
    return {dp: {"code": code} for dp, code in por_categoria.items()}


def classificar(device) -> str:
    """
    Tipo derivado do aparelho. A categoria Tuya decide na maioria dos casos; sem
    categoria conhecida, quem decide são os códigos que ele publica.
    """
    categoria = str((device or {}).get("category") or "").strip().lower()
    if categoria in TIPO_POR_CATEGORIA:
        return TIPO_POR_CATEGORIA[categoria]
    if categoria.startswith("infrared_"):
        return "infravermelho"

    codigos = {str(e.get("code") or "")
               for e in _mapping(device).values() if isinstance(e, dict)}
    if any(c.startswith("unlock_") for c in codigos):
        return "fechadura"
    if "ptz_control" in codigos or "basic_private" in codigos:
        return "camera"
    if any(c.startswith("switch") for c in codigos):
        return "interruptor"
    if "cur_power" in codigos or "add_ele" in codigos:
        return "medidor"
    return "desconhecido"


def tipo_do_dispositivo(device) -> str:
    """O tipo que vale: o que o usuário fixou à mão, senão o derivado."""
    manual = (device or {}).get("tipo_manual")
    if manual and manual in TIPOS:
        return manual
    return classificar(device)


def rotulo_tipo(tipo: str) -> str:
    return TIPOS.get(tipo, TIPOS["desconhecido"])["rotulo"]


def _opcoes(code: str, valores: dict) -> List[dict]:
    """Valores de um enum com rótulo amigável quando temos um."""
    rotulos = (ACOES.get(code) or {}).get("rotulos") or {}
    faixa = valores.get("range") or []
    return [{"valor": str(v), "rotulo": rotulos.get(str(v), str(v))}
            for v in faixa]


def acoes_do_dispositivo(device) -> List[Acao]:
    """
    O que este aparelho aceita como comando — derivado do mapping_json dele.

    Nenhuma chamada de rede: a especificação já está no banco. Um DP que não
    resolve para código canônico conhecido, ou cujo tipo não vira controle,
    simplesmente não aparece.
    """
    tipo_aparelho = tipo_do_dispositivo(device)
    if tipo_aparelho in TIPOS_SEM_ACAO:
        return []

    # A descoberta acontece AQUI, na leitura — não num momento de cadastro.
    # Reimportou o aparelho e ele passou a declarar um DP novo? O botão
    # aparece sozinho na próxima abertura da tela.
    mapping = _mapping(device) or _mapping_da_categoria(device)
    if not mapping:
        return []

    categoria = (device or {}).get("category")
    saida: List[Acao] = []
    for dp, entrada in mapping.items():
        if not isinstance(entrada, dict):
            continue
        info = get_dp_info(dp, categoria, mapping)
        code = info.get("code") or str(dp)
        if code in BLOQUEADAS or code not in ACOES:
            continue

        tipo = _tipo_do_mapping(entrada, code)
        if tipo not in _TIPOS_CONTROLAVEIS:
            continue
        if tipo == "value":
            tipo = "integer"

        escala = int(info.get("scale") or 0)
        valores = escalar_faixa(entrada.get("values"), escala)
        # Infravermelho declara a faixa como min/max sem 'range'; vira enum
        # numérico para a tela ter o que mostrar.
        if tipo == "enum" and "range" not in valores and "min" in valores:
            valores["range"] = [str(v) for v in
                                range(int(valores["min"]),
                                      int(valores["max"]) + 1)]

        catalogo = ACOES[code]
        saida.append(Acao(
            dp=str(dp),
            code=code,
            nome=catalogo["nome"],
            tipo=tipo,
            classe=catalogo["classe"],
            valores=valores,
            opcoes=_opcoes(code, valores) if tipo == "enum" else [],
            escala=escala,
            unidade=info.get("unit") or "",
        ))

    ordem = {"atuacao": 0, "movimento": 1, "ajuste": 2}
    saida.sort(key=lambda a: (ordem.get(a.classe, 9),
                             (not a.dp.isdigit(), a.dp.zfill(4))))
    return saida


def acao_por_dp(device, dp: str) -> Optional[Acao]:
    """A ação correspondente a um DP, ou None se este aparelho não a oferece."""
    dp = str(dp)
    for acao in acoes_do_dispositivo(device):
        if acao.dp == dp:
            return acao
    return None


def validar_valor(acao: Acao, valor: Any) -> Any:
    """
    Converte e valida o valor recebido do cliente para o que o aparelho espera.

    Levanta ValidationError com mensagem em português — a mesma que a tela
    mostra. É a última barreira antes de um pacote sair para o dispositivo.
    """
    if acao.tipo == "boolean":
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, (int, float)) and valor in (0, 1):
            return bool(valor)
        if isinstance(valor, str) and valor.lower() in ("true", "false",
                                                        "1", "0"):
            return valor.lower() in ("true", "1")
        raise ValidationError("'%s' aceita apenas ligado ou desligado."
                              % acao.nome)

    if acao.tipo == "enum":
        texto = str(valor)
        permitidos = [o["valor"] for o in acao.opcoes]
        if permitidos and texto not in permitidos:
            raise ValidationError("Valor inválido para '%s'. Aceitos: %s."
                                  % (acao.nome, ", ".join(permitidos)))
        return texto

    # integer. Sem escala o valor JÁ é o que o aparelho aceita e tem que ser
    # inteiro; com escala ele chega em unidade real (12,5 V) e vira inteiro
    # só depois de reverter.
    try:
        numero = float(valor) if acao.escala else int(valor)
    except (TypeError, ValueError):
        raise ValidationError("'%s' espera um número%s."
                              % (acao.nome, "" if acao.escala else " inteiro"))

    minimo = acao.valores.get("min")
    maximo = acao.valores.get("max")
    if minimo is not None and numero < float(minimo):
        raise ValidationError("'%s' aceita no mínimo %s." % (acao.nome, minimo))
    if maximo is not None and numero > float(maximo):
        raise ValidationError("'%s' aceita no máximo %s." % (acao.nome, maximo))
    # O aparelho só entende o inteiro deslocado — a conversão de volta é a
    # última coisa que acontece antes do pacote sair.
    return reverter_valor(numero, acao.escala) if acao.escala else numero


def acao_principal(device) -> Optional[Acao]:
    """
    A ação que representa o aparelho num card ou numa linha de tabela: o
    primeiro liga/desliga de atuação. Um interruptor de 3 teclas mostra a
    tecla 1 na listagem e as três na tela dele — a listagem é para reconhecer,
    não para operar tudo.
    """
    for acao in acoes_do_dispositivo(device):
        if acao.classe == "atuacao" and acao.tipo == "boolean":
            return acao
    return None


def valor_de(leitura: Optional[dict], dp: str):
    """O valor de um DP na última leitura, ou None se não veio nela."""
    if not leitura or not leitura.get("dps_json"):
        return None
    try:
        dados = json.loads(leitura["dps_json"])
    except (ValueError, TypeError):
        return None
    return dados.get(str(dp)) if isinstance(dados, dict) else None


def enriquecer_status(statuses: List[dict]) -> List[dict]:
    """
    Acrescenta tipo e capacidades aos dicts de status do repository.

    Fica aqui, e não no repository, de propósito: o repository lê banco, não
    interpreta vocabulário de fabricante. As telas chamam isto e recebem tudo
    já em português.
    """
    for st in statuses:
        device = st.get("device") or {}
        tipo = tipo_do_dispositivo(device)
        principal = acao_principal(device)

        st["tipo"] = tipo
        st["tipo_rotulo"] = rotulo_tipo(tipo)
        st["acionavel"] = bool(device.get("acionavel"))
        st["confirmar_acao"] = bool(device.get("confirmar_acao"))
        st["acao_principal"] = None
        if principal is not None:
            dados = principal.to_dict()
            dados["valor"] = valor_de(st.get("reading"), principal.dp)
            st["acao_principal"] = dados
    return statuses


def enriquecer_grupos(grupos: List[dict]) -> List[dict]:
    """`enriquecer_status` na estrutura aninhada de get_devices_grouped_by_local."""
    for grupo in grupos:
        enriquecer_status(grupo.get("sem_comodo") or [])
        for comodo in grupo.get("comodos") or []:
            enriquecer_status(comodo.get("devices") or [])
    return grupos


def tipos_presentes(statuses: List[dict]) -> List[dict]:
    """
    Os tipos que aparecem nesta lista, com a contagem — alimenta os filtros.
    Só o que existe: um filtro "Fechadura" numa casa sem fechadura é ruído.
    """
    contagem: Dict[str, int] = {}
    for st in statuses:
        tipo = st.get("tipo") or "desconhecido"
        contagem[tipo] = contagem.get(tipo, 0) + 1
    return [{"tipo": t, "rotulo": rotulo_tipo(t), "total": n}
            for t, n in sorted(contagem.items(),
                               key=lambda kv: (-kv[1], kv[0]))]
