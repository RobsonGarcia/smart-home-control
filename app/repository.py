import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from app.config import (
    DEFAULT_LOCAL_NAME,
    DEFAULT_POLL_INTERVAL_SECONDS,
    ONLINE_WINDOW_MINUTES,
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

        cursor.execute("""
            INSERT INTO devices
            (id, name, local_key, category, product_name, model, mapping_json,
             is_sub, parent_id, ip, protocol_version, source, created_at, updated_at,
             local_id, comodo_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                local_key=excluded.local_key,
                category=excluded.category,
                product_name=excluded.product_name,
                model=excluded.model,
                mapping_json=excluded.mapping_json,
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
            json.dumps(device_data.get('mapping', {})),
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


def insert_reading(device_id: str, dps_json: str, online: bool) -> None:
    """Insere uma leitura de status do dispositivo."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO readings (device_id, dps_json, online, collected_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        """, (device_id, dps_json, 1 if online else 0))


def get_latest_reading(device_id: str) -> Optional[Dict]:
    """Retorna a leitura mais recente de um dispositivo."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM readings WHERE device_id = ?
            ORDER BY collected_at DESC LIMIT 1
        """, (device_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_readings_for_series(device_id: str, dps_code: str,
                           start_timestamp: str = None,
                           end_timestamp: str = None) -> List[Dict]:
    """Retorna leituras de um DP específico de um device em um período."""
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
        cursor.execute("SELECT * FROM monitor_configs WHERE enabled = 1")
        return [dict(row) for row in cursor.fetchall()]


def get_device_status(device_id: str) -> Optional[Dict]:
    """Status de um device. Wrapper de _query_device_statuses (1 query)."""
    linhas = _query_device_statuses(device_id=device_id)
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
        return cursor.lastrowid


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
            group['scope'] = (
                'geral' if group.get('scope_local_id') is None else 'local'
            )
            group['scope_local_nome'] = _nome_do_local(
                conn, group.get('scope_local_id'))
            group['fora_do_escopo'] = sum(
                1 for s in group['series'] if s['out_of_scope'])

        return groups


def add_series_to_group(group_id: int, device_id: str, dps_code: str,
                       label: str, sort_order: int = 0) -> int:
    """
    Adiciona uma série a um grupo, respeitando o escopo do grupo.

    Grupo com escopo de local só aceita dispositivo daquele local. A checagem
    e o INSERT ficam na mesma conexão e na mesma transação de propósito: em
    conexões separadas haveria janela entre verificar e gravar.
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

        cursor.execute("""
            INSERT INTO comparison_series
            (group_id, device_id, dps_code, label, sort_order)
            VALUES (?, ?, ?, ?, ?)
        """, (group_id, device_id, dps_code, label, sort_order))
        return cursor.lastrowid


def delete_comparison_group(group_id: int) -> None:
    """Deleta um grupo comparativo e suas séries."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM comparison_series WHERE group_id = ?",
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
                 AND r.collected_at >= datetime('now', ?)
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
    WHERE (? IS NULL OR d.id = ?)
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
                           comodo_id: int = None) -> List[Dict]:
    """
    Devices + último reading + monitor config + local/cômodo, numa query.

    Substitui o laço que chamava get_device_status() por dispositivo (3–4
    conexões cada). O is_online é calculado no SQL com datetime('now', ...),
    que roda no mesmo relógio e no mesmo formato do CURRENT_TIMESTAMP que
    gravou collected_at — os dois em UTC. Comparar com datetime.now() do
    Python, como antes, dava diferença negativa em fuso a oeste de Greenwich
    e fazia tudo passar por online.
    """
    janela = '-%d minutes' % ONLINE_WINDOW_MINUTES
    with get_db() as conn:
        # Dispositivos importados antes desta versão podem não ter config.
        # INSERT OR IGNORE + PK em device_id torna isso no-op depois da 1a vez.
        conn.execute("""
            INSERT OR IGNORE INTO monitor_configs
            (device_id, enabled, poll_interval_seconds)
            SELECT id, 1, ? FROM devices
        """, (DEFAULT_POLL_INTERVAL_SECONDS,))

        rows = conn.execute(_SQL_STATUS, (
            janela,
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


def get_all_device_statuses(local_id: int = None,
                            comodo_id: int = None) -> List[Dict]:
    return _query_device_statuses(local_id=local_id, comodo_id=comodo_id)


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
        ORDER BY s.sort_order
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

    return _query_device_statuses(local_id=escopo)


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
