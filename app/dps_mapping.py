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

from typing import Dict, Optional

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
    "forward_energy_total": {"name": "Energia total", "unit": "kWh", "type": "numeric"},

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


def get_dp_info(dps_code: str, category: str = None,
                mapping: dict = None) -> dict:
    """
    Informações de um DP. Passe `category` e/ou o `mapping` do dispositivo
    sempre que tiver — sem eles, um DP numérico baixo é ambíguo e a função
    devolve um rótulo neutro em vez de chutar.
    """
    dps_code = str(dps_code)
    nomeado = _codigo_nomeado(dps_code, category, mapping)

    if nomeado and nomeado in DPS_NOMEADOS:
        info = dict(DPS_NOMEADOS[nomeado])
        info["code"] = nomeado
        return info

    # Código nomeado que não está na tabela: o próprio código já é mais
    # informativo que "DP 3".
    if nomeado:
        return {"name": nomeado, "unit": "valor", "type": "unknown", "code": nomeado}

    info = dict(DESCONHECIDO)
    info["name"] = "DP %s" % dps_code if dps_code.isdigit() else dps_code
    info["code"] = dps_code
    return info


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
