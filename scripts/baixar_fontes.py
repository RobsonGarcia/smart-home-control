#!/usr/bin/env python
"""
Baixa as fontes do painel do Google Fonts para app/static/vendor/fonts/ e
gera o fonts.css que as declara.

Por que vendorizar: o painel roda na LAN de casa e o README promete "sem
dependência de cloud" — puxar fonte de CDN quebra isso, e quebra o painel
inteiro quando a internet cai.

O detalhe que exige cuidado: o Google serve DOIS tipos de família.

  - VARIÁVEL (IBM Plex Sans, Space Grotesk): a mesma URL atende todos os pesos
    pedidos. O certo é UMA face por subset, com INTERVALO de peso.
  - ESTÁTICA (IBM Plex Mono): um arquivo POR PESO. Cada peso precisa da sua
    face; declarar um intervalo apontando para um arquivo só faz o peso 500
    renderizar com o desenho do 400 — sem erro nenhum, só feio.

O script detecta qual é qual comparando as URLs entre os pesos, em vez de
assumir.

Uso:  python scripts/baixar_fontes.py
"""

import os
import re
import sys
import urllib.request
from collections import OrderedDict

# Um User-Agent moderno é o que faz o Google devolver woff2 em vez de ttf.
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

# Os pesos que o app.css realmente usa. Pedir menos deixa o pacote menor.
FAMILIAS = [
    ('IBM Plex Mono', ['400', '500']),
    ('IBM Plex Sans', ['400', '500', '600']),
    ('Space Grotesk', ['500', '600', '700']),
]

# latin + latin-ext cobrem o português. Sem cirílico/grego/vietnamita.
SUBSETS = ('latin', 'latin-ext')

RAIZ = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DESTINO = os.path.join(RAIZ, 'app', 'static', 'vendor')

CABECALHO = """/* Fontes servidas localmente: este painel roda em LAN e nao deve depender de
   internet para renderizar. Subsets latin e latin-ext, que cobrem o portugues.

   Duas formas aqui, porque o Google serve os dois tipos:

   - IBM Plex Sans e Space Grotesk sao VARIAVEIS: o mesmo arquivo atende todo
     peso, entao ha uma face por subset com INTERVALO de peso.
   - IBM Plex Mono e ESTATICA: um arquivo POR PESO, e cada um precisa da sua
     face. Declarar um intervalo apontando para um arquivo so faria o peso 500
     renderizar com o desenho do 400.

   GERADO por scripts/baixar_fontes.py -- nao edite a mao. */

"""


def _buscar(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=30).read()


def _faces_do_css(css):
    """(peso, subset) -> (url, unicode-range), só dos subsets que interessam."""
    faces = OrderedDict()
    for bloco in re.split(r'(?=/\*\s*[a-z0-9-]+\s*\*/)', css):
        m = re.match(r'/\*\s*([a-z0-9-]+)\s*\*/', bloco.strip())
        if not m or m.group(1) not in SUBSETS:
            continue
        peso = re.search(r'font-weight:\s*([^;]+);', bloco)
        url = re.search(r'url\((https://[^)]+)\)', bloco)
        faixa = re.search(r'unicode-range:\s*([^;]+);', bloco)
        if peso and url:
            faces[(peso.group(1).strip(), m.group(1))] = (
                url.group(1), faixa.group(1).strip() if faixa else None)
    return faces


def _e_variavel(faces):
    """Variável = a mesma URL aparece em todos os pesos."""
    por_peso = {}
    for (peso, _), (url, _) in faces.items():
        por_peso.setdefault(peso, set()).add(url)
    if len(por_peso) < 2:
        return True
    todas = set().union(*por_peso.values())
    return all(urls == todas for urls in por_peso.values())


def main():
    pasta_fontes = os.path.join(DESTINO, 'fonts')
    if not os.path.isdir(DESTINO):
        print('destino não encontrado: %s' % DESTINO, file=sys.stderr)
        return 1
    if not os.path.isdir(pasta_fontes):
        os.makedirs(pasta_fontes)

    regras = []
    total = 0

    for familia, pesos in FAMILIAS:
        url = ('https://fonts.googleapis.com/css2?family=%s:wght@%s&display=swap'
               % (familia.replace(' ', '+'), ';'.join(pesos)))
        faces = _faces_do_css(_buscar(url).decode('utf-8'))
        if not faces:
            print('nenhuma face encontrada para %s' % familia, file=sys.stderr)
            return 1

        variavel = _e_variavel(faces)
        base = familia.replace(' ', '')
        print('%-16s %s' % (familia, 'variável' if variavel else 'estática'))

        if variavel:
            intervalo = '%s %s' % (pesos[0], pesos[-1])
            ja_baixados = set()
            for (_, subset), (u, faixa) in faces.items():
                if subset in ja_baixados:
                    continue
                ja_baixados.add(subset)
                nome = '%s-%s.woff2' % (base, subset)
                dados = _buscar(u)
                open(os.path.join(pasta_fontes, nome), 'wb').write(dados)
                total += len(dados)
                regras.append((familia, intervalo, nome, faixa))
        else:
            for (peso, subset), (u, faixa) in faces.items():
                nome = '%s-%s-%s.woff2' % (base, peso, subset)
                dados = _buscar(u)
                open(os.path.join(pasta_fontes, nome), 'wb').write(dados)
                total += len(dados)
                regras.append((familia, peso, nome, faixa))

    corpo = '\n'.join(
        "@font-face {\n"
        "  font-family: '%s';\n"
        "  font-style: normal;\n"
        "  font-weight: %s;\n"
        "  font-display: swap;\n"
        "  src: url('./fonts/%s') format('woff2');\n"
        "%s}\n" % (fam, peso, nome,
                   "  unicode-range: %s;\n" % faixa if faixa else '')
        for fam, peso, nome, faixa in regras)

    caminho_css = os.path.join(DESTINO, 'fonts.css')
    with open(caminho_css, 'w', encoding='utf-8') as f:
        f.write(CABECALHO + corpo)

    print('\n%d arquivos, %.0f KB, %d regras @font-face'
          % (len(os.listdir(pasta_fontes)), total / 1024.0, len(regras)))
    print('css escrito em %s' % caminho_css)
    return 0


if __name__ == '__main__':
    sys.exit(main())
