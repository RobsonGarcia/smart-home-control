#!/usr/bin/env python
"""
Sonda de vídeo: descobre COMO (ou se) dá para ver as suas câmeras.

Rode isto na rede das câmeras ANTES de configurar qualquer coisa na
interface. É a mesma disciplina da sonda do SolPlanet: o que decide o
caminho é a resposta do aparelho, não a documentação do fabricante.

    python scripts/sonda_onvif.py                       # descobre e varre
    python scripts/sonda_onvif.py --usuario admin --senha ****
    python scripts/sonda_onvif.py --host 192.168.3.9 --porta 8000
    python scripts/sonda_onvif.py --arquivo camera.local.json

O que ela faz, na ordem:

  1. WS-Discovery na LAN (multicast) — quem se anuncia como câmera ONVIF;
  2. os IPs das câmeras que já estão no inventário do painel;
  3. varredura das portas típicas de ONVIF e RTSP em cada IP;
  4. com credenciais, tenta ONVIF de verdade: informação do aparelho,
     perfis, URL de foto e URL de vídeo.

Nenhuma senha é impressa — nem dentro das URLs, que saem mascaradas.

Como ler o resultado:

  - respondeu ONVIF  -> use o driver `onvif`; o painel descobre o resto.
  - só porta RTSP    -> use o driver `rtsp` com a URL que o fabricante
                        documenta (`rtsp://IP:6554/stream_0` é o formato
                        mais comum em câmera Tuya).
  - nada aberto      -> a câmera só fala pelo app/nuvem do fabricante;
                        o caminho é a nuvem Tuya, quando houver credencial.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.config import PORTAS_ONVIF, PORTAS_RTSP    # noqa: E402
from app.errors import DomainError                  # noqa: E402
from app.cameras.onvif import (                     # noqa: E402
    OnvifDriver,
    descobrir,
    portas_abertas,
)


def _mascarar(uri: str) -> str:
    """URL sem a senha — a de stream costuma trazer credencial embutida."""
    if "@" not in uri:
        return uri
    antes, depois = uri.split("@", 1)
    esquema = antes.split("//", 1)[0] if "//" in antes else ""
    return "%s//****:****@%s" % (esquema, depois)


def _cameras_do_inventario():
    """IPs das câmeras que o painel já conhece — o alvo mais provável."""
    try:
        from app.capacidades import tipo_do_dispositivo
        from app.repository import get_all_devices
    except Exception as exc:
        print("   (não consegui ler o inventário: %s)" % exc)
        return []
    saida = []
    for device in get_all_devices():
        if tipo_do_dispositivo(device) == "camera" and device.get("ip"):
            saida.append((device["name"], device["ip"]))
    return saida


def main():
    parser = argparse.ArgumentParser(description="Sonda ONVIF/RTSP de câmeras")
    parser.add_argument("--host", action="append", default=[],
                        help="IP a testar (repetível). Sem isto, usa a "
                             "descoberta e o inventário do painel.")
    parser.add_argument("--porta", type=int, default=None,
                        help="Porta ONVIF a forçar (padrão: as típicas)")
    parser.add_argument("--usuario", default=os.environ.get("CAMERA_USUARIO", ""))
    parser.add_argument("--senha", default=os.environ.get("CAMERA_SENHA", ""))
    parser.add_argument("--arquivo", default="camera.local.json",
                        help='JSON com {"usuario": "...", "senha": "..."}')
    parser.add_argument("--timeout", type=float, default=4.0,
                        help="segundos de escuta na descoberta")
    args = parser.parse_args()

    usuario, senha = args.usuario, args.senha
    if not usuario and os.path.exists(args.arquivo):
        try:
            with open(args.arquivo, encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
            usuario = dados.get("usuario", "")
            senha = dados.get("senha", "")
            print("credenciais lidas de %s" % args.arquivo)
        except (OSError, ValueError) as exc:
            print("aviso: %s ilegível (%s)" % (args.arquivo, exc))

    alvos = {}   # ip -> porta ONVIF sugerida

    print("\n1. WS-Discovery (multicast na LAN, %.1fs)" % args.timeout)
    try:
        encontradas = descobrir(timeout=args.timeout)
    except Exception as exc:
        encontradas = []
        print("   falhou: %s" % exc)
    if encontradas:
        for camera in encontradas:
            print("   %-16s porta %-5d %s %s"
                  % (camera.host, camera.porta, camera.fabricante or "",
                     camera.nome or ""))
            alvos[camera.host] = camera.porta
    else:
        print("   nenhuma câmera se anunciou.")
        print("   (isso NÃO fecha a questão: há firmware com o discovery "
              "desligado e ONVIF funcionando)")

    if args.host:
        print("\n2. Hosts informados na linha de comando")
        for ip in args.host:
            print("   %s" % ip)
            alvos[ip] = args.porta
    else:
        print("\n2. Câmeras no inventário do painel")
        for nome, ip in _cameras_do_inventario():
            print("   %-16s %s" % (ip, nome))
            alvos.setdefault(ip, None)

    if not alvos:
        print("\nNada para testar. Passe --host IP ou rode isto na rede das "
              "câmeras.")
        return 1

    print("\n3. Portas abertas (ONVIF %s / RTSP %s)"
          % (", ".join(map(str, PORTAS_ONVIF)), ", ".join(map(str, PORTAS_RTSP))))
    candidatos = {}
    for ip in sorted(alvos):
        onvif = portas_abertas(ip, PORTAS_ONVIF)
        rtsp = portas_abertas(ip, PORTAS_RTSP)
        print("   %-16s ONVIF: %-14s RTSP: %s"
              % (ip, ", ".join(map(str, onvif)) or "nenhuma",
                 ", ".join(map(str, rtsp)) or "nenhuma"))
        porta = alvos[ip] or (onvif[0] if onvif else None)
        if porta:
            candidatos[ip] = porta
        elif rtsp:
            print("      -> sem ONVIF, mas com RTSP: use o driver `rtsp` "
                  "com rtsp://%s:%d/stream_0" % (ip, rtsp[0]))

    if not candidatos:
        print("\nNenhuma porta ONVIF aberta. Caminho: driver `rtsp` onde há "
              "porta RTSP, ou a nuvem Tuya no resto.")
        return 0

    if not usuario:
        print("\n4. ONVIF: pulado (sem credenciais)")
        print("   Rode de novo com --usuario/--senha, ou crie um "
              "%s com usuario e senha." % args.arquivo)
        return 0

    print("\n4. ONVIF com credenciais")
    algum = False
    for ip, porta in sorted(candidatos.items()):
        print("   %s:%d" % (ip, porta))
        driver = OnvifDriver({"host": ip, "porta": porta,
                              "usuario": usuario, "senha": senha})
        try:
            info = driver.testar()
            print("      aparelho: %s %s (firmware %s)"
                  % (info.get("fabricante") or "?", info.get("modelo") or "?",
                     info.get("firmware") or "?"))
            for perfil in driver.perfis():
                print("      perfil %-18s %-10s"
                      % (perfil.nome, perfil.resolucao or "?"))
                print("         foto  : %s"
                      % (_mascarar(perfil.snapshot_uri) or "(não expõe)"))
                print("         vídeo : %s"
                      % (_mascarar(perfil.stream_uri) or "(não expõe)"))
            algum = True
        except DomainError as exc:
            print("      recusou: %s" % exc.message)
        except Exception as exc:
            print("      erro inesperado: %s" % exc)

    print("\n%s" % ("Pelo menos uma câmera falou ONVIF — use o driver `onvif` "
                    "no painel." if algum else
                    "Nenhuma completou o ONVIF. Se o erro foi de senha, "
                    "confira o usuário ONVIF no app do fabricante; se foi de "
                    "protocolo, o caminho é o driver `rtsp`."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
