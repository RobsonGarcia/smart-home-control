"""
Perfis de DP por modelo — o resgate de quem a nuvem não descreve.

O Tuya publica, para quase todo produto, a especificação dos DPs: código,
tipo, unidade e escala. É de lá que sai o `mapping` do devices.json, e é ele
que `app/dps_mapping` e `app/escala` consomem. Quase todo produto.

O medidor bidirecional PJ1103C (`wifech3utowiyknu`) é um dos que ficam de
fora. Perguntado direto à API com credencial válida, o Tuya responde:

    getdps       -> Err 913, "not support this device"
    getfunctions -> {"category": "cz", "functions": []}

Não é falha do wizard: a especificação não existe do lado deles. O aparelho
reporta 40 DPs numerados de 101 a 150 e o painel não tem como saber que o 112
é tensão. Este módulo é onde esse conhecimento passa a morar.

## Perfil, não modelo

O mesmo desenho de dados costuma se repetir em vários produtos — o fabricante
troca a carcaça e o `product_id`, e o firmware continua o mesmo. Por isso o
conhecimento fica num PERFIL nomeado, e os modelos apontam para ele. Associar
um aparelho novo a um perfil que já existe é uma linha em `PRODUTOS` ou em
`MODELOS`, sem duplicar tabela nenhuma.

## O formato: um mapping igual ao do Tuya

Cada perfil produz exatamente a mesma estrutura do campo `mapping` do
devices.json — `{dp: {"code", "type", "values": {"unit", "scale"}}}`. Não é
capricho: é o que faz `get_dp_info`, `escala.escalas_do_device` e
`capacidades.acoes_do_dispositivo` funcionarem sem uma linha de caso especial.
O aparelho passa a ser tratado como se o Tuya o tivesse descrito.

## A convergência: código canônico, não código do fabricante

Um DP só nomeado ainda é um beco sem saída — "power_a" não compara com nada.
Por isso cada DP converge para o vocabulário que o sistema já fala
(`DPS_NOMEADOS`, em app/dps_mapping.py): a potência do canal A vira
`cur_power_1`, a mesma coisa que uma tomada Tuya de dois canais reporta. É o
que permite pôr o medidor e a tomada na mesma série de um painel de energia.

É o mesmo princípio de `CANAIS_SOLAR` (app/solar/base.py), onde a corrente de
um inversor AiSWEI vira `corrente_mppt_1` e passa a ser comparável com a de
qualquer outro fabricante. Fabricante novo, vocabulário velho.

## As escalas foram MEDIDAS, não chutadas

Escala errada aqui é pior que escala nenhuma: a conversão acontece na coleta
(app/escala.py), então um expoente errado grava número errado no histórico.
As declaradas abaixo foram conferidas por três caminhos independentes:

  1. física — P = V × I × fator de potência fecha nos dois canais com erro
     < 2%, e a soma dos canais bate EXATAMENTE o DP 115 (772,8 W);
  2. coerência interna — 106+108 = 130 e 107+109 = 131, ou seja, os totais
     são a soma dos canais; e a energia integrada da potência ao longo de
     23 min deu 419 Wh contra 390 Wh dos contadores em escala 2, enquanto as
     escalas 1 e 3 errariam por 9x e 10x;
  3. o app do fabricante — mostrando 18,5 W e 363 mA no canal A no mesmo
     instante em que os DPs crus liam 185 e 363.

O que NÃO foi verificado não recebe unidade nem escala: fica com nome e tipo,
e o painel mostra o número cru em vez de afirmar uma grandeza que ninguém
conferiu. É a mesma regra de `dps_mapping`: melhor um rótulo neutro do que um
errado.

## Acrescentar um aparelho

Se ele tem o mesmo desenho de dados de um perfil existente, é uma linha em
`PRODUTOS` (pelo `product_id`, que não se repete) ou em `MODELOS`. Se o
desenho é novo, um perfil novo — e, se ele medir algo que ainda não tem código
canônico, o código entra UMA vez em `DPS_NOMEADOS` e passa a existir para
todos.
"""

from typing import Dict, Optional


def _dp(code: str, tipo: str = "Integer", unit: str = "", scale: int = 0) -> dict:
    """Uma entrada no formato que o Tuya usaria, se ele descrevesse o aparelho."""
    entrada = {"code": code, "type": tipo}
    valores = {}
    if unit:
        valores["unit"] = unit
    if scale:
        valores["scale"] = scale
    entrada["values"] = valores
    return entrada


# --------------------------------------------------------------------------
# Medidor bidirecional de 2 canais.
#
# Canal A e canal B são as duas medições instaladas no quadro; o aparelho
# conta energia nos DOIS sentidos, e o "total" é a soma ALGÉBRICA dos canais,
# não o consumo bruto — o rótulo diz isso, para ninguém somar duas vezes num
# painel de energia.
# --------------------------------------------------------------------------
_MEDIDOR_2CH_BIDIRECIONAL: Dict[str, dict] = {
    # --- medição instantânea (escalas verificadas) ---------------------
    "101": _dp("cur_power_1", unit="W", scale=1),
    "105": _dp("cur_power_2", unit="W", scale=1),
    "115": _dp("cur_power", unit="W", scale=1),
    "112": _dp("cur_voltage", unit="V", scale=1),
    "113": _dp("cur_current_1", unit="mA"),
    "114": _dp("cur_current_2", unit="mA"),
    "110": _dp("power_factor_1", scale=2),
    "121": _dp("power_factor_2", scale=2),
    "111": _dp("frequencia_ca", unit="Hz", scale=2),

    # --- energia acumulada, nos dois sentidos (escala 2 verificada) ----
    "106": _dp("add_ele_1", unit="kWh", scale=2),
    "108": _dp("add_ele_2", unit="kWh", scale=2),
    "107": _dp("add_ele_rev_1", unit="kWh", scale=2),
    "109": _dp("add_ele_rev_2", unit="kWh", scale=2),
    "130": _dp("forward_energy_total", unit="kWh", scale=2),
    "131": _dp("reverse_energy_total", unit="kWh", scale=2),

    # --- sentido do fluxo em cada canal --------------------------------
    "102": _dp("direction_1", tipo="Enum"),
    "104": _dp("direction_2", tipo="Enum"),

    # --- limites de alarme ---------------------------------------------
    # 137/138 usam a mesma escala do DP 112 (tensão), e os valores batem com
    # uma rede 127/220 V: 260,0 V de sobretensão e 85,0 V de subtensão.
    "137": _dp("ov_threshold", unit="V", scale=1),
    "138": _dp("uv_threshold", unit="V", scale=1),
    # 139/140/145/146 chegam como 150000 e 330000. A leitura coerente seria
    # 150 A e 33 kW (220 V x 150 A = 33 kW), mas isso é dedução, não medição —
    # então ficam sem unidade e sem escala até alguém confirmar no aparelho.
    "139": _dp("oc_threshold_1"),
    "140": _dp("op_threshold_1"),
    "145": _dp("oc_threshold_2"),
    "146": _dp("op_threshold_2"),

    # --- calibração e configuração --------------------------------------
    # Chegam todos em 1000, que é o coeficiente neutro. Sem unidade: são
    # fatores internos do aparelho, não grandezas.
    "116": _dp("voltage_coe"),
    "117": _dp("electric_coe"),
    "118": _dp("power_coe"),
    "119": _dp("electricity_coe"),
    "122": _dp("freq_coe"),
    "123": _dp("electric_coe_2"),
    "124": _dp("power_coe_2"),
    "125": _dp("electricity_coe_2"),
    "127": _dp("electricity_rev_coe"),
    "128": _dp("electricity_rev_coe_2"),
    "120": _dp("coef_reset_1", tipo="Boolean"),
    "126": _dp("coef_reset_2", tipo="Boolean"),
    "129": _dp("report_rate", unit="s"),

    # --- chaves de alarme -----------------------------------------------
    "132": _dp("buz_enable", tipo="Boolean"),
    "133": _dp("ov_enable", tipo="Boolean"),
    "134": _dp("uv_enable", tipo="Boolean"),
    "135": _dp("oc_enable_1", tipo="Boolean"),
    "136": _dp("op_enable_1", tipo="Boolean"),
    "149": _dp("oc_enable_2", tipo="Boolean"),
    "150": _dp("op_enable_2", tipo="Boolean"),

    # --- estado dos alarmes ---------------------------------------------
    # Nunca apareceram numa leitura deste aparelho; mapeados para que, se
    # aparecerem, cheguem à tela com nome em vez de "DP 141".
    "141": _dp("ov_status", tipo="Boolean"),
    "142": _dp("uv_status", tipo="Boolean"),
    "143": _dp("oc_status_1", tipo="Boolean"),
    "144": _dp("op_status_1", tipo="Boolean"),
    "147": _dp("oc_status_2", tipo="Boolean"),
    "148": _dp("op_status_2", tipo="Boolean"),
}


PERFIS: Dict[str, dict] = {
    "medidor-2ch-bidirecional": {
        "rotulo": "Medidor bidirecional de 2 canais",
        "dps": _MEDIDOR_2CH_BIDIRECIONAL,
    },
}

# --------------------------------------------------------------------------
# Quem usa cada perfil. Vários produtos podem apontar para o mesmo: é o caso
# do fabricante que troca a carcaça e o product_id mantendo o firmware.
#
# PRODUTOS tem precedência: `product_id` é o identificador do Tuya para o
# produto e não se repete. MODELOS é a rede de segurança para aparelhos
# importados antes de `product_id` passar a ser gravado, e para quando o
# mesmo modelo aparece com product_id diferente entre regiões.
# --------------------------------------------------------------------------
PRODUTOS: Dict[str, str] = {
    "wifech3utowiyknu": "medidor-2ch-bidirecional",   # PJ1103C
}

MODELOS: Dict[str, str] = {
    "PJ1103C": "medidor-2ch-bidirecional",
}


def perfil_do_produto(product_id: str = None,
                      model: str = None) -> Optional[str]:
    """O nome do perfil que descreve este aparelho, ou None."""
    if product_id:
        perfil = PRODUTOS.get(str(product_id).strip())
        if perfil:
            return perfil
    if model:
        return MODELOS.get(str(model).strip())
    return None


def mapping_do_produto(product_id: str = None,
                       model: str = None) -> Optional[Dict[str, dict]]:
    """
    O mapping conhecido para este aparelho, ou None.

    Mesmo formato do `mapping` do devices.json, de propósito: quem consome não
    precisa saber que a especificação veio daqui em vez de vir do Tuya.
    """
    perfil = perfil_do_produto(product_id, model)
    return PERFIS[perfil]["dps"] if perfil else None
