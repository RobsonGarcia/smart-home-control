#!/usr/bin/env python
"""
Monta um data/app.db testável a partir de um backup real.

Serve para rodar o painel fora da rede dos aparelhos, quando o coletor não tem
como capturar nada novo. Não inventa leitura: usa as que já existem no backup.

O que faz, nessa ordem:

  1. copia o backup para data/app.db (recusa sobrescrever sem --force);
  2. aplica as migrações — o backup é do schema antigo, sem locais/cômodos;
  3. desloca o relógio das leituras para a mais recente cair em "agora";
  4. organiza os aparelhos em cômodos pelo nome (--sem-organizar desliga);
  5. liga o monitoramento de alguns aparelhos que não respondem, para o estado
     "offline" existir de verdade na tela.

Sobre o passo 3: a janela de online é de 5 minutos (ONLINE_WINDOW_MINUTES).
Um backup de meia hora atrás deixa TUDO offline, e aí não dá para testar nem a
tela nem o gráfico. Deslocar todas as leituras pelo mesmo delta preserva os
intervalos entre elas — a forma das curvas e quem parou antes de quem continua
igual, só que ancorado no presente.

Sobre o passo 5: não é dado inventado. São aparelhos que têm leitura real com
online=0 (o coletor tentou e não conseguiu resposta) e que estavam com o
monitoramento desligado. Ligando, eles passam a contar como monitorados e sem
resposta, que é exatamente o que "offline" significa.

Uso:
    python scripts/preparar_teste.py
    python scripts/preparar_teste.py --force            # sobrescreve data/app.db
    python scripts/preparar_teste.py --sem-organizar    # deixa tudo sem cômodo
    python scripts/preparar_teste.py --backup outro.db
"""

import argparse
import os
import shutil
import sqlite3
import sys

RAIZ = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, RAIZ)

BACKUP_PADRAO = os.path.join(RAIZ, 'backups', 'app.db')
DESTINO = os.path.join(RAIZ, 'data', 'app.db')

NOME_DO_LOCAL = 'Casa'

# Regra de cômodo por trecho do nome do aparelho. A ordem importa: vence a
# primeira que casar. É um chute a partir do nome — o objetivo é ter a tela
# povoada para testar; corrija na interface o que estiver errado.
REGRAS = [
    ('Sala',          ['luzes sala', 'spots sala']),
    ('Varanda',       ['varanda']),
    ('Garagem',       ['garagem']),
    ('Jardim',        ['jardim']),
    ('Terraço',       ['terraço', 'terraco']),
    ('Sobrado',       ['sobrado']),
    ('Área Externa',  ['externa', 'bomba']),
    ('Cozinha',       ['freezer', 'geladeira']),
    ('Quarto Casal',  ['quarto casal', 'ar condicionado', 'air conditioning']),
    ('Entrada',       ['fechadura']),
    ('Infraestrutura', ['hub ', 'gateway', 'zigbee', 'multimode']),
]

# Aparelhos que passam a ser monitorados para o estado "offline" existir na
# tela. Casa por trecho do nome. Dois casos diferentes de propósito:
# o Freezer respondeu e parou (offline com histórico), a Geladeira nunca
# respondeu (offline sem nenhuma leitura boa).
LIGAR_MONITORAMENTO = ['freezer', 'geladeira']

# Grupos de energia montados a partir dos canais do backup que têm número
# variando — sem isso o painel de Energia só teria curva reta de interruptor.
# São dois casos bem diferentes de propósito, e vale ver os dois:
#
#   - o robô tem 227 leituras com carimbo distinto ao longo de 4 h: é série
#     temporal de verdade, e o eixo sai certo;
#   - o Freezer tem potência em W (o que o painel existe para medir), mas as
#     20 leituras foram gravadas num intervalo de 2 segundos, então o eixo
#     colapsa para milissegundos. Não é defeito da tela.
#
# device: trecho do nome; dp: código do canal.
GRUPOS_DEMO = [
    {
        'nome': 'Bateria do robô',
        'descricao': '227 leituras ao longo de 4 h — série temporal de verdade',
        'series': [('smart 900', '106', 'Robô — DP 106')],
    },
    {
        'nome': 'Consumo do Freezer',
        'descricao': 'Potência em W; as leituras vieram em rajada de 2 s',
        'series': [('freezer', '19', 'Freezer — potência')],
    },
]


def _conectar(caminho):
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    return conn


def copiar(backup, destino, force):
    if not os.path.exists(backup):
        print('backup não encontrado: %s' % backup, file=sys.stderr)
        return False
    if os.path.exists(destino) and not force:
        print('já existe %s.' % destino, file=sys.stderr)
        print('Use --force para sobrescrever (o backup não é tocado).', file=sys.stderr)
        return False

    pasta = os.path.dirname(destino)
    if not os.path.isdir(pasta):
        os.makedirs(pasta)
    shutil.copy2(backup, destino)
    print('1. copiado  %s -> %s (%.1f MB)'
          % (os.path.relpath(backup, RAIZ), os.path.relpath(destino, RAIZ),
             os.path.getsize(destino) / 1048576.0))
    return True


def migrar(destino):
    import app.db as db
    db.DB_PATH = destino          # antes de qualquer init_db
    import app.config as cfg
    cfg.DB_PATH = destino
    db.init_db()
    with _conectar(destino) as c:
        versao = c.execute('PRAGMA user_version').fetchone()[0]
    print('2. migrado  schema na versão %d' % versao)


def deslocar_leituras(destino):
    """Ancora a leitura mais recente em agora, preservando os intervalos."""
    with _conectar(destino) as c:
        linha = c.execute("""
            SELECT MAX(collected_at) AS ultima,
                   CAST((julianday('now') - julianday(MAX(collected_at))) * 86400 AS INTEGER) AS atraso
            FROM readings""").fetchone()
        if linha is None or linha['ultima'] is None:
            print('3. deslocado  nenhuma leitura no backup')
            return

        atraso = linha['atraso'] or 0
        if atraso <= 0:
            print('3. deslocado  leituras já estão no presente')
            return

        c.execute("UPDATE readings SET collected_at = datetime(collected_at, ?)",
                  ('+%d seconds' % atraso,))
        nova = c.execute("SELECT MIN(collected_at), MAX(collected_at) FROM readings").fetchone()
        c.commit()

    horas = atraso / 3600.0
    print('3. deslocado  +%.1f h — leituras agora vão de %s a %s (UTC)'
          % (horas, nova[0], nova[1]))


def organizar(destino):
    import app.repository as r

    locais = r.get_all_locais()
    if not locais:
        print('4. organizado  nenhum local — migração não rodou?')
        return
    local = locais[0]
    if local['nome'] != NOME_DO_LOCAL:
        r.update_local(local['id'], nome=NOME_DO_LOCAL)
    local_id = local['id']

    # Rede do local, deduzida do prefixo mais comum entre os IPs.
    with _conectar(destino) as c:
        ips = [row[0] for row in c.execute(
            "SELECT ip FROM devices WHERE ip IS NOT NULL AND ip != ''")]
    if ips:
        prefixos = {}
        for ip in ips:
            partes = ip.split('.')
            if len(partes) == 4:
                p = '.'.join(partes[:3]) + '.0/24'
                prefixos[p] = prefixos.get(p, 0) + 1
        if prefixos:
            r.update_local(local_id, rede_cidr=max(prefixos, key=prefixos.get))

    existentes = {c['nome']: c['id'] for c in r.get_comodos_by_local(local_id)}
    atribuidos, sem_comodo = 0, []

    for st in r.get_all_device_statuses():
        nome = (st['device']['name'] or '').lower()
        destino_comodo = None
        for comodo, trechos in REGRAS:
            if any(t in nome for t in trechos):
                destino_comodo = comodo
                break

        if destino_comodo is None:
            sem_comodo.append(st['device']['name'])
            continue

        if destino_comodo not in existentes:
            existentes[destino_comodo] = r.create_comodo(local_id, destino_comodo)
        r.assign_device_placement(st['device']['id'], local_id,
                                  existentes[destino_comodo])
        atribuidos += 1

    print('4. organizado  local "%s", %d cômodos, %d aparelhos atribuídos'
          % (NOME_DO_LOCAL, len(existentes), atribuidos))
    if sem_comodo:
        print('              %d ficaram sem cômodo (caixa de entrada): %s'
              % (len(sem_comodo), ', '.join(sem_comodo)))


def ligar_offline(destino):
    """Faz alguns aparelhos que não respondem contarem como offline."""
    import app.repository as r

    ligados = []
    for st in r.get_all_device_statuses():
        nome = (st['device']['name'] or '').lower()
        if st['config']['enabled']:
            continue
        if any(t in nome for t in LIGAR_MONITORAMENTO):
            r.update_monitor_config(st['device']['id'], enabled=True)
            ligados.append(st['device']['name'])

    print('5. offline  monitoramento ligado em %d aparelho(s) que não respondem: %s'
          % (len(ligados), ', '.join(ligados) if ligados else '—'))


def criar_grupos_demo(destino):
    """Grupos de energia com os canais que realmente variam no backup."""
    import app.repository as r

    existentes = {g['name'] for g in r.get_all_comparison_groups()}
    locais = r.get_all_locais()
    escopo = locais[0]['id'] if locais else None
    por_nome = {(st['device']['name'] or '').lower(): st['device']['id']
                for st in r.get_all_device_statuses()}

    criados = []
    for demo in GRUPOS_DEMO:
        if demo['nome'] in existentes:
            continue
        series = []
        for trecho, dp, rotulo in demo['series']:
            alvo = next((i for n, i in por_nome.items() if trecho in n), None)
            if alvo:
                series.append((alvo, dp, rotulo))
        if not series:
            continue
        gid = r.create_comparison_group(demo['nome'], demo['descricao'],
                                        scope_local_id=escopo)
        for device_id, dp, rotulo in series:
            r.add_series_to_group(gid, device_id, dp, rotulo)
        criados.append(demo['nome'])

    print('6. energia  %s' % ('grupos criados: ' + ', '.join(criados)
                              if criados else 'grupos demo já existiam'))


def diagnosticar_series(destino):
    """
    Diz quantos canais numéricos realmente variam AO LONGO DO TEMPO.

    Existe porque um backup pode ter milhares de leituras e ainda assim não
    dar gráfico nenhum: se o aparelho ficou o tempo todo no mesmo estado, a
    curva é uma reta. E se as leituras foram gravadas em rajada (vários
    registros no mesmo segundo), o eixo de tempo colapsa para milissegundos.
    Melhor saber disso aqui do que estranhar a tela depois.
    """
    import json
    from collections import defaultdict

    canais = defaultdict(lambda: {'ts': set(), 'vals': set()})
    with _conectar(destino) as c:
        for row in c.execute("""SELECT d.name, r.collected_at, r.dps_json
                                FROM readings r JOIN devices d ON d.id = r.device_id
                                WHERE r.online = 1"""):
            try:
                dps = json.loads(row['dps_json'])
            except Exception:
                continue
            for chave, valor in dps.items():
                if isinstance(valor, (int, float)) and not isinstance(valor, bool):
                    canal = canais[(row['name'], chave)]
                    canal['ts'].add(row['collected_at'])
                    canal['vals'].add(valor)

    temporais = [(n, dp) for (n, dp), e in canais.items()
                 if len(e['ts']) > 10 and len(e['vals']) > 2]
    rajadas = [(n, dp) for (n, dp), e in canais.items()
               if len(e['vals']) > 2 and len(e['ts']) <= 10]

    print()
    print('gráficos: %d canal(is) numérico(s) com variação ao longo do tempo'
          % len(temporais))
    if not temporais:
        print('   O backup é de aparelhos em estado parado: interruptores')
        print('   desligados e câmeras com configuração fixa. As curvas vão')
        print('   sair retas — não é defeito do painel.')
    if rajadas:
        print('   %d canal(is) variam, mas foram gravados em rajada (vários'
              % len(rajadas))
        print('   registros no mesmo segundo), então o eixo de tempo fica')
        print('   comprimido: %s'
              % ', '.join('%s DP %s' % (n, dp) for n, dp in rajadas[:3]))


def resumir(destino):
    import app.repository as r

    estados = {'online': 0, 'offline': 0, 'sem-coleta': 0}
    for st in r.get_all_device_statuses():
        if not st['config']['enabled']:
            estados['sem-coleta'] += 1
        elif st['is_online']:
            estados['online'] += 1
        else:
            estados['offline'] += 1

    with _conectar(destino) as c:
        leituras = c.execute('SELECT COUNT(*) FROM readings').fetchone()[0]
        grupos = c.execute('SELECT COUNT(*) FROM comparison_groups').fetchone()[0]

    print()
    print('pronto. %d dispositivos, %d leituras, %d grupo(s) de energia'
          % (sum(estados.values()), leituras, grupos))
    print('   online          %d' % estados['online'])
    print('   offline         %d' % estados['offline'])
    print('   não monitorado  %d' % estados['sem-coleta'])
    print()
    print('suba com:  python run_web.py     ->  http://localhost:8000')
    print('o coletor (run_collector.py) não precisa rodar: sem rede, ele só')
    print('gravaria leituras com online=0 e derrubaria tudo para offline.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--backup', default=BACKUP_PADRAO)
    ap.add_argument('--force', action='store_true',
                    help='sobrescreve data/app.db se já existir')
    ap.add_argument('--sem-organizar', action='store_true',
                    help='não cria cômodos; tudo fica na caixa de entrada')
    args = ap.parse_args()

    if not copiar(args.backup, DESTINO, args.force):
        return 1

    migrar(DESTINO)
    deslocar_leituras(DESTINO)
    if args.sem_organizar:
        print('4. organizado  pulado (--sem-organizar)')
    else:
        organizar(DESTINO)
    ligar_offline(DESTINO)
    criar_grupos_demo(DESTINO)
    resumir(DESTINO)
    diagnosticar_series(DESTINO)
    return 0


if __name__ == '__main__':
    sys.exit(main())
