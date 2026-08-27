#!/usr/bin/env python
"""
Sonda da API SolPlanet/AiSWEI: valida credenciais, assinatura e conversão
de canais ANTES de configurar qualquer coisa na interface.

Uso (nesta ordem de precedência):
    python scripts/sonda_solplanet.py --appkey X --appsecret Y --token Z
    # ou via ambiente (SOLPLANET_APPKEY/APPSECRET/TOKEN/PLANTA);
    # ou, o mais prático: um solar.local.json na raiz do projeto, gitignorado.
    # Conta Pro (com token):
    #   {"appkey": "...", "appsecret": "...", "token": "..."}
    # Conta comum do cloud.solplanet.net (sem token, planta na mão):
    #   {"appkey": "...", "appsecret": "...", "planta_apikey": "..."}
    python scripts/sonda_solplanet.py

O que ela faz, na ordem: testar() -> plantas -> inversores da primeira
planta -> última telemetria de cada inversor, já convertida para os códigos
canônicos do sistema. Nenhuma credencial é impressa.

Se a assinatura falhar, o erro traz o X-Ca-Error-Message do gateway — que
contém o string-to-sign calculado PELO SERVIDOR, o único jeito de comparar
com o nosso e achar a diferença.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.errors import ValidationError          # noqa: E402
from app.solar import get_driver                # noqa: E402
from app.solar.base import CANAIS_SOLAR         # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Sonda da API SolPlanet/AiSWEI")
    parser.add_argument("--appkey", default=os.environ.get("SOLPLANET_APPKEY", ""))
    parser.add_argument("--appsecret", default=os.environ.get("SOLPLANET_APPSECRET", ""))
    parser.add_argument("--token", default=os.environ.get("SOLPLANET_TOKEN", ""))
    parser.add_argument("--planta",
                        default=os.environ.get("SOLPLANET_PLANTA", ""),
                        help="API Key da planta (obrigatório sem token)")
    parser.add_argument("--arquivo", default="solar.local.json",
                        help="JSON com appkey/appsecret/token/planta_apikey")
    args = parser.parse_args()

    if os.path.isfile(args.arquivo):
        import json
        with open(args.arquivo, encoding="utf-8") as f:
            do_arquivo = json.load(f)
        args.appkey = args.appkey or do_arquivo.get("appkey", "")
        args.appsecret = args.appsecret or do_arquivo.get("appsecret", "")
        args.token = args.token or do_arquivo.get("token", "")
        args.planta = args.planta or do_arquivo.get("planta_apikey", "")
        args.regiao = do_arquivo.get("regiao", "")
        args.nivel = do_arquivo.get("nivel_acesso", "")

    if not (args.appkey and args.appsecret):
        parser.error("informe ao menos --appkey e --appsecret (ou as "
                     "variáveis SOLPLANET_*, ou um solar.local.json)")
    if not (args.token or args.planta):
        parser.error("informe o token (conta Pro) OU --planta com o API Key "
                     "da planta (conta comum)")

    # Sem token = conta comum: a planta é obrigatória e não há listagem.
    nivel = getattr(args, "nivel", "") or ("pro" if args.token else "comum")
    credenciais = {"appkey": args.appkey, "appsecret": args.appsecret,
                   "token": args.token, "nivel_acesso": nivel}
    if getattr(args, "regiao", ""):
        credenciais["regiao"] = args.regiao
    if args.planta:
        credenciais["planta_apikey"] = args.planta
    print("modo: %s | região: %s"
          % (nivel, getattr(args, "regiao", "") or "eu (padrão)"))

    driver_cls = get_driver("solplanet")

    try:
        print("1. testando credenciais/assinatura...")
        driver_cls(credenciais).testar()
        print("   ok — assinatura aceita pelo gateway")

        print("2. plantas visíveis:")
        plantas = driver_cls(credenciais).descobrir_plantas()
        if not plantas:
            print("   nenhuma planta nesta conta — nada mais a sondar")
            return 0
        for p in plantas:
            print("   - %-30s %6.2f kW  %-8s apikey=%s..."
                  % (p.nome, p.potencia_kw, p.status, p.apikey[:8]))

        apikey = args.planta or plantas[0].apikey
        driver = driver_cls({**credenciais, "planta_apikey": apikey})

        print("3. resumo da planta:")
        resumo = driver.resumo_planta()
        print("   agora %.0f W | hoje %.1f kWh | mês %.1f kWh | total %.1f kWh | atualizado %s"
              % (resumo.potencia_w, resumo.geracao_hoje_kwh,
                 resumo.geracao_mes_kwh, resumo.geracao_total_kwh,
                 resumo.ultima_atualizacao or "?"))

        print("4. inversores:")
        inversores = driver.descobrir_inversores()
        if not inversores:
            print("   nenhum inversor listado")
            return 0
        for inv in inversores:
            print("   - sn=%s  estado=%-8s ultima_com=%s"
                  % (inv.sn, inv.estado or "?", inv.ultima_comunicacao or "?"))

        print("5. última telemetria (canais canônicos):")
        for inv in inversores:
            t = driver.ultima_telemetria(inv.sn)
            print("   %s  tmstp=%d  online=%s" % (inv.sn, t.tmstp_ms, t.online))
            for codigo in t.valores:
                info = CANAIS_SOLAR.get(codigo, {})
                print("      %-22s %10.3f %-4s (%s)"
                      % (codigo, t.valores[codigo],
                         info.get("unit", ""), info.get("name", "?")))
            if not t.valores:
                print("      (sem dados — inversor offline?)")

    except ValidationError as exc:
        print("\nERRO: %s" % exc.message, file=sys.stderr)
        return 1

    print("\nsonda concluída — driver funcionando de ponta a ponta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
