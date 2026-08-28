"""
Nomes amigáveis para DPs (Data Points) Tuya.

Por que não é um dicionário único: os DPs NUMÉRICOS (1, 2, 3…) significam coisas
diferentes conforme a categoria do aparelho — num interruptor o DP 1 é
"Interruptor 1", num sensor é "Temperatura". Um mapa global só chuta, e chuta
errado para metade dos aparelhos.

A ordem de resolução é sempre a mesma, da fonte mais confiável para a menos:

  1. mapping_json do próprio dispositivo (vem do Tuya Cloud, é autoritativo)
  2. código NOMEADO (cur_power, switch_1…), que não é ambíguo entre categorias
  3. tabela por categoria, para o DP numérico
  4. desiste e devolve "DP 3" — melhor um rótulo neutro do que um errado
"""

import json
from typing import Dict, Optional

from app.solar.base import CANAIS_SOLAR

# --------------------------------------------------------------------------
# Códigos nomeados do Tuya. Estes NÃO são ambíguos: 'cur_power' é potência em
# qualquer categoria. É a tabela de referência de verdade.
# --------------------------------------------------------------------------
DPS_NOMEADOS: Dict[str, dict] = {
    # Interruptores / relés
    "switch": {"name": "Interruptor", "unit": "on/off", "type": "boolean"},
    "switch_1": {"name": "Interruptor 1", "unit": "on/off", "type": "boolean"},
    "switch_2": {"name": "Interruptor 2", "unit": "on/off", "type": "boolean"},
    "switch_3": {"name": "Interruptor 3", "unit": "on/off", "type": "boolean"},
    "switch_4": {"name": "Interruptor 4", "unit": "on/off", "type": "boolean"},
    "switch_5": {"name": "Interruptor 5", "unit": "on/off", "type": "boolean"},
    "switch_6": {"name": "Interruptor 6", "unit": "on/off", "type": "boolean"},
    "switch_led": {"name": "Luz", "unit": "on/off", "type": "boolean"},

    # Energia — acumulada
    "add_ele": {"name": "Energia acumulada", "unit": "kWh", "type": "numeric"},
    "add_ele_1": {"name": "Energia acumulada — canal 1", "unit": "kWh", "type": "numeric"},
    "add_ele_2": {"name": "Energia acumulada — canal 2", "unit": "kWh", "type": "numeric"},
    "add_ele_3": {"name": "Energia acumulada — canal 3", "unit": "kWh", "type": "numeric"},
    "forward_energy_total": {"name": "Energia total — sentido direto", "unit": "kWh", "type": "numeric"},

    # Energia no sentido REVERSO. Um medidor bidirecional conta os dois lados
    # separadamente: sem estes códigos, a energia que volta para a rede seria
    # somada à consumida, que é o oposto do que ela significa.
    "add_ele_rev_1": {"name": "Energia reversa — canal 1", "unit": "kWh", "type": "numeric"},
    "add_ele_rev_2": {"name": "Energia reversa — canal 2", "unit": "kWh", "type": "numeric"},
    "add_ele_rev_3": {"name": "Energia reversa — canal 3", "unit": "kWh", "type": "numeric"},
    "reverse_energy_total": {"name": "Energia total — sentido reverso", "unit": "kWh", "type": "numeric"},

    # Energia — instantânea
    "cur_power": {"name": "Potência", "unit": "W", "type": "numeric"},
    "cur_power_1": {"name": "Potência — canal 1", "unit": "W", "type": "numeric"},
    "cur_power_2": {"name": "Potência — canal 2", "unit": "W", "type": "numeric"},
    "cur_power_3": {"name": "Potência — canal 3", "unit": "W", "type": "numeric"},
    "cur_current": {"name": "Corrente", "unit": "mA", "type": "numeric"},
    "cur_current_1": {"name": "Corrente — canal 1", "unit": "mA", "type": "numeric"},
    "cur_current_2": {"name": "Corrente — canal 2", "unit": "mA", "type": "numeric"},
    "cur_current_3": {"name": "Corrente — canal 3", "unit": "mA", "type": "numeric"},
    "cur_voltage": {"name": "Voltagem", "unit": "V", "type": "numeric"},
    "cur_voltage_1": {"name": "Voltagem — canal 1", "unit": "V", "type": "numeric"},
    "cur_voltage_2": {"name": "Voltagem — canal 2", "unit": "V", "type": "numeric"},
    "cur_voltage_3": {"name": "Voltagem — canal 3", "unit": "V", "type": "numeric"},

    # Fator de potência: adimensional de propósito — é uma razão, e escrever
    # uma unidade ao lado dela seria inventar grandeza.
    "power_factor": {"name": "Fator de potência", "unit": "", "type": "numeric"},
    "power_factor_1": {"name": "Fator de potência — canal 1", "unit": "", "type": "numeric"},
    "power_factor_2": {"name": "Fator de potência — canal 2", "unit": "", "type": "numeric"},
    "power_factor_3": {"name": "Fator de potência — canal 3", "unit": "", "type": "numeric"},

    # Sentido do fluxo, num medidor bidirecional.
    "direction_1": {"name": "Sentido — canal 1", "unit": "sentido", "type": "enum"},
    "direction_2": {"name": "Sentido — canal 2", "unit": "sentido", "type": "enum"},

    # Limites e chaves de alarme de um medidor. Os de tensão têm unidade
    # verificada; os de corrente e potência ficam sem, porque a escala deles
    # não foi conferida no aparelho (ver app/modelos.py).
    "ov_threshold": {"name": "Limite de sobretensão", "unit": "V", "type": "numeric"},
    "uv_threshold": {"name": "Limite de subtensão", "unit": "V", "type": "numeric"},
    "oc_threshold_1": {"name": "Limite de sobrecorrente — canal 1", "unit": "", "type": "numeric"},
    "oc_threshold_2": {"name": "Limite de sobrecorrente — canal 2", "unit": "", "type": "numeric"},
    "op_threshold_1": {"name": "Limite de sobrepotência — canal 1", "unit": "", "type": "numeric"},
    "op_threshold_2": {"name": "Limite de sobrepotência — canal 2", "unit": "", "type": "numeric"},
    "report_rate": {"name": "Intervalo de envio", "unit": "s", "type": "numeric"},
    "buz_enable": {"name": "Aviso sonoro", "unit": "on/off", "type": "boolean"},
    "ov_enable": {"name": "Alarme de sobretensão", "unit": "on/off", "type": "boolean"},
    "uv_enable": {"name": "Alarme de subtensão", "unit": "on/off", "type": "boolean"},
    "oc_enable_1": {"name": "Alarme de sobrecorrente — canal 1", "unit": "on/off", "type": "boolean"},
    "oc_enable_2": {"name": "Alarme de sobrecorrente — canal 2", "unit": "on/off", "type": "boolean"},
    "op_enable_1": {"name": "Alarme de sobrepotência — canal 1", "unit": "on/off", "type": "boolean"},
    "op_enable_2": {"name": "Alarme de sobrepotência — canal 2", "unit": "on/off", "type": "boolean"},
    "ov_status": {"name": "Sobretensão detectada", "unit": "on/off", "type": "boolean"},
    "uv_status": {"name": "Subtensão detectada", "unit": "on/off", "type": "boolean"},
    "oc_status_1": {"name": "Sobrecorrente — canal 1", "unit": "on/off", "type": "boolean"},
    "oc_status_2": {"name": "Sobrecorrente — canal 2", "unit": "on/off", "type": "boolean"},
    "op_status_1": {"name": "Sobrepotência — canal 1", "unit": "on/off", "type": "boolean"},
    "op_status_2": {"name": "Sobrepotência — canal 2", "unit": "on/off", "type": "boolean"},

    # Sensores
    "va_temperature": {"name": "Temperatura", "unit": "°C", "type": "numeric"},
    "temp_current": {"name": "Temperatura", "unit": "°C", "type": "numeric"},
    "va_humidity": {"name": "Umidade", "unit": "%", "type": "numeric"},
    "humidity_value": {"name": "Umidade", "unit": "%", "type": "numeric"},
    "bright_value": {"name": "Luminosidade", "unit": "lux", "type": "numeric"},
    "battery_percentage": {"name": "Bateria", "unit": "%", "type": "numeric"},
    "doorcontact_state": {"name": "Sensor de abertura", "unit": "aberto/fechado", "type": "boolean"},
    "pir": {"name": "Movimento", "unit": "estado", "type": "enum"},
    "watersensor_state": {"name": "Sensor de água", "unit": "estado", "type": "enum"},

    # Câmeras
    "basic_indicator": {"name": "LED indicador", "unit": "on/off", "type": "boolean"},
    "basic_flip": {"name": "Imagem invertida", "unit": "on/off", "type": "boolean"},
    "basic_osd": {"name": "Marca d'água", "unit": "on/off", "type": "boolean"},
    "basic_private": {"name": "Modo privacidade", "unit": "on/off", "type": "boolean"},
    "motion_sensitivity": {"name": "Sensibilidade de movimento", "unit": "nível", "type": "enum"},
    "record_switch": {"name": "Gravação", "unit": "on/off", "type": "boolean"},
    "record_mode": {"name": "Modo de gravação", "unit": "modo", "type": "enum"},

    # Diversos
    "countdown_1": {"name": "Temporizador 1", "unit": "s", "type": "numeric"},
    "countdown_2": {"name": "Temporizador 2", "unit": "s", "type": "numeric"},
    "countdown_3": {"name": "Temporizador 3", "unit": "s", "type": "numeric"},
    "countdown_4": {"name": "Temporizador 4", "unit": "s", "type": "numeric"},
    "relay_status": {"name": "Estado após queda de energia", "unit": "modo", "type": "enum"},
    "child_lock": {"name": "Trava de segurança", "unit": "on/off", "type": "boolean"},
}

# --------------------------------------------------------------------------
# DP numérico -> código nomeado, POR CATEGORIA Tuya.
#
# É aqui que morava o bug: as chaves "1" e "2" apareciam duas vezes no
# dicionário global (interruptor e depois sensor), e como literal Python a
# última vencia — todo DP 1 virava "Temperatura" e nenhum interruptor era
# rotulado corretamente. Separado por categoria, cada um resolve certo.
# Canais solares canonicos (app/solar/base.py). A tabela e definida la --
# o vocabulario pertence a abstracao solar -- e absorvida aqui para que
# get_dp_info/get_device_dps_list resolvam nome e unidade sem caso especial.
DPS_NOMEADOS.update(CANAIS_SOLAR)

# --------------------------------------------------------------------------
DPS_POR_CATEGORIA: Dict[str, Dict[str, str]] = {
    # Tomadas e filtros de linha
    "cz": {"1": "switch_1", "9": "countdown_1", "14": "relay_status"},
    "pc": {"1": "switch_1", "2": "switch_2", "3": "switch_3",
           "4": "switch_4", "5": "switch_5", "6": "switch_6"},
    "wkcz": {"1": "switch_1", "2": "switch_2"},

    # Interruptores de parede
    "kg": {"1": "switch_1", "2": "switch_2", "3": "switch_3",
           "4": "switch_4", "5": "switch_5", "6": "switch_6"},
    "tgkg": {"1": "switch_1", "2": "switch_2", "3": "switch_3"},
    # tdq é a categoria dos relés/interruptores Aubess e afins — a mesma
    # numeração de kg, com os temporizadores e o estado pós-queda por canal.
    "tdq": {"1": "switch_1", "2": "switch_2", "3": "switch_3",
            "4": "switch_4", "9": "countdown_1", "10": "countdown_2",
            "11": "countdown_3", "38": "relay_status"},

    # Iluminação
    "dj": {"1": "switch_led", "3": "bright_value"},
    "dd": {"1": "switch_led", "3": "bright_value"},
    "fwd": {"1": "switch_led", "3": "bright_value"},

    # Sensores
    "wsdcg": {"1": "va_temperature", "2": "va_humidity", "4": "battery_percentage"},
    "ldcg": {"1": "bright_value", "4": "battery_percentage"},
    "mcs": {"1": "doorcontact_state", "2": "battery_percentage"},
    "pir": {"1": "pir", "4": "battery_percentage"},
    "sj": {"1": "watersensor_state", "4": "battery_percentage"},

    # Câmeras
    "sp": {"101": "basic_indicator", "103": "basic_flip", "104": "basic_osd",
           "105": "basic_private", "106": "motion_sensitivity",
           "150": "record_switch", "151": "record_mode"},
}

# Os DPs de energia 17–20 são estáveis em toda categoria que mede consumo, e
# não colidem com os DPs baixos de interruptor/sensor. Servem de complemento
# para qualquer categoria.
DPS_ENERGIA_NUMERICOS: Dict[str, str] = {
    "17": "add_ele",
    "18": "cur_current",
    "19": "cur_power",
    "20": "cur_voltage",
}

DESCONHECIDO = {"name": None, "unit": "valor", "type": "unknown"}

# --------------------------------------------------------------------------
# O que NÃO vira curva no gráfico de histórico.
#
# A pergunta que separa não é "de que tipo é o aparelho" — é "isto é uma
# grandeza medida, que muda sozinha ao longo do tempo?". A mesma tomada tem
# potência (medição) e coeficiente de calibração (ajuste), e o mesmo medidor
# tem tensão e limite de sobretensão. Os dois convivem em qualquer categoria.
#
# Por que importa: um limite de sobrepotência em 330000 e um coeficiente em
# 26657 dividem o eixo Y com uma potência de 18 W, e achatam a curva que
# alguém foi ali ver. Nada some da tela — os DPs de ajuste continuam inteiros
# no cartão "O que veio na última coleta"; eles só não ganham linha.
#
# A maioria dos ajustes já cai fora sozinha por não ser `numeric` (todo
# `*_coe` resolve para tipo desconhecido, e chave e enum não são número).
# Esta lista é para os que SÃO numéricos e ainda assim são configuração.
# --------------------------------------------------------------------------
DPS_DE_AJUSTE = {
    "countdown_1", "countdown_2", "countdown_3", "countdown_4",
    "report_rate",
    "ov_threshold", "uv_threshold",
    "oc_threshold_1", "oc_threshold_2",
    "op_threshold_1", "op_threshold_2",
}


def plotavel(info: dict) -> bool:
    """Se este DP merece uma linha no gráfico de histórico."""
    return (info.get("type") == "numeric"
            and info.get("code") not in DPS_DE_AJUSTE)


def _codigo_nomeado(dps_code: str, category: Optional[str],
                    mapping: Optional[dict]) -> Optional[str]:
    """Resolve um DP para o código nomeado do Tuya, ou None."""
    # 1. mapping_json do dispositivo: o que o Tuya Cloud diz sobre ESTE aparelho.
    if mapping:
        entrada = mapping.get(dps_code)
        if isinstance(entrada, dict) and entrada.get("code"):
            return entrada["code"]

    # 2. já é um código nomeado.
    if dps_code in DPS_NOMEADOS:
        return dps_code

    # 3. numérico + categoria conhecida.
    if category:
        por_cat = DPS_POR_CATEGORIA.get(category.lower())
        if por_cat and dps_code in por_cat:
            return por_cat[dps_code]

    # 4. numérico de energia, estável entre categorias.
    return DPS_ENERGIA_NUMERICOS.get(dps_code)


def mapping_do_device(device) -> dict:
    """
    A especificação de DPs de um aparelho — a do Tuya, ou a do perfil dele.

    Ponto único porque antes eram cinco: o mesmo `json.loads` com o mesmo
    try/except aparecia em capacidades, escala e nas duas rotas, e cada um
    teria que aprender sozinho sobre perfis.

    A ordem é a de sempre, da fonte mais confiável para a menos: o
    `mapping_json` que veio do Tuya manda; quando ele vem vazio — porque a
    nuvem não descreve aquele produto — vale o perfil de `app/modelos.py`.
    """
    dados = (device or {}).get("mapping_json")
    if dados:
        try:
            mapping = json.loads(dados)
        except (json.JSONDecodeError, TypeError):
            mapping = None
        if isinstance(mapping, dict) and mapping:
            return mapping

    from app.modelos import mapping_do_produto
    return mapping_do_produto((device or {}).get("product_id"),
                              (device or {}).get("model")) or {}


def _spec_do_mapping(dps_code: str, mapping: Optional[dict]) -> dict:
    """
    `unit` e `scale` que o Tuya declara para ESTE DP neste aparelho.

    A unidade da tabela DPS_NOMEADOS é a do código em geral; a do mapping é a
    do aparelho na sua frente. Quando as duas existem, a do aparelho vence —
    mas ela vem vazia com frequência (`add_ele` publica `unit: ""` e mede em
    kWh), e aí a tabela é quem sabe.
    """
    entrada = (mapping or {}).get(dps_code)
    if not isinstance(entrada, dict):
        return {}
    valores = entrada.get("values")
    if not isinstance(valores, dict):
        return {}

    spec = {}
    unidade = str(valores.get("unit") or "").strip()
    if unidade:
        spec["unit"] = unidade
    try:
        escala = int(valores.get("scale") or 0)
    except (TypeError, ValueError):
        escala = 0
    if escala > 0:
        spec["scale"] = escala
    return spec


def get_dp_info(dps_code: str, category: str = None,
                mapping: dict = None) -> dict:
    """
    Informações de um DP. Passe `category` e/ou o `mapping` do dispositivo
    sempre que tiver — sem eles, um DP numérico baixo é ambíguo e a função
    devolve um rótulo neutro em vez de chutar.

    `scale` sai daqui junto do resto porque quem mostra o número é quem
    precisa saber em quantas casas mostrá-lo: o expoente do Tuya é, na prática,
    a precisão do aparelho (scale 1 -> 126,5 V).
    """
    dps_code = str(dps_code)
    nomeado = _codigo_nomeado(dps_code, category, mapping)
    spec = _spec_do_mapping(dps_code, mapping)

    if nomeado and nomeado in DPS_NOMEADOS:
        info = dict(DPS_NOMEADOS[nomeado])
        info["code"] = nomeado
    elif nomeado:
        # Código nomeado que não está na tabela: o próprio código já é mais
        # informativo que "DP 3".
        info = {"name": nomeado, "unit": "valor", "type": "unknown",
                "code": nomeado}
    else:
        info = dict(DESCONHECIDO)
        info["name"] = "DP %s" % dps_code if dps_code.isdigit() else dps_code
        info["code"] = dps_code

    info.setdefault("scale", 0)
    info.update(spec)
    return info


def unidade_exibivel(info: dict) -> str:
    """
    A unidade que faz sentido AO LADO DE UM NÚMERO — só a de grandeza real.

    A tabela guarda "on/off" para interruptor e "valor" para DP desconhecido,
    e isso é útil no seletor de série ("Interruptor 1 (on/off)"). Ao lado de
    um número vira ruído: "1 on/off" e "724 valor" não dizem nada. Grandeza
    de verdade tem unidade; estado não tem.
    """
    return (info.get("unit") or "") if info.get("type") == "numeric" else ""


def get_friendly_name(dps_code: str, category: str = None,
                      mapping: dict = None) -> str:
    """Nome amigável de um DP."""
    return get_dp_info(dps_code, category, mapping)["name"]


def get_device_dps_list(mapping: dict = None, category: str = None) -> list:
    """
    Os DPs de um dispositivo específico, com nome amigável.

    Alimenta o seletor de série. Usa o mapping_json do aparelho como fonte;
    sem ele, cai na tabela da categoria.
    """
    codigos = []
    if mapping:
        codigos = sorted(mapping.keys(), key=lambda k: (not k.isdigit(), k.zfill(4)))
    elif category and category.lower() in DPS_POR_CATEGORIA:
        codigos = sorted(DPS_POR_CATEGORIA[category.lower()].keys(),
                         key=lambda k: k.zfill(4))

    saida = []
    for codigo in codigos:
        info = get_dp_info(codigo, category, mapping)
        saida.append({
            "code": codigo,
            "name": info["name"],
            "unit": info.get("unit", ""),
            "scale": info.get("scale", 0),
            "type": info.get("type", "unknown"),
            "tuya_code": info.get("code"),
        })
    return saida


def get_common_dps_list() -> list:
    """
    DPs nomeados conhecidos, para sugerir quando o usuário digita um código à
    mão (aparelho sem mapping_json). Só códigos NOMEADOS: um DP numérico solto
    não significa nada sem saber a categoria.
    """
    return [
        {
            "code": codigo,
            "name": "%s (%s)" % (info["name"], codigo),
            "unit": info.get("unit", ""),
            "type": info.get("type", "unknown"),
        }
        for codigo, info in sorted(DPS_NOMEADOS.items())
    ]
