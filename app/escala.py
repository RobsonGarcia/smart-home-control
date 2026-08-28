"""
Escala dos DPs Tuya: o fator que separa o número gravado do número real.

O Tuya publica, por DP, um `scale` dentro de `values` — e o valor que o
aparelho manda é INTEIRO, deslocado por esse expoente:

    valor real = valor cru / 10 ** scale

A tomada que reporta `cur_voltage = 1265` está dizendo 126,5 V (scale 1), e a
que reporta `add_ele = 29` está dizendo 0,029 kWh (scale 3). Sem aplicar isso,
o painel mostra "1265 V" ao lado de um rótulo que promete volts — o número e a
unidade discordam, e a unidade está certa.

A conversão acontece na COLETA: `readings.dps_json` guarda o valor já em
unidade real, e toda a leitura (gráfico, legenda, grupos de energia) fica
honesta sem cada tela lembrar de dividir. O caminho de COMANDO é o inverso —
o aparelho só aceita o inteiro cru — e por isso este módulo converte nos dois
sentidos.

O mapa vem de `devices.mapping_json`, que já é gravado inteiro pelo import do
devices.json. Nada de coluna nova: o scale sempre esteve no banco, só nunca
tinha sido lido.

Fora do escopo, de propósito: as fontes solares. O driver de cada fabricante
já entrega em unidade real (ver `app/solar/base.py`) — escalar de novo ali
seria dividir duas vezes.
"""

import json
from typing import Any, Dict, Optional

from app.dps_mapping import get_dp_info

# Tipos do mapping que carregam número escalável. Boolean, Enum, String,
# Bitmap e Raw passam intactos — não há o que dividir num on/off.
_TIPOS_NUMERICOS = {"integer", "value"}


def _mapping_de(device) -> dict:
    """mapping_json do device como dict — tolerante a nulo e a JSON quebrado."""
    bruto = (device or {}).get("mapping_json")
    if not bruto:
        return {}
    try:
        dados = json.loads(bruto)
    except (json.JSONDecodeError, TypeError):
        return {}
    return dados if isinstance(dados, dict) else {}


def escalas_do_device(device) -> Dict[str, int]:
    """
    Expoente de escala por chave de DP, indexado das DUAS formas.

    Isto não é preciosismo: a leitura pela LAN chega chaveada pelo DP numérico
    (`{"19": 777}`, app/collector.py) e a pela nuvem pelo código nomeado
    (`{"cur_power": 777}`, app/control/cloud.py). O mesmo dispositivo produz
    frames com chaves diferentes conforme o caminho, e um mapa só resolveria
    metade deles.

    DPs sem escala (ou com scale 0) ficam de fora — o dicionário só tem o que
    de fato precisa ser convertido.
    """
    saida: Dict[str, int] = {}
    mapping = _mapping_de(device)
    if not mapping:
        return saida

    categoria = (device or {}).get("category")
    for dp, entrada in mapping.items():
        if not isinstance(entrada, dict):
            continue

        tipo = str(entrada.get("type") or "").strip().lower()
        if tipo and tipo not in _TIPOS_NUMERICOS:
            continue

        valores = entrada.get("values")
        if not isinstance(valores, dict):
            continue

        try:
            escala = int(valores.get("scale") or 0)
        except (TypeError, ValueError):
            continue
        if escala <= 0:
            continue

        saida[str(dp)] = escala
        codigo = get_dp_info(dp, categoria, mapping).get("code")
        if codigo:
            saida[str(codigo)] = escala

    return saida


def escala_de(escalas: Dict[str, int], chave: Any) -> int:
    """O expoente de uma chave, 0 quando ela não tem escala."""
    return escalas.get(str(chave), 0)


def aplicar_valor(valor: Any, escala: int) -> Any:
    """
    Um valor cru em unidade real.

    `bool` é subclasse de `int` em Python: sem o teste explícito, um
    `switch_1 = True` com escala viraria 0.1. Nunca acontece hoje (Boolean não
    tem scale), mas o custo do teste é uma linha.
    """
    if escala <= 0 or isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return valor
    return round(valor / (10 ** escala), escala)


def aplicar(dps: dict, escalas: Dict[str, int]) -> dict:
    """
    Um frame de DPs inteiro, cru -> unidade real.

    Devolve um dicionário novo; o que não tem escala é copiado como está.
    """
    if not dps:
        return {}
    if not escalas:
        return dict(dps)
    return {chave: aplicar_valor(valor, escala_de(escalas, chave))
            for chave, valor in dps.items()}


def aplicar_no_device(dps: dict, device) -> dict:
    """Atalho para quem tem o device em mãos e não o mapa de escalas."""
    return aplicar(dps, escalas_do_device(device))


def reverter_valor(valor: Any, escala: int) -> Any:
    """
    Unidade real -> valor cru, o que o aparelho aceita num comando.

    Volta a INTEIRO porque é isso que o DP espera; um `bright_value` de 12,5
    com escala 1 vira 125, não 125.0.
    """
    if escala <= 0 or isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return valor
    return int(round(valor * (10 ** escala)))


def escalar_faixa(valores: Optional[dict], escala: int) -> dict:
    """
    `min`/`max`/`step` do mapping em unidade real.

    O `<input type=number>` da tela de detalhe é montado com esta faixa. Se o
    valor atual vem escalado e a faixa não, o campo nasce fora dos próprios
    limites — e o usuário vê um erro que não cometeu.
    """
    faixa = dict(valores) if isinstance(valores, dict) else {}
    if escala <= 0:
        return faixa
    for chave in ("min", "max", "step"):
        if faixa.get(chave) is None:
            continue
        try:
            valor = aplicar_valor(float(faixa[chave]), escala)
        except (TypeError, ValueError):
            continue
        # "aceita no mínimo 10" lê melhor que "no mínimo 10.0" — e o campo da
        # tela recebe um limite que não parece ter precisão que não tem.
        faixa[chave] = int(valor) if float(valor).is_integer() else valor
    return faixa
