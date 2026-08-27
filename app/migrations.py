"""
Migrações de schema, versionadas por PRAGMA user_version.

Por que isto existe: init_db() roda schema.sql, que é todo CREATE TABLE IF NOT
EXISTS. Num banco que já existe, editar schema.sql é no-op — a coluna nova
simplesmente nunca aparece. Este módulo aplica as mudanças incrementais.

Concorrência: o web (app/main.py) e o coletor (run_collector.py) são processos
separados e ambos chamam init_db() na subida. Cada migração roda dentro de um
BEGIN IMMEDIATE, que pega o write-lock ANTES de ler o user_version — então
quem chega em segundo lugar espera, relê a versão já incrementada e não faz
nada. O seed também é idempotente por conta própria, como segunda barreira.
"""

import logging
import sqlite3
import time

from app.config import DEFAULT_LOCAL_NAME
from app.db import get_connection

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


# Lista, nao um script: conn.executescript() faz COMMIT implicito da
# transacao pendente, o que quebraria o BEGIN IMMEDIATE de _aplicar().
_DDL_HIERARQUIA = [
    """CREATE TABLE IF NOT EXISTS locais (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        descricao TEXT,
        rede_cidr TEXT,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_locais_nome
        ON locais(nome COLLATE NOCASE)""",
    """CREATE TABLE IF NOT EXISTS comodos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        local_id INTEGER NOT NULL REFERENCES locais(id),
        nome TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_comodos_local_nome
        ON comodos(local_id, nome COLLATE NOCASE)""",
    """CREATE INDEX IF NOT EXISTS idx_comodos_local
        ON comodos(local_id, sort_order)""",
]


def _colunas(conn, tabela):
    return {row[1] for row in conn.execute("PRAGMA table_info(%s)" % tabela)}


def _add_coluna(conn, tabela, coluna, definicao):
    """ALTER TABLE ADD COLUMN idempotente."""
    if coluna in _colunas(conn, tabela):
        return
    conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (tabela, coluna, definicao))
    logger.info("migração: coluna %s.%s criada", tabela, coluna)


def _reconstruir_comparison_groups(conn):
    """
    Recria comparison_groups com scope_local_id e nome único POR ESCOPO.

    O `name TEXT NOT NULL UNIQUE` original é global, e com locais dois lugares
    vão querer um grupo "Cargas". SQLite não remove constraint por ALTER, então
    a tabela é recriada. Sem ALTER TABLE RENAME de propósito: renomear reescreve
    as cláusulas REFERENCES de comparison_series e é onde esse procedimento
    costuma quebrar. Como a tabela é pequena, os dados dão a volta pelo Python.
    """
    if "scope_local_id" in _colunas(conn, "comparison_groups"):
        return  # banco novo: schema.sql já criou no formato certo

    linhas = conn.execute(
        "SELECT id, name, description, created_at FROM comparison_groups"
    ).fetchall()

    conn.execute("DROP TABLE comparison_groups")
    conn.execute("""
        CREATE TABLE comparison_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            scope_local_id INTEGER REFERENCES locais(id),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Grupos que já existiam viram gerais (scope_local_id NULL), que é
    # exatamente o comportamento que eles tinham antes do escopo existir.
    conn.executemany(
        "INSERT INTO comparison_groups (id, name, description, scope_local_id,"
        " created_at) VALUES (?, ?, ?, NULL, ?)",
        [(r[0], r[1], r[2], r[3]) for r in linhas],
    )
    logger.info("migração: comparison_groups recriada (%d grupos preservados)",
                len(linhas))


def _migracao_001(conn):
    """Hierarquia local/cômodo, escopo de grupo, e o seed do local padrão."""
    for ddl in _DDL_HIERARQUIA:
        conn.execute(ddl)

    _add_coluna(conn, "devices", "local_id", "INTEGER REFERENCES locais(id)")
    _add_coluna(conn, "devices", "comodo_id", "INTEGER REFERENCES comodos(id)")

    _reconstruir_comparison_groups(conn)

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS"
                 " idx_comparison_groups_nome_escopo ON comparison_groups"
                 "(name COLLATE NOCASE, COALESCE(scope_local_id, 0))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_comparison_groups_scope"
                 " ON comparison_groups(scope_local_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_local_comodo"
                 " ON devices(local_id, comodo_id)")

    # --- local padrão, onde todo o inventário existente vai parar ---
    #
    # Nenhum cômodo é criado aqui de propósito. Um cômodo chamado "Sem cômodo"
    # ficaria lado a lado com a caixa de entrada de mesmo nome — dois conceitos
    # com o mesmo rótulo. O dispositivo migrado fica REALMENTE sem cômodo
    # (comodo_id NULL) e aparece na caixa de entrada do local até você atribuir.
    row = conn.execute("SELECT id FROM locais WHERE nome = ?",
                       (DEFAULT_LOCAL_NAME,)).fetchone()
    if row:
        local_id = row[0]
    else:
        local_id = conn.execute(
            "INSERT INTO locais (nome, descricao, sort_order) VALUES (?, ?, 0)",
            (DEFAULT_LOCAL_NAME, "Criado na migração inicial"),
        ).lastrowid

    cur = conn.execute(
        "UPDATE devices SET local_id = ? WHERE local_id IS NULL", (local_id,))
    logger.info("migração 001: %d dispositivos movidos para '%s', sem cômodo",
                cur.rowcount, DEFAULT_LOCAL_NAME)


_DDL_SOLAR = [
    # Uma integracao = uma conta/planta num fabricante (driver). As credenciais
    # ficam no banco de proposito: o *.db e gitignorado e o painel e de LAN --
    # e nada delas pode aparecer em log ou resposta de API.
    """CREATE TABLE IF NOT EXISTS solar_integracoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        driver TEXT NOT NULL,
        nome TEXT NOT NULL,
        credenciais_json TEXT NOT NULL,
        planta_apikey TEXT,
        planta_nome TEXT,
        enabled INTEGER NOT NULL DEFAULT 1,
        backfill_feito INTEGER NOT NULL DEFAULT 0,
        nivel_acesso TEXT NOT NULL DEFAULT 'pro',
        local_id INTEGER REFERENCES locais(id),
        poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    # O vinculo inversor -> integracao. O inversor em si e uma linha em
    # devices (source = 'solar'); aqui fica so o que e especifico da coleta
    # cloud: o serial na API do fabricante e o coletor (PMU) dele.
    """CREATE TABLE IF NOT EXISTS solar_inversores (
        device_id TEXT PRIMARY KEY,
        integracao_id INTEGER NOT NULL REFERENCES solar_integracoes(id),
        sn TEXT NOT NULL UNIQUE,
        psn TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE INDEX IF NOT EXISTS idx_solar_inversores_integracao
        ON solar_inversores(integracao_id)""",
]


def _reconstruir_devices(conn):
    """
    Recria devices aceitando inversores solares: local_key vira nulavel (nao
    existe chave Tuya num inversor) e o CHECK de source ganha 'solar'.

    SQLite nao altera constraint por ALTER, entao e o mesmo procedimento de
    _reconstruir_comparison_groups: recriar e copiar pelo Python, sem
    ALTER TABLE RENAME. DROP TABLE derruba os indices junto -- eles sao
    recriados no fim.
    """
    ddl_atual = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'devices'"
    ).fetchone()[0]
    if "'solar'" in ddl_atual:
        return  # banco novo: schema.sql ja criou no formato certo

    colunas = ("id, name, local_key, category, product_name, model, "
               "mapping_json, is_sub, parent_id, ip, protocol_version, "
               "last_seen_at, local_id, comodo_id, source, created_at, "
               "updated_at")
    linhas = conn.execute("SELECT %s FROM devices" % colunas).fetchall()

    conn.execute("DROP TABLE devices")
    conn.execute("""
        CREATE TABLE devices (
            id TEXT PRIMARY KEY NOT NULL UNIQUE,
            name TEXT NOT NULL,
            local_key TEXT,
            category TEXT,
            product_name TEXT,
            model TEXT,
            mapping_json TEXT,
            is_sub INTEGER DEFAULT 0,
            parent_id TEXT,
            ip TEXT,
            protocol_version REAL DEFAULT 3.4,
            last_seen_at TIMESTAMP,
            local_id INTEGER REFERENCES locais(id),
            comodo_id INTEGER REFERENCES comodos(id),
            source TEXT DEFAULT 'cloud'
                CHECK(source IN ('cloud', 'broadcast', 'solar')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(id)
        )
    """)
    conn.executemany(
        "INSERT INTO devices (%s) VALUES (%s)"
        % (colunas, ", ".join(["?"] * 17)),
        [tuple(r) for r in linhas],
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_devices_local_comodo"
                 " ON devices(local_id, comodo_id)")
    logger.info("migração: devices recriada para aceitar source='solar'"
                " (%d dispositivos preservados)", len(linhas))


def _migracao_002(conn):
    """Energia solar: devices aceita inversores e nascem as tabelas solares."""
    _reconstruir_devices(conn)
    for ddl in _DDL_SOLAR:
        conn.execute(ddl)


MIGRACOES = {1: _migracao_001, 2: _migracao_002}


def run_migrations(max_wait_seconds: int = 20) -> int:
    """
    Aplica o que faltar. Espera se o outro processo estiver migrando — sem
    isso, um 'database is locked' aqui derruba o uvicorn, porque init_db()
    roda no import de app/main.py.
    """
    limite = time.monotonic() + max_wait_seconds
    while True:
        try:
            return _aplicar()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() > limite:
                raise
            logger.warning("migração: banco bloqueado por outro processo, aguardando")
            time.sleep(0.5)


def _aplicar() -> int:
    conn = get_connection()
    try:
        conn.execute("PRAGMA busy_timeout = 10000")
        # Transação manual: no modo autocommit legado do sqlite3 o DDL escapa
        # da transação implícita, e uma migração interrompida deixaria metade
        # das tabelas criadas com o user_version desatualizado.
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        atual = conn.execute("PRAGMA user_version").fetchone()[0]
        if atual >= SCHEMA_VERSION:
            conn.execute("ROLLBACK")
            return atual

        for versao in range(atual + 1, SCHEMA_VERSION + 1):
            MIGRACOES[versao](conn)
            # PRAGMA não aceita placeholder; o valor vem do range(), nunca do usuário.
            conn.execute("PRAGMA user_version = %d" % versao)
            logger.info("migração %d aplicada", versao)

        conn.execute("COMMIT")
        return SCHEMA_VERSION
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
