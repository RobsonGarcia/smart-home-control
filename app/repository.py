import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.config import (
    DEFAULT_LOCAL_NAME,
    DEFAULT_POLL_INTERVAL_SECONDS,
    ONLINE_WINDOW_MINUTES,
    SOLAR_POLL_INTERVAL_SECONDS,
)
from app.db import get_db
from app.errors import ConflictError, NotFoundError, ValidationError

# Sentinela para distinguir "nao informado" de "informado como None", porque
# scope_local_id = None e um valor com significado proprio (grupo geral).
_NAO_INFORMADO = object()


def insert_or_update_device(device_data: Dict[str, Any]) -> None:
    """Insere ou atualiza um dispositivo no inventário."""
    with get_db() as conn:
        cursor = conn.cursor()
        # Placement so entra no INSERT (dispositivo novo cai no local/comodo
        # padrao). O DO UPDATE SET abaixo NAO lista local_id/comodo_id de
        # proposito: e isso que faz a atribuicao do usuario sobreviver a um
        # scan de rede ou a uma reimportacao do devices.json, que nao tem
        # nocao nenhuma de comodo. Nao adicione essas colunas la.
        local_id, comodo_id = _placement_padrao(conn)

        # mapping_json e icon_url sao COALESCE/NULLIF pela mesma razao que ip e
        # protocol_version: o scan de rede (app/scanner.py) nao conhece nem a
        # especificacao de DPs nem o icone, e mandava '{}' e NULL por cima do
        # que o devices.json tinha trazido. Isso apagava o `scale` de que a
        # coleta depende -- as leituras voltavam a ser gravadas cruas.
        cursor.execute("""
            INSERT INTO devices
            (id, name, local_key, category, product_name, model, product_id,
             mapping_json, icon_url, is_sub, parent_id, ip, protocol_version,
             source, created_at, updated_at, local_id, comodo_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                local_key=excluded.local_key,
                category=excluded.category,
                product_name=excluded.product_name,
                model=excluded.model,
                product_id=COALESCE(NULLIF(excluded.product_id, ''), product_id),
                mapping_json=COALESCE(
                    NULLIF(NULLIF(excluded.mapping_json, '{}'), ''),
                    mapping_json),
                icon_url=COALESCE(NULLIF(excluded.icon_url, ''), icon_url),
                is_sub=excluded.is_sub,
                parent_id=excluded.parent_id,
                ip=COALESCE(excluded.ip, ip),
                protocol_version=COALESCE(excluded.protocol_version, protocol_version),
                source=excluded.source,
                updated_at=CURRENT_TIMESTAMP
        """, (
            device_data.get('id'),
            device_data.get('name'),
            device_data.get('key'),
            device_data.get('category'),
            device_data.get('product_name'),
            device_data.get('model'),
            device_data.get('product_id'),
            json.dumps(device_data.get('mapping', {})),
            device_data.get('icon'),
            1 if device_data.get('sub') else 0,
            device_data.get('parent'),
            device_data.get('ip'),
            device_data.get('version'),
            device_data.get('source', 'cloud'),
            datetime.now(),
            datetime.now(),
            local_id,
            comodo_id
        ))

        # Enrolamento no coletor acontece aqui, na entrada do inventario, e
        # nao mais como efeito colateral de renderizar uma pagina.
        cursor.execute("""
            INSERT OR IGNORE INTO monitor_configs
            (device_id, enabled, poll_interval_seconds)
            VALUES (?, 1, ?)
        """, (device_data.get('id'), DEFAULT_POLL_INTERVAL_SECONDS))


def registrar_descoberta(device_id: str, ip: str, name: str = None,
                         local_key: str = None,
                         protocol_version=None) -> bool:
    """
    Grava o que um broadcast da LAN descobriu. Devolve True se criou a linha.

    Por que isto NAO usa insert_or_update_device: aquela funcao e para quem
    sabe TUDO sobre o aparelho -- o devices.json, que traz categoria, modelo,
    icone e a especificacao de DPs. O broadcast nao sabe nada disso. Passando
    por la, o scan mandava category='', model='' e mapping={} e, na hora em
    que ele finalmente acerta a linha certa (ver app/scanner.py), apagaria a
    categoria e a especificacao de todo aparelho visivel na rede.

    Entao o UPDATE aqui toca SO o que o broadcast realmente conhece: onde o
    aparelho esta (ip), como falar com ele (local_key, protocol_version) e
    quando foi visto. O que ele nao sabe, ele nao escreve -- inclusive
    `source`, que por isso nao rebaixa para 'broadcast' um dispositivo que
    veio do devices.json.

    O nome so entra na CRIACAO: renomear um aparelho na tela nao pode ser
    desfeito por uma varredura de rede.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        existe = cursor.execute("SELECT 1 FROM devices WHERE id = ?",
                                (device_id,)).fetchone() is not None

        if existe:
            cursor.execute("""
                UPDATE devices SET
                    ip = COALESCE(?, ip),
                    local_key = COALESCE(?, local_key),
                    protocol_version = COALESCE(?, protocol_version),
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (ip, local_key, protocol_version, device_id))
            return False

        local_id, comodo_id = _placement_padrao(conn)
        cursor.execute("""
            INSERT INTO devices
            (id, name, local_key, ip, protocol_version, source,
             last_seen_at, created_at, updated_at, local_id, comodo_id)
            VALUES (?, ?, ?, ?, ?, 'broadcast',
                    CURRENT_TIMESTAMP, ?, ?, ?, ?)
        """, (device_id, name or device_id, local_key, ip, protocol_version,
              datetime.now(), datetime.now(), local_id, comodo_id))

        # Mesma politica de insert_or_update_device: quem entra no inventario
        # ja entra no coletor.
        cursor.execute("""
            INSERT OR IGNORE INTO monitor_configs
            (device_id, enabled, poll_interval_seconds)
            VALUES (?, 1, ?)
        """, (device_id, DEFAULT_POLL_INTERVAL_SECONDS))
        return True


def get_device(device_id: str) -> Optional[Dict]:
    """Retorna um dispositivo pelo ID."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_devices() -> List[Dict]:
    """Retorna todos os dispositivos ordenados por nome."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM devices ORDER BY name")
        return [dict(row) for row in cursor.fetchall()]


def insert_reading(device_id: str, dps_json: str, online: bool,
                   collected_at: str = None) -> None:
    """
    Insere uma leitura de status do dispositivo.

    `collected_at` ("YYYY-MM-DD HH:MM:SS", UTC) é o instante da MEDIÇÃO,
    quando ele é conhecido — o coletor solar grava o tmstp do equipamento,
    senão o backfill nasceria com todos os pontos amontoados no "agora".
    Sem ele, vale o relógio do banco (coleta Tuya em tempo real).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        if collected_at is None:
            cursor.execute("""
                INSERT INTO readings (device_id, dps_json, online, collected_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (device_id, dps_json, 1 if online else 0))
        else:
            cursor.execute("""
                INSERT INTO readings (device_id, dps_json, online, collected_at)
                VALUES (?, ?, ?, ?)
            """, (device_id, dps_json, 1 if online else 0, collected_at))


def get_latest_reading(device_id: str) -> Optional[Dict]:
    """
    Retorna a leitura mais recente de um dispositivo.

    O desempate por id é necessário, não decorativo: collected_at tem
    resolução de 1 segundo, e um comando grava a leitura dele no mesmo
    segundo em que o coletor pode ter gravado a dele. Sem o desempate, qual
    das duas volta é sorteio — e o painel pisca o estado antigo.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM readings WHERE device_id = ?
            ORDER BY collected_at DESC, id DESC LIMIT 1
        """, (device_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_readings_for_series(device_id: str, dps_code: str,
                           start_timestamp: str = None,
                           end_timestamp: str = None,
                           max_pontos: int = None) -> List[Dict]:
    """
    Retorna leituras de um DP específico de um device em um período.

    `max_pontos` afina a série mantendo 1 a cada k amostras. Trinta dias de um
    inversor são ~4.500 pontos POR SÉRIE (ele mede a cada 5 min), e um gráfico
    de 900 px não tem pixel para todos eles — mandar tudo só gasta banda e
    trava o navegador. O primeiro e o último ponto são sempre preservados,
    para o período começar e terminar onde deve.
    """
    with get_db() as conn:
        cursor = conn.cursor()
        query = """
            SELECT collected_at, dps_json FROM readings
            WHERE device_id = ? AND online = 1
        """
        params = [device_id]

        if start_timestamp:
            query += " AND collected_at >= ?"
            params.append(start_timestamp)
        if end_timestamp:
            query += " AND collected_at <= ?"
            params.append(end_timestamp)

        query += " ORDER BY collected_at ASC"
        cursor.execute(query, params)

        results = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            dps_dict = json.loads(row_dict['dps_json'])
            value = dps_dict.get(dps_code)
            if value is not None:
                results.append({
                    'timestamp': row_dict['collected_at'],
                    'value': value
                })

        if max_pontos and len(results) > max_pontos:
            passo = len(results) / float(max_pontos)
            afinado = [results[int(i * passo)] for i in range(max_pontos)]
            if afinado[-1] is not results[-1]:
                afinado[-1] = results[-1]
            return afinado
        return results


def get_or_create_monitor_config(device_id: str, poll_interval: int = 60) -> Dict:
    """Obtém ou cria configuração de monitoramento para um device."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM monitor_configs WHERE device_id = ?",
                      (device_id,))
        row = cursor.fetchone()

        if row:
            return dict(row)

        cursor.execute("""
            INSERT INTO monitor_configs (device_id, enabled, poll_interval_seconds)
            VALUES (?, 1, ?)
        """, (device_id, poll_interval))

        cursor.execute("SELECT * FROM monitor_configs WHERE device_id = ?",
                      (device_id,))
        return dict(cursor.fetchone())


def update_monitor_config(device_id: str, enabled: bool = None,
                         poll_interval: int = None) -> None:
    """Atualiza configuração de monitoramento."""
    with get_db() as conn:
        cursor = conn.cursor()

        if enabled is not None:
            cursor.execute(
                "UPDATE monitor_configs SET enabled = ? WHERE device_id = ?",
                (1 if enabled else 0, device_id)
            )

        if poll_interval is not None:
            cursor.execute(
                "UPDATE monitor_configs SET poll_interval_seconds = ? WHERE device_id = ?",
                (poll_interval, device_id)
            )


def get_all_monitor_configs() -> List[Dict]:
    """Retorna todas as configurações de monitoramento."""
    with get_db() as conn:
        cursor = conn.cursor()
        # O source do device decide QUAL coletor roda (tinytuya x driver
        # solar); o JOIN também descarta configs órfãs de devices apagados.
        cursor.execute("""
            SELECT mc.*, COALESCE(d.source, 'cloud') AS source
            FROM monitor_configs mc
            JOIN devices d ON d.id = mc.device_id
            WHERE mc.enabled = 1
        """)
        return [dict(row) for row in cursor.fetchall()]


def get_device_status(device_id: str) -> Optional[Dict]:
    """Status de um device. Wrapper de _query_device_statuses (1 query)."""
    linhas = _query_device_statuses(device_id=device_id, incluir_solar=True)
    return linhas[0] if linhas else None


def insert_discovery_log(device_id: str, ip: str, raw_json: str = None) -> None:
    """Registra uma descoberta de dispositivo na rede."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO discovery_log (device_id, ip, raw_json, timestamp)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (device_id, ip, raw_json))


def create_comparison_group(name: str, description: str = None,
                            scope_local_id: Optional[int] = None) -> int:
    """Cria um grupo comparativo. scope_local_id None = grupo geral."""
    with get_db() as conn:
        cursor = conn.cursor()
        if scope_local_id is not None:
            _exige_local(conn, scope_local_id)
        cursor.execute("""
            INSERT INTO comparison_groups (name, description, scope_local_id)
            VALUES (?, ?, ?)
        """, (name, description, scope_local_id))
        group_id = cursor.lastrowid
        # Todo grupo nasce com um painel. A alternativa -- criar o primeiro
        # painel na primeira serie -- deixaria um grupo vazio sem lugar para
        # a serie ir, e a tela teria que lidar com um estado que nao precisa
        # existir. E na mesma transacao: um grupo sem painel nunca chega ao
        # disco.
        cursor.execute("""
            INSERT INTO comparison_panels (group_id, nome, principal, sort_order)
            VALUES (?, 'Principal', 1, 0)
        """, (group_id,))
        return group_id


def get_comparison_group(group_id: int) -> Optional[Dict]:
    """Retorna um grupo comparativo e suas séries."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM comparison_groups WHERE id = ?", (group_id,))
        group = cursor.fetchone()
        if not group:
            return None

        group_dict = dict(group)
        group_dict['series'] = _series_do_grupo(conn, group_dict)
        # As duas visoes sobre as MESMAS series: 'series' plana (a lista de
        # sempre, que a tela de grupos conta e o escopo percorre) e 'paineis'
        # aninhada, que e o que a tela de detalhe desenha. Manter as duas
        # evita reescrever quem so quer contar.
        group_dict['paineis'] = _agrupar_em_paineis(
            conn, group_id, group_dict['series'])
        group_dict['scope'] = (
            'geral' if group_dict.get('scope_local_id') is None else 'local'
        )
        group_dict['scope_local_nome'] = _nome_do_local(
            conn, group_dict.get('scope_local_id'))
        group_dict['fora_do_escopo'] = sum(
            1 for s in group_dict['series'] if s['out_of_scope'])
        return group_dict


def get_all_comparison_groups() -> List[Dict]:
    """Retorna todos os grupos comparativos."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM comparison_groups ORDER BY name")
        groups = [dict(row) for row in cursor.fetchall()]

        for group in groups:
            group['series'] = _series_do_grupo(conn, group)
            # A lista so precisa da CONTAGEM de paineis no card; montar a
            # estrutura aninhada de cada grupo aqui seria trabalho jogado fora.
            group['total_paineis'] = len(_paineis_do_grupo(conn, group['id']))
            group['scope'] = (
                'geral' if group.get('scope_local_id') is None else 'local'
            )
            group['scope_local_nome'] = _nome_do_local(
                conn, group.get('scope_local_id'))
            group['fora_do_escopo'] = sum(
                1 for s in group['series'] if s['out_of_scope'])

        return groups


def add_series_to_group(group_id: int, device_id: str, dps_code: str,
                       label: str, panel_id: Optional[int] = None) -> int:
    """
    Adiciona uma série a um grupo, respeitando o escopo do grupo.

    Grupo com escopo de local só aceita dispositivo daquele local. A checagem
    e o INSERT ficam na mesma conexão e na mesma transação de propósito: em
    conexões separadas haveria janela entre verificar e gravar.

    Sem `panel_id`, a série cai no painel principal — é o que mantém quem
    chama sem saber que painéis existem. A ordem dela é o fim da fila DAQUELE
    painel, não do grupo.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id, name, scope_local_id FROM comparison_groups WHERE id = ?",
            (group_id,))
        grupo = cursor.fetchone()
        if grupo is None:
            raise NotFoundError("Grupo %s não encontrado" % group_id)

        cursor.execute("SELECT id, name, local_id FROM devices WHERE id = ?",
                       (device_id,))
        device = cursor.fetchone()
        if device is None:
            raise NotFoundError("Dispositivo %s não encontrado" % device_id)

        escopo = grupo['scope_local_id']
        if escopo is not None and device['local_id'] != escopo:
            raise ConflictError(
                "'%s' não pertence ao local deste grupo" % device['name'],
                device_id=device_id,
                device_local_id=device['local_id'],
                group_scope_local_id=escopo,
            )

        if panel_id is None:
            panel_id = _painel_principal_id(conn, group_id)
        else:
            _exige_painel(conn, group_id, panel_id)

        cursor.execute("""
            INSERT INTO comparison_series
            (group_id, device_id, dps_code, label, sort_order, panel_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (group_id, device_id, dps_code, label,
              _proxima_ordem_no_painel(conn, panel_id), panel_id))
        return cursor.lastrowid


def delete_comparison_group(group_id: int) -> None:
    """Deleta um grupo comparativo e suas séries."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM comparison_series WHERE group_id = ?",
                      (group_id,))
        cursor.execute("DELETE FROM comparison_panels WHERE group_id = ?",
                      (group_id,))
        cursor.execute("DELETE FROM comparison_groups WHERE id = ?", (group_id,))


def remove_series_from_group(series_id: int) -> None:
    """Remove uma série de um grupo."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM comparison_series WHERE id = ?", (series_id,))


# ---------------------------------------------------------------------------
# Hierarquia: locais e cômodos
# ---------------------------------------------------------------------------

def _placement_padrao(conn):
    """
    Onde um dispositivo novo entra: no local padrão e SEM cômodo.

    Sem cômodo de propósito — ele cai na caixa de entrada do local, à espera de
    atribuição, em vez de ser plantado num cômodo que ninguém escolheu. Recebe
    a conexão já aberta porque roda dentro do laço de importação, um
    dispositivo por vez; abrir conexão nova aqui multiplicaria por N.
    """
    row = conn.execute("SELECT id FROM locais WHERE nome = ?",
                       (DEFAULT_LOCAL_NAME,)).fetchone()
    if row is None:
        # Banco sem o local padrão (renomeado?): usa o primeiro que existir.
        row = conn.execute(
            "SELECT id FROM locais ORDER BY sort_order, id LIMIT 1").fetchone()
    return (row['id'], None) if row else (None, None)


def get_default_placement() -> tuple:
    with get_db() as conn:
        return _placement_padrao(conn)


def _exige_local(conn, local_id: int):
    row = conn.execute("SELECT * FROM locais WHERE id = ?", (local_id,)).fetchone()
    if row is None:
        raise NotFoundError("Local %s não encontrado" % local_id)
    return row


def _nome_do_local(conn, local_id):
    if local_id is None:
        return None
    row = conn.execute("SELECT nome FROM locais WHERE id = ?", (local_id,)).fetchone()
    return row['nome'] if row else None


def _nome_limpo(nome: str, campo: str = "nome") -> str:
    nome = (nome or "").strip()
    if not nome:
        raise ValidationError("O %s não pode ficar em branco" % campo)
    return nome


def _nome_ja_usado(conn, sql: str, params: tuple, nome: str) -> bool:
    """
    Compara nomes em Python, com casefold().

    O COLLATE NOCASE do SQLite só dobra ASCII: por ele "SÍTIO" e "Sítio"
    seriam locais diferentes. O índice único continua no banco como rede de
    segurança contra corrida; a mensagem amigável sai daqui. Acento continua
    sendo diferença de verdade — "Sitio" e "Sítio" são nomes distintos.
    """
    alvo = nome.casefold()
    return any(row[0].casefold() == alvo for row in conn.execute(sql, params))


def get_all_locais(with_counts: bool = True) -> List[Dict]:
    """Locais ordenados, com contagem de cômodos e dispositivos."""
    with get_db() as conn:
        if not with_counts:
            rows = conn.execute(
                "SELECT * FROM locais ORDER BY sort_order, nome COLLATE NOCASE"
            ).fetchall()
            return [dict(r) for r in rows]

        rows = conn.execute("""
            SELECT l.*,
                   (SELECT COUNT(*) FROM comodos c WHERE c.local_id = l.id)
                       AS total_comodos,
                   (SELECT COUNT(*) FROM devices d WHERE d.local_id = l.id)
                       AS total_devices,
                   (SELECT COUNT(*) FROM devices d
                     WHERE d.local_id = l.id AND d.comodo_id IS NULL)
                       AS total_sem_comodo
            FROM locais l
            ORDER BY l.sort_order, l.nome COLLATE NOCASE
        """).fetchall()
        return [dict(r) for r in rows]


def get_local(local_id: int) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM locais WHERE id = ?", (local_id,)).fetchone()
        return dict(row) if row else None


def create_local(nome: str, descricao: str = None, rede_cidr: str = None,
                 sort_order: int = 0) -> int:
    nome = _nome_limpo(nome)
    with get_db() as conn:
        if _nome_ja_usado(conn, "SELECT nome FROM locais", (), nome):
            raise ConflictError("Já existe um local chamado '%s'" % nome)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO locais (nome, descricao, rede_cidr, sort_order)
            VALUES (?, ?, ?, ?)
        """, (nome, descricao, rede_cidr, sort_order))
        return cursor.lastrowid


def update_local(local_id: int, nome: str = None, descricao=_NAO_INFORMADO,
                 rede_cidr=_NAO_INFORMADO, sort_order: int = None) -> None:
    with get_db() as conn:
        _exige_local(conn, local_id)
        if nome is not None:
            nome = _nome_limpo(nome)
            if _nome_ja_usado(conn, "SELECT nome FROM locais WHERE id != ?",
                              (local_id,), nome):
                raise ConflictError("Já existe um local chamado '%s'" % nome)
            conn.execute("UPDATE locais SET nome = ? WHERE id = ?", (nome, local_id))
        if descricao is not _NAO_INFORMADO:
            conn.execute("UPDATE locais SET descricao = ? WHERE id = ?",
                         (descricao, local_id))
        if rede_cidr is not _NAO_INFORMADO:
            conn.execute("UPDATE locais SET rede_cidr = ? WHERE id = ?",
                         (rede_cidr, local_id))
        if sort_order is not None:
            conn.execute("UPDATE locais SET sort_order = ? WHERE id = ?",
                         (sort_order, local_id))
        conn.execute("UPDATE locais SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (local_id,))


def delete_local(local_id: int) -> None:
    """
    Só apaga local vazio. As FKs não são aplicadas nesta conexão, então a
    integridade é garantida aqui — e apagar em cascata levaria junto leituras
    e séries que não teriam como voltar.
    """
    with get_db() as conn:
        _exige_local(conn, local_id)
        devices = conn.execute(
            "SELECT COUNT(*) FROM devices WHERE local_id = ?", (local_id,)
        ).fetchone()[0]
        comodos = conn.execute(
            "SELECT COUNT(*) FROM comodos WHERE local_id = ?", (local_id,)
        ).fetchone()[0]
        if devices or comodos:
            raise ConflictError(
                "Mova os %d dispositivos e apague os %d cômodos antes de excluir"
                % (devices, comodos),
                devices=devices, comodos=comodos,
            )
        if conn.execute("SELECT COUNT(*) FROM locais").fetchone()[0] <= 1:
            raise ConflictError("Precisa existir pelo menos um local")

        # Grupo apontando para o local excluído vira geral, em vez de ficar com
        # um id morto. Mesma regra do resto: degrada, não apaga.
        conn.execute(
            "UPDATE comparison_groups SET scope_local_id = NULL"
            " WHERE scope_local_id = ?", (local_id,))
        conn.execute("DELETE FROM locais WHERE id = ?", (local_id,))


def get_comodos_by_local(local_id: int) -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.*,
                   (SELECT COUNT(*) FROM devices d WHERE d.comodo_id = c.id)
                       AS total_devices
            FROM comodos c
            WHERE c.local_id = ?
            ORDER BY c.sort_order, c.nome COLLATE NOCASE
        """, (local_id,)).fetchall()
        return [dict(r) for r in rows]


def get_comodo(comodo_id: int) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM comodos WHERE id = ?",
                           (comodo_id,)).fetchone()
        return dict(row) if row else None


def create_comodo(local_id: int, nome: str, sort_order: int = 0) -> int:
    nome = _nome_limpo(nome)
    with get_db() as conn:
        _exige_local(conn, local_id)
        if _nome_ja_usado(conn, "SELECT nome FROM comodos WHERE local_id = ?",
                          (local_id,), nome):
            raise ConflictError("Este local já tem um cômodo '%s'" % nome)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO comodos (local_id, nome, sort_order) VALUES (?, ?, ?)",
            (local_id, nome, sort_order))
        return cursor.lastrowid


def update_comodo(comodo_id: int, nome: str = None, sort_order: int = None,
                  local_id: int = None) -> None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM comodos WHERE id = ?",
                           (comodo_id,)).fetchone()
        if row is None:
            raise NotFoundError("Cômodo %s não encontrado" % comodo_id)
        destino = row['local_id'] if local_id is None else local_id
        if local_id is not None:
            _exige_local(conn, local_id)

        if nome is not None:
            nome = _nome_limpo(nome)
            if _nome_ja_usado(
                conn, "SELECT nome FROM comodos WHERE local_id = ? AND id != ?",
                (destino, comodo_id), nome
            ):
                raise ConflictError("Este local já tem um cômodo '%s'" % nome)
            conn.execute("UPDATE comodos SET nome = ? WHERE id = ?",
                         (nome, comodo_id))

        if sort_order is not None:
            conn.execute("UPDATE comodos SET sort_order = ? WHERE id = ?",
                         (sort_order, comodo_id))

        if local_id is not None and local_id != row['local_id']:
            conn.execute("UPDATE comodos SET local_id = ? WHERE id = ?",
                         (local_id, comodo_id))
            # devices.local_id e denormalizado a partir de comodos.local_id.
            # Mover o comodo sem propagar deixaria o dispositivo aparecendo no
            # local errado e faria a checagem de escopo recusar serie valida.
            conn.execute("UPDATE devices SET local_id = ? WHERE comodo_id = ?",
                         (local_id, comodo_id))

        conn.execute("UPDATE comodos SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (comodo_id,))


def delete_comodo(comodo_id: int) -> None:
    """Apagar cômodo não apaga dispositivo: eles voltam para 'sem cômodo'."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM comodos WHERE id = ?",
                           (comodo_id,)).fetchone()
        if row is None:
            raise NotFoundError("Cômodo %s não encontrado" % comodo_id)
        conn.execute("UPDATE devices SET comodo_id = NULL WHERE comodo_id = ?",
                     (comodo_id,))
        conn.execute("DELETE FROM comodos WHERE id = ?", (comodo_id,))


def assign_device_placement(device_id: str, local_id: int,
                            comodo_id: Optional[int] = None) -> None:
    """
    Move um dispositivo para um local (e opcionalmente um cômodo).
    UPDATE isolado de propósito — nunca passa pelo UPSERT de importação.
    """
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM devices WHERE id = ?",
                        (device_id,)).fetchone() is None:
            raise NotFoundError("Dispositivo %s não encontrado" % device_id)
        _exige_local(conn, local_id)

        if comodo_id is not None:
            comodo = conn.execute("SELECT * FROM comodos WHERE id = ?",
                                  (comodo_id,)).fetchone()
            if comodo is None:
                raise NotFoundError("Cômodo %s não encontrado" % comodo_id)
            if comodo['local_id'] != local_id:
                raise ConflictError(
                    "O cômodo '%s' não pertence a esse local" % comodo['nome'])

        conn.execute(
            "UPDATE devices SET local_id = ?, comodo_id = ?,"
            " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (local_id, comodo_id, device_id))


# ---------------------------------------------------------------------------
# Status dos dispositivos: uma query só
# ---------------------------------------------------------------------------

_SQL_STATUS = """
    SELECT d.*,
           l.nome       AS local_nome,
           l.sort_order AS local_sort,
           c.nome       AS comodo_nome,
           c.sort_order AS comodo_sort,
           mc.enabled               AS cfg_enabled,
           mc.poll_interval_seconds AS cfg_poll_interval_seconds,
           r.id           AS rd_id,
           r.collected_at AS rd_collected_at,
           r.dps_json     AS rd_dps_json,
           r.online       AS rd_online,
           CASE WHEN r.online = 1
                 AND r.collected_at >= datetime('now',
                     '-' || MAX(?, COALESCE(mc.poll_interval_seconds, 0) * 2)
                         || ' seconds')
                THEN 1 ELSE 0 END AS is_online
    FROM devices d
    LEFT JOIN locais  l ON l.id = d.local_id
    LEFT JOIN comodos c ON c.id = d.comodo_id
    LEFT JOIN monitor_configs mc ON mc.device_id = d.id
    LEFT JOIN readings r ON r.id = (
        SELECT r2.id FROM readings r2
        WHERE r2.device_id = d.id
        ORDER BY r2.collected_at DESC, r2.id DESC
        LIMIT 1
    )
    WHERE (? = 1 OR COALESCE(d.source, 'cloud') != 'solar')
      AND (? IS NULL OR d.id = ?)
      AND (? IS NULL OR d.local_id = ?)
      AND (? IS NULL OR d.comodo_id = ?)
    ORDER BY l.sort_order, l.nome COLLATE NOCASE,
             c.sort_order, c.nome COLLATE NOCASE,
             d.name COLLATE NOCASE
"""

# Colunas do SELECT que não pertencem à tabela devices e são removidas antes
# de montar o dict de device.
_COLUNAS_EXTRA = (
    'local_nome', 'local_sort', 'comodo_nome', 'comodo_sort',
    'cfg_enabled', 'cfg_poll_interval_seconds',
    'rd_id', 'rd_collected_at', 'rd_dps_json', 'rd_online', 'is_online',
)


def _query_device_statuses(device_id: str = None, local_id: int = None,
                           comodo_id: int = None,
                           incluir_solar: bool = False) -> List[Dict]:
    """
    Devices + último reading + monitor config + local/cômodo, numa query.

    Substitui o laço que chamava get_device_status() por dispositivo (3–4
    conexões cada). O is_online é calculado no SQL com datetime('now', ...),
    que roda no mesmo relógio e no mesmo formato do CURRENT_TIMESTAMP que
    gravou collected_at — os dois em UTC. Comparar com datetime.now() do
    Python, como antes, dava diferença negativa em fuso a oeste de Greenwich
    e fazia tudo passar por online.

    A janela de online é POR DISPOSITIVO: o maior entre ONLINE_WINDOW_MINUTES
    e 2x o intervalo de polling. Com a janela fixa de 5 min, um inversor
    solar coletado a cada 5 min "piscaria" offline entre uma leitura e outra.

    Inversores solares só entram com incluir_solar=True — eles moram na seção
    /solar, não nas telas de dispositivos e locais.
    """
    janela_segundos = ONLINE_WINDOW_MINUTES * 60
    with get_db() as conn:
        # Dispositivos importados antes desta versão podem não ter config.
        # INSERT OR IGNORE + PK em device_id torna isso no-op depois da 1a vez.
        conn.execute("""
            INSERT OR IGNORE INTO monitor_configs
            (device_id, enabled, poll_interval_seconds)
            SELECT id, 1, ? FROM devices
        """, (DEFAULT_POLL_INTERVAL_SECONDS,))

        rows = conn.execute(_SQL_STATUS, (
            janela_segundos,
            1 if incluir_solar else 0,
            device_id, device_id,
            local_id, local_id,
            comodo_id, comodo_id,
        )).fetchall()

    resultado = []
    for row in rows:
        bruto = dict(row)
        device = {k: v for k, v in bruto.items() if k not in _COLUNAS_EXTRA}

        reading = None
        if bruto['rd_id'] is not None:
            reading = {
                'id': bruto['rd_id'],
                'device_id': device['id'],
                'collected_at': bruto['rd_collected_at'],
                'dps_json': bruto['rd_dps_json'],
                'online': bruto['rd_online'],
            }

        resultado.append({
            'device': device,
            'reading': reading,
            'config': {
                'device_id': device['id'],
                'enabled': bruto['cfg_enabled'] if bruto['cfg_enabled'] is not None else 0,
                'poll_interval_seconds': (
                    bruto['cfg_poll_interval_seconds']
                    if bruto['cfg_poll_interval_seconds'] is not None
                    else DEFAULT_POLL_INTERVAL_SECONDS
                ),
            },
            'is_online': bool(bruto['is_online']),
            'local': (
                {'id': device['local_id'], 'nome': bruto['local_nome']}
                if bruto['local_nome'] is not None else None
            ),
            'comodo': (
                {'id': device['comodo_id'], 'nome': bruto['comodo_nome']}
                if bruto['comodo_nome'] is not None else None
            ),
        })
    return resultado


def get_all_device_statuses(local_id: int = None, comodo_id: int = None,
                            incluir_solar: bool = False) -> List[Dict]:
    return _query_device_statuses(local_id=local_id, comodo_id=comodo_id,
                                  incluir_solar=incluir_solar)


def get_devices_grouped_by_local(local_id: int = None) -> List[Dict]:
    """
    Agrupa os status por local e cômodo, em memória — a query já vem ordenada,
    então não custa consulta nenhuma a mais. Locais e cômodos vazios também
    aparecem, por isso o merge com get_all_locais/get_comodos_by_local.
    """
    statuses = _query_device_statuses(local_id=local_id)

    por_local = {}
    for st in statuses:
        chave = st['device'].get('local_id')
        por_local.setdefault(chave, []).append(st)

    saida = []
    for local in get_all_locais():
        if local_id is not None and local['id'] != local_id:
            continue
        do_local = por_local.pop(local['id'], [])
        por_comodo = {}
        for st in do_local:
            por_comodo.setdefault(st['device'].get('comodo_id'), []).append(st)

        comodos = []
        for comodo in get_comodos_by_local(local['id']):
            comodos.append({
                'comodo': comodo,
                'devices': por_comodo.pop(comodo['id'], []),
            })

        sem_comodo = por_comodo.pop(None, [])
        # comodo_id apontando para cômodo inexistente (FKs não são aplicadas):
        # o dispositivo não some, cai no balde de sem cômodo.
        for restante in por_comodo.values():
            sem_comodo.extend(restante)

        saida.append({
            'local': local,
            'comodos': comodos,
            'sem_comodo': sem_comodo,
            'total_devices': len(do_local),
            'online': sum(1 for st in do_local if st['is_online']),
            'offline': sum(1 for st in do_local
                           if not st['is_online'] and st['config']['enabled']),
            'nao_monitorados': sum(1 for st in do_local
                                   if not st['config']['enabled']),
        })

    # Dispositivo com local_id nulo ou apontando para local inexistente.
    orfaos = [st for lista in por_local.values() for st in lista]
    if orfaos and local_id is None:
        saida.append({
            'local': {'id': None, 'nome': 'Sem local'},
            'comodos': [],
            'sem_comodo': orfaos,
            'total_devices': len(orfaos),
            'online': sum(1 for st in orfaos if st['is_online']),
            'offline': sum(1 for st in orfaos
                           if not st['is_online'] and st['config']['enabled']),
            'nao_monitorados': sum(1 for st in orfaos
                                   if not st['config']['enabled']),
        })
    return saida


# ---------------------------------------------------------------------------
# Painéis: os gráficos dentro de um grupo
#
# Um grupo era um gráfico só, e um eixo Y só serve a UMA grandeza — potência em
# W e geração em kWh no mesmo gráfico deixam a curva de kWh colada no chão.
# Cada painel é um gráfico com o eixo dele.
#
# Duas invariantes valem SEMPRE, e valem aqui e não na tela, porque um POST
# direto na API tem que esbarrar nelas igual:
#
#   1. todo grupo tem pelo menos um painel;
#   2. exatamente um painel do grupo tem principal = 1.
#
# E uma regra de postura, a mesma que o escopo já segue: mexer no arranjo
# nunca apaga série. Excluir um painel MOVE as séries dele para o principal.
# ---------------------------------------------------------------------------

def _paineis_do_grupo(conn, group_id: int) -> List[Dict]:
    """
    Os painéis de um grupo, na ordem em que a tela os desenha.

    O principal vem primeiro sempre — é o gráfico que abre a página. Entre os
    secundários vale o sort_order, com o id de desempate para a ordem nunca
    depender de sorte.
    """
    return [dict(r) for r in conn.execute(
        """SELECT * FROM comparison_panels WHERE group_id = ?
           ORDER BY principal DESC, sort_order, id""", (group_id,))]


def _painel_principal_id(conn, group_id: int) -> Optional[int]:
    """
    O painel principal do grupo.

    O fallback para o primeiro por ordem não é decoração: um grupo criado
    antes da migração, ou um principal apagado fora do app, não pode deixar as
    séries sem destino.
    """
    row = conn.execute(
        """SELECT id FROM comparison_panels WHERE group_id = ?
           ORDER BY principal DESC, sort_order, id LIMIT 1""",
        (group_id,)).fetchone()
    return row[0] if row else None


def _exige_painel(conn, group_id: int, painel_id: int) -> Dict:
    """
    O painel, contanto que ele seja DESTE grupo.

    A checagem de dono é o que impede mover uma série para o painel de outro
    grupo passando o id na mão — a tela nunca ofereceria isso, a API sim.
    """
    row = conn.execute(
        "SELECT * FROM comparison_panels WHERE id = ? AND group_id = ?",
        (painel_id, group_id)).fetchone()
    if row is None:
        raise NotFoundError("Painel %s não encontrado neste grupo" % painel_id)
    return dict(row)


def _exige_serie(conn, group_id: int, series_id: int) -> Dict:
    """A série, contanto que ela seja deste grupo. Mesma razão de _exige_painel."""
    row = conn.execute(
        "SELECT * FROM comparison_series WHERE id = ? AND group_id = ?",
        (series_id, group_id)).fetchone()
    if row is None:
        raise NotFoundError("Série %s não encontrada neste grupo" % series_id)
    return dict(row)


def _proxima_ordem_no_painel(conn, painel_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM comparison_series"
        " WHERE panel_id = ?", (painel_id,)).fetchone()
    return row[0]


def _agrupar_em_paineis(conn, group_id: int, series: List[Dict]) -> List[Dict]:
    """
    A lista plana de séries distribuída nos painéis do grupo.

    Série com panel_id nulo cai no principal. Isso é defensivo — a migração
    005 preencheu todas —, mas uma série órfã sumindo da tela seria a pior
    forma possível de descobrir que o vínculo se perdeu.
    """
    paineis = _paineis_do_grupo(conn, group_id)
    if not paineis:
        return []

    por_id = {p["id"]: p for p in paineis}
    for painel in paineis:
        painel["series"] = []

    principal = por_id[_painel_principal_id(conn, group_id)]
    for serie in series:
        destino = por_id.get(serie.get("panel_id")) or principal
        destino["series"].append(serie)
    return paineis


def create_panel(group_id: int, nome: str) -> Dict:
    """Cria um painel secundário no grupo. O nome é único dentro do grupo."""
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM comparison_groups WHERE id = ?",
                        (group_id,)).fetchone() is None:
            raise NotFoundError("Grupo %s não encontrado" % group_id)
        nome = _nome_limpo(nome, "nome do painel")
        ordem = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM comparison_panels"
            " WHERE group_id = ?", (group_id,)).fetchone()[0]
        painel_id = conn.execute(
            """INSERT INTO comparison_panels (group_id, nome, principal, sort_order)
               VALUES (?, ?, 0, ?)""", (group_id, nome, ordem)).lastrowid
        return _exige_painel(conn, group_id, painel_id)


def update_panel(group_id: int, painel_id: int, nome: str = None,
                 principal: bool = None) -> Dict:
    """
    Renomeia o painel e/ou o torna principal.

    Promover um painel rebaixa o anterior NA MESMA transação — é isso que
    mantém a invariante de um principal por grupo. Não há como "desmarcar" o
    principal: alguém tem que abrir a página.
    """
    with get_db() as conn:
        _exige_painel(conn, group_id, painel_id)

        if nome is not None:
            conn.execute("UPDATE comparison_panels SET nome = ? WHERE id = ?",
                         (_nome_limpo(nome, "nome do painel"), painel_id))

        if principal:
            conn.execute(
                "UPDATE comparison_panels SET principal = 0 WHERE group_id = ?",
                (group_id,))
            conn.execute(
                "UPDATE comparison_panels SET principal = 1 WHERE id = ?",
                (painel_id,))

        return _exige_painel(conn, group_id, painel_id)


def delete_panel(group_id: int, painel_id: int) -> Dict:
    """
    Exclui um painel e MOVE as séries dele para o principal.

    Nenhuma série é apagada aqui, de propósito: rearranjar gráficos e perder
    histórico são coisas diferentes, e a segunda nunca deve ser efeito
    colateral da primeira. Excluir o principal promove o próximo antes de
    mover — e o último painel de um grupo não pode ser excluído, senão as
    séries ficariam sem destino.
    """
    with get_db() as conn:
        _exige_painel(conn, group_id, painel_id)
        restantes = [p for p in _paineis_do_grupo(conn, group_id)
                     if p["id"] != painel_id]
        if not restantes:
            raise ValidationError(
                "Este é o único painel do grupo — um grupo precisa de pelo "
                "menos um gráfico. Crie outro painel antes de excluir este.")

        destino = restantes[0]
        conn.execute("UPDATE comparison_panels SET principal = 1 WHERE id = ?",
                     (destino["id"],))
        cur = conn.execute(
            "UPDATE comparison_series SET panel_id = ? WHERE panel_id = ?",
            (destino["id"], painel_id))
        conn.execute("DELETE FROM comparison_panels WHERE id = ?", (painel_id,))
        return {"deleted": True, "series_movidas": cur.rowcount,
                "destino_id": destino["id"], "destino_nome": destino["nome"]}


def reordenar_paineis(group_id: int, ordem: List[int]) -> List[Dict]:
    """
    Aplica a ordem dos painéis de uma vez.

    Recebe a lista inteira, e não um "sobe um": a reordenação vira uma escrita
    só, idempotente, sem estado intermediário se alguém clicar rápido. Ids que
    não são do grupo são recusados; painéis fora da lista vão para o fim, na
    ordem que já tinham.
    """
    with get_db() as conn:
        atuais = _paineis_do_grupo(conn, group_id)
        if not atuais:
            raise NotFoundError("Grupo %s não encontrado" % group_id)

        conhecidos = {p["id"] for p in atuais}
        vistos, fila = set(), []
        for painel_id in ordem:
            painel_id = int(painel_id)
            if painel_id not in conhecidos:
                raise NotFoundError(
                    "Painel %s não encontrado neste grupo" % painel_id)
            if painel_id not in vistos:
                vistos.add(painel_id)
                fila.append(painel_id)
        fila += [p["id"] for p in atuais if p["id"] not in vistos]

        for posicao, painel_id in enumerate(fila):
            conn.execute(
                "UPDATE comparison_panels SET sort_order = ? WHERE id = ?",
                (posicao, painel_id))
        return _paineis_do_grupo(conn, group_id)


def update_series(group_id: int, series_id: int, label: str = None,
                  panel_id=_NAO_INFORMADO) -> Dict:
    """
    Renomeia a série e/ou a move para outro painel.

    Mudar de painel joga a série para o fim da fila do destino — inserir no
    meio seria adivinhar onde ela deve ficar, e reordenar já existe para isso.
    """
    with get_db() as conn:
        _exige_serie(conn, group_id, series_id)

        if label is not None:
            conn.execute("UPDATE comparison_series SET label = ? WHERE id = ?",
                         (_nome_limpo(label, "rótulo da série"), series_id))

        if panel_id is not _NAO_INFORMADO:
            destino = (_painel_principal_id(conn, group_id) if panel_id is None
                       else _exige_painel(conn, group_id, int(panel_id))["id"])
            conn.execute(
                "UPDATE comparison_series SET panel_id = ?, sort_order = ?"
                " WHERE id = ?",
                (destino, _proxima_ordem_no_painel(conn, destino), series_id))

        return _exige_serie(conn, group_id, series_id)


def reordenar_series(group_id: int, ordem: List[int]) -> None:
    """
    Aplica a ordem das séries de uma vez, como reordenar_paineis.

    A ordem é global no grupo, não por painel: o SELECT de _series_do_grupo
    ordena por sort_order antes de distribuir nos painéis, e a ordem relativa
    dentro de cada painel é preservada por consequência.
    """
    with get_db() as conn:
        conhecidos = {row[0] for row in conn.execute(
            "SELECT id FROM comparison_series WHERE group_id = ?", (group_id,))}
        vistos, fila = set(), []
        for series_id in ordem:
            series_id = int(series_id)
            if series_id not in conhecidos:
                raise NotFoundError(
                    "Série %s não encontrada neste grupo" % series_id)
            if series_id not in vistos:
                vistos.add(series_id)
                fila.append(series_id)
        fila += sorted(conhecidos - vistos)

        for posicao, series_id in enumerate(fila):
            conn.execute(
                "UPDATE comparison_series SET sort_order = ? WHERE id = ?",
                (posicao, series_id))


# ---------------------------------------------------------------------------
# Escopo dos grupos comparativos
# ---------------------------------------------------------------------------

def _series_do_grupo(conn, grupo: Dict) -> List[Dict]:
    """
    Séries com o dispositivo já resolvido, e a marca de fora do escopo.

    Série fora do escopo é MARCADA, nunca apagada — trocar o escopo de um
    grupo não pode fazer histórico sumir. device_missing cobre o id pendurado,
    já que as FKs não são aplicadas.
    """
    rows = conn.execute("""
        SELECT s.*,
               d.name     AS device_name,
               d.local_id AS device_local_id,
               l.nome     AS device_local_nome,
               c.nome     AS device_comodo_nome
        FROM comparison_series s
        LEFT JOIN devices d ON d.id = s.device_id
        LEFT JOIN comodos c ON c.id = d.comodo_id
        LEFT JOIN locais  l ON l.id = d.local_id
        WHERE s.group_id = ?
        ORDER BY s.sort_order, s.id
    """, (grupo['id'],)).fetchall()

    escopo = grupo.get('scope_local_id')
    series = []
    for row in rows:
        item = dict(row)
        item['device_missing'] = item['device_name'] is None
        item['out_of_scope'] = (
            escopo is not None and item['device_local_id'] != escopo
        )
        series.append(item)
    return series


def update_comparison_group(group_id: int, name: str = None,
                            description=_NAO_INFORMADO,
                            scope_local_id=_NAO_INFORMADO) -> Dict:
    """
    Edita nome/descrição/escopo. Não toca em comparison_series: mudar o escopo
    só muda quem passa a estar marcado como fora dele.
    """
    with get_db() as conn:
        grupo = conn.execute("SELECT * FROM comparison_groups WHERE id = ?",
                             (group_id,)).fetchone()
        if grupo is None:
            raise NotFoundError("Grupo %s não encontrado" % group_id)

        if name is not None:
            conn.execute("UPDATE comparison_groups SET name = ? WHERE id = ?",
                         (_nome_limpo(name), group_id))
        if description is not _NAO_INFORMADO:
            conn.execute(
                "UPDATE comparison_groups SET description = ? WHERE id = ?",
                (description, group_id))
        if scope_local_id is not _NAO_INFORMADO:
            if scope_local_id is not None:
                _exige_local(conn, scope_local_id)
            conn.execute(
                "UPDATE comparison_groups SET scope_local_id = ? WHERE id = ?",
                (scope_local_id, group_id))

        atualizado = dict(conn.execute(
            "SELECT * FROM comparison_groups WHERE id = ?", (group_id,)).fetchone())
        atualizado['series'] = _series_do_grupo(conn, atualizado)
        atualizado['fora_do_escopo'] = sum(
            1 for s in atualizado['series'] if s['out_of_scope'])
        return atualizado


def get_devices_for_group_scope(group_id: int) -> List[Dict]:
    """Dispositivos que o grupo aceita: todos, ou só os do local do escopo."""
    with get_db() as conn:
        grupo = conn.execute(
            "SELECT scope_local_id FROM comparison_groups WHERE id = ?",
            (group_id,)).fetchone()
        if grupo is None:
            raise NotFoundError("Grupo %s não encontrado" % group_id)
        escopo = grupo['scope_local_id']

    # incluir_solar: os canais do inversor são exatamente o tipo de série que
    # um grupo de energia compara — a exclusão das telas de dispositivos não
    # vale aqui.
    return _query_device_statuses(local_id=escopo, incluir_solar=True)


def device_is_in_scope(group_id: int, device_id: str) -> bool:
    with get_db() as conn:
        grupo = conn.execute(
            "SELECT scope_local_id FROM comparison_groups WHERE id = ?",
            (group_id,)).fetchone()
        if grupo is None:
            raise NotFoundError("Grupo %s não encontrado" % group_id)
        if grupo['scope_local_id'] is None:
            return True
        device = conn.execute("SELECT local_id FROM devices WHERE id = ?",
                              (device_id,)).fetchone()
        if device is None:
            raise NotFoundError("Dispositivo %s não encontrado" % device_id)
        return device['local_id'] == grupo['scope_local_id']

# ---------------------------------------------------------------------------
# Energia solar: integrações e inversores
#
# Uma integração é uma conta/planta num fabricante (driver em app/solar/).
# O inversor é uma linha comum em devices (source='solar') — é o que faz
# grupos de energia, gráficos e is_online funcionarem de graça — e
# solar_inversores guarda só o vínculo com a integração e o serial na API.
#
# credenciais_json circula por aqui porque o coletor precisa dele. As ROTAS
# é que nunca podem devolvê-lo — e ninguém pode logá-lo.
# ---------------------------------------------------------------------------

def get_solar_integracoes() -> List[Dict]:
    """Todas as integrações, com a contagem de inversores de cada uma."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT i.*, COUNT(si.device_id) AS total_inversores
            FROM solar_integracoes i
            LEFT JOIN solar_inversores si ON si.integracao_id = i.id
            GROUP BY i.id
            ORDER BY i.nome COLLATE NOCASE
        """).fetchall()
        return [dict(r) for r in rows]


def get_solar_integracao(integracao_id: int) -> Dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM solar_integracoes WHERE id = ?",
                           (integracao_id,)).fetchone()
        if row is None:
            raise NotFoundError("Integração solar %s não encontrada"
                                % integracao_id)
        return dict(row)


def create_solar_integracao(driver: str, nome: str, credenciais_json: str,
                            planta_apikey: str = None,
                            planta_nome: str = None,
                            local_id: int = None,
                            poll_interval_seconds: int =
                                SOLAR_POLL_INTERVAL_SECONDS,
                            nivel_acesso: str = "pro") -> int:
    """
    Os parâmetros de coleta (local, intervalo) são DA INTEGRAÇÃO — os
    inversores herdam deles no discover. Pode haver quantas integrações do
    mesmo fabricante se quiser (duas contas, duas plantas); o que é recusado
    é a MESMA planta duas vezes, que duplicaria toda leitura.
    """
    nome = (nome or "").strip()
    if not nome:
        raise ValidationError("Informe um nome para a integração")
    with get_db() as conn:
        if planta_apikey:
            ja_existe = conn.execute(
                "SELECT 1 FROM solar_integracoes WHERE driver = ?"
                " AND planta_apikey = ?", (driver, planta_apikey)).fetchone()
            if ja_existe:
                raise ConflictError(
                    "Esta planta já está configurada em outra integração")
        cur = conn.execute("""
            INSERT INTO solar_integracoes
                (driver, nome, credenciais_json, planta_apikey, planta_nome,
                 local_id, poll_interval_seconds, nivel_acesso)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (driver, nome, credenciais_json, planta_apikey, planta_nome,
              local_id, max(60, int(poll_interval_seconds or
                                    SOLAR_POLL_INTERVAL_SECONDS)),
              (nivel_acesso or "pro").strip().lower()))
        return cur.lastrowid


def update_solar_integracao(integracao_id: int, nome: str = None,
                            enabled: bool = None,
                            local_id: int = _NAO_INFORMADO,
                            poll_interval_seconds: int = None) -> None:
    get_solar_integracao(integracao_id)
    with get_db() as conn:
        if local_id is not _NAO_INFORMADO:
            conn.execute("UPDATE solar_integracoes SET local_id = ?,"
                         " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (local_id, integracao_id))
            # Os inversores acompanham o local da integração.
            conn.execute("""
                UPDATE devices SET local_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN (SELECT device_id FROM solar_inversores
                             WHERE integracao_id = ?)
            """, (local_id, integracao_id))
        if poll_interval_seconds is not None:
            intervalo = max(60, int(poll_interval_seconds))
            conn.execute("UPDATE solar_integracoes SET poll_interval_seconds"
                         " = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (intervalo, integracao_id))
            conn.execute("""
                UPDATE monitor_configs SET poll_interval_seconds = ?,
                       updated_at = CURRENT_TIMESTAMP
                WHERE device_id IN (SELECT device_id FROM solar_inversores
                                    WHERE integracao_id = ?)
            """, (intervalo, integracao_id))
        if nome is not None:
            nome = nome.strip()
            if not nome:
                raise ValidationError("O nome da integração não pode ser vazio")
            conn.execute("UPDATE solar_integracoes SET nome = ?,"
                         " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (nome, integracao_id))
        if enabled is not None:
            conn.execute("UPDATE solar_integracoes SET enabled = ?,"
                         " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (1 if enabled else 0, integracao_id))
            # Liga/desliga a coleta de todos os inversores da integração de
            # uma vez — é o toggle da integração, não o de cada aparelho.
            conn.execute("""
                UPDATE monitor_configs SET enabled = ?,
                       updated_at = CURRENT_TIMESTAMP
                WHERE device_id IN
                    (SELECT device_id FROM solar_inversores
                     WHERE integracao_id = ?)
            """, (1 if enabled else 0, integracao_id))


def delete_solar_integracao(integracao_id: int) -> Dict:
    """
    Remove a integração e TUDO dos inversores dela: devices, configs,
    leituras. As séries de grupos de energia que apontavam para eles ficam —
    marcadas como device_missing, o mesmo tratamento de qualquer device
    apagado (nunca apagar série dos outros por efeito colateral).
    """
    get_solar_integracao(integracao_id)
    with get_db() as conn:
        ids = [r["device_id"] for r in conn.execute(
            "SELECT device_id FROM solar_inversores WHERE integracao_id = ?",
            (integracao_id,))]
        for device_id in ids:
            conn.execute("DELETE FROM readings WHERE device_id = ?", (device_id,))
            conn.execute("DELETE FROM monitor_configs WHERE device_id = ?",
                         (device_id,))
            conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        conn.execute("DELETE FROM solar_inversores WHERE integracao_id = ?",
                     (integracao_id,))
        conn.execute("DELETE FROM solar_integracoes WHERE id = ?",
                     (integracao_id,))
        return {"inversores_removidos": len(ids)}


def criar_inversor_solar(integracao_id: int, sn: str, nome: str = None,
                         mapping_json: str = None, psn: str = "") -> str:
    """
    Materializa um inversor descoberto: device + monitor_config + vínculo.
    Local e intervalo vêm DA INTEGRAÇÃO — são parâmetros dela.

    Idempotente por sn — o re-discover atualiza mapping/psn sem tocar em
    nome, local e intervalo já existentes, que são escolhas do usuário. O
    monitor_config nasce AQUI com o intervalo certo, antes que o auto-enroll
    de _query_device_statuses crie um com o default de 60 s.
    """
    integracao = get_solar_integracao(integracao_id)
    local_id = integracao.get("local_id")
    poll_interval = (integracao.get("poll_interval_seconds")
                     or SOLAR_POLL_INTERVAL_SECONDS)
    sn = (sn or "").strip()
    if not sn:
        raise ValidationError("Inversor sem número de série")
    # O id é prefixado pelo DRIVER, não pelo fabricante comercial: duas
    # integrações do mesmo fabricante não colidem porque o sn é único.
    device_id = "%s-%s" % (integracao["driver"], sn)

    with get_db() as conn:
        conn.execute("""
            INSERT INTO devices
                (id, name, local_key, category, product_name, mapping_json,
                 ip, local_id, source, last_seen_at)
            VALUES (?, ?, NULL, 'solar', ?, ?, NULL, ?, 'solar',
                    CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                mapping_json = excluded.mapping_json,
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
        """, (device_id, (nome or "").strip() or "Inversor %s" % sn,
              integracao["nome"], mapping_json, local_id))
        conn.execute("""
            INSERT OR IGNORE INTO monitor_configs
                (device_id, enabled, poll_interval_seconds)
            VALUES (?, 1, ?)
        """, (device_id, poll_interval))
        conn.execute("""
            INSERT INTO solar_inversores (device_id, integracao_id, sn, psn)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET psn = excluded.psn
        """, (device_id, integracao_id, sn, psn or ""))
    return device_id


def get_solar_inversores(integracao_id: int = None) -> List[Dict]:
    """Vínculos inversor/integração (sem status — ver rota /solar)."""
    with get_db() as conn:
        sql = """
            SELECT si.*, i.driver, i.nome AS integracao_nome
            FROM solar_inversores si
            JOIN solar_integracoes i ON i.id = si.integracao_id
        """
        params = ()
        if integracao_id is not None:
            sql += " WHERE si.integracao_id = ?"
            params = (integracao_id,)
        return [dict(r) for r in conn.execute(sql + " ORDER BY si.sn", params)]


def get_config_coleta_solar(device_id: str) -> Optional[Dict]:
    """O que o coletor precisa para ler um inversor: driver + credenciais."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT si.sn, si.integracao_id, i.driver, i.credenciais_json,
                   i.planta_apikey, i.nivel_acesso, i.enabled
            FROM solar_inversores si
            JOIN solar_integracoes i ON i.id = si.integracao_id
            WHERE si.device_id = ?
        """, (device_id,)).fetchone()
        return dict(row) if row else None


def get_integracoes_backfill_pendente() -> List[Dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM solar_integracoes
            WHERE enabled = 1 AND backfill_feito = 0
        """).fetchall()
        return [dict(r) for r in rows]


def marcar_backfill_feito(integracao_id: int) -> None:
    with get_db() as conn:
        conn.execute("UPDATE solar_integracoes SET backfill_feito = 1,"
                     " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (integracao_id,))


def tmstps_do_device(device_id: str) -> set:
    """
    Todos os tmstp já gravados de um inversor. O backfill roda de novo se
    falhar no meio (só marca backfill_feito no fim) — sem este conjunto, a
    segunda passada duplicaria os pontos que a primeira já gravou.
    """
    saida = set()
    with get_db() as conn:
        for row in conn.execute(
                "SELECT dps_json FROM readings WHERE device_id = ?"
                " AND online = 1", (device_id,)):
            try:
                tmstp = json.loads(row["dps_json"]).get("tmstp")
                if tmstp is not None:
                    saida.add(int(tmstp))
            except (ValueError, TypeError):
                continue
    return saida


def ultima_leitura_tmstp(device_id: str) -> Optional[int]:
    """
    O tmstp (epoch ms do equipamento) da última leitura solar — o coletor
    compara com o da telemetria nova e pula a gravação se for o mesmo:
    polling de 5 em 5 min num equipamento que mede de 5 em 5 min encosta na
    mesma medição duas vezes com frequência.
    """
    with get_db() as conn:
        row = conn.execute("""
            SELECT dps_json FROM readings
            WHERE device_id = ? AND online = 1
            ORDER BY collected_at DESC, id DESC LIMIT 1
        """, (device_id,)).fetchone()
    if row is None:
        return None
    try:
        tmstp = json.loads(row["dps_json"]).get("tmstp")
        return int(tmstp) if tmstp is not None else None
    except (ValueError, TypeError):
        return None



# ---------------------------------------------------------------------------
# Acionamento: opt-in por dispositivo e auditoria de comandos
# ---------------------------------------------------------------------------

def _exige_device(conn, device_id: str) -> Dict:
    row = conn.execute("SELECT * FROM devices WHERE id = ?",
                       (device_id,)).fetchone()
    if row is None:
        raise NotFoundError("Dispositivo %s não encontrado" % device_id)
    return dict(row)


def set_acionavel(device_id: str, acionavel: bool) -> Dict:
    """
    Liga ou desliga o acionamento DESTE dispositivo.

    É o opt-in: nada nasce acionável, nem depois de um scan. Desligar a chave
    não desfaz nada no aparelho — só tira o botão da tela e faz a rota de
    comando recusar.
    """
    with get_db() as conn:
        _exige_device(conn, device_id)
        conn.execute("UPDATE devices SET acionavel = ?,"
                     " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (1 if acionavel else 0, device_id))
        return _exige_device(conn, device_id)


def set_confirmar_acao(device_id: str, confirmar: bool) -> Dict:
    """Pedir (ou não) o diálogo de confirmação antes de cada comando."""
    with get_db() as conn:
        _exige_device(conn, device_id)
        conn.execute("UPDATE devices SET confirmar_acao = ?,"
                     " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (1 if confirmar else 0, device_id))
        return _exige_device(conn, device_id)


def set_tipo_manual(device_id: str, tipo: Optional[str]) -> Dict:
    """
    Fixa o tipo à mão. `None` volta a valer o derivado da categoria/mapping —
    que é o normal; isto existe para o aparelho que a categoria classifica mal.
    """
    with get_db() as conn:
        _exige_device(conn, device_id)
        conn.execute("UPDATE devices SET tipo_manual = ?,"
                     " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (tipo or None, device_id))
        return _exige_device(conn, device_id)


def registrar_comando(device_id: str, dp: str, code: str = None,
                      valor=None, transporte: str = None, ok: bool = False,
                      erro: str = None, origem: str = "painel") -> None:
    """
    Grava o comando no log — sucesso E falha.

    A falha importa tanto quanto o acerto: "mandei desligar a bomba e não
    aconteceu nada" só é diagnosticável se ficou registrado que a tentativa
    existiu e qual foi o erro.
    """
    with get_db() as conn:
        conn.execute("""
            INSERT INTO command_log
            (device_id, dp, code, valor_json, transporte, origem, ok, erro)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (device_id, str(dp), code, json.dumps(valor), transporte,
              origem, 1 if ok else 0, erro))


def ultimos_comandos(device_id: str, limite: int = 10) -> List[Dict]:
    """As últimas ações enviadas a um dispositivo, mais recente primeiro."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM command_log WHERE device_id = ?
            ORDER BY created_at DESC, id DESC LIMIT ?
        """, (device_id, limite)).fetchall()
    saida = []
    for row in rows:
        item = dict(row)
        try:
            item["valor"] = json.loads(item["valor_json"] or "null")
        except (ValueError, TypeError):
            item["valor"] = None
        saida.append(item)
    return saida


# ---------------------------------------------------------------------------
# Câmeras: o vínculo de vídeo de um dispositivo
# ---------------------------------------------------------------------------

def upsert_camera(device_id: str, driver: str, host: str = None,
                  porta: int = None, credenciais_json: str = None,
                  perfil_token: str = None, perfil_nome: str = None,
                  snapshot_uri: str = None, stream_uri: str = None,
                  enabled: bool = True) -> Dict:
    """
    Cria ou atualiza o vínculo de vídeo. Reconfigurar uma câmera é o caso
    comum (mudou a senha, trocou o perfil), então não há create separado.
    """
    with get_db() as conn:
        _exige_device(conn, device_id)
        conn.execute("""
            INSERT INTO cameras (device_id, driver, host, porta,
                credenciais_json, perfil_token, perfil_nome, snapshot_uri,
                stream_uri, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                driver = excluded.driver,
                host = excluded.host,
                porta = excluded.porta,
                credenciais_json = COALESCE(excluded.credenciais_json,
                                            cameras.credenciais_json),
                perfil_token = excluded.perfil_token,
                perfil_nome = excluded.perfil_nome,
                snapshot_uri = excluded.snapshot_uri,
                stream_uri = excluded.stream_uri,
                enabled = excluded.enabled,
                updated_at = CURRENT_TIMESTAMP
        """, (device_id, driver, host, porta, credenciais_json, perfil_token,
              perfil_nome, snapshot_uri, stream_uri, 1 if enabled else 0))
        return dict(conn.execute("SELECT * FROM cameras WHERE device_id = ?",
                                 (device_id,)).fetchone())


def get_camera(device_id: str) -> Optional[Dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cameras WHERE device_id = ?",
                           (device_id,)).fetchone()
        return dict(row) if row else None


def get_cameras(apenas_ativas: bool = False) -> List[Dict]:
    """Câmeras configuradas, com o nome e o local do dispositivo junto."""
    sql = """
        SELECT c.*, d.name AS device_nome, d.ip AS device_ip,
               d.local_id, l.nome AS local_nome
        FROM cameras c
        JOIN devices d ON d.id = c.device_id
        LEFT JOIN locais l ON l.id = d.local_id
        WHERE (? = 0 OR c.enabled = 1)
        ORDER BY d.name COLLATE NOCASE
    """
    with get_db() as conn:
        return [dict(r) for r in conn.execute(sql,
                                              (1 if apenas_ativas else 0,))]


def delete_camera(device_id: str) -> None:
    """
    Remove só o vínculo de vídeo. O dispositivo continua no inventário, com
    a coleta de DPs intacta — desconfigurar a imagem não é apagar a câmera.
    """
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM cameras WHERE device_id = ?",
                        (device_id,)).fetchone() is None:
            raise NotFoundError("Câmera %s não configurada" % device_id)
        conn.execute("DELETE FROM cameras WHERE device_id = ?", (device_id,))
