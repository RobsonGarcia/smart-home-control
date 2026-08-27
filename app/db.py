import sqlite3
from pathlib import Path
from contextlib import contextmanager

from app.config import DB_PATH


def get_connection():
    # timeout alto porque web e coletor sao processos separados e disputam o
    # write-lock, principalmente durante a migracao na subida.
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Criar tabelas se não existirem e aplicar migrações pendentes."""
    schema_path = Path(__file__).parent / "schema.sql"
    conn = get_connection()
    with open(schema_path, encoding='utf-8') as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    # Import tardio: migrations importa get_connection daqui.
    from app.migrations import run_migrations
    run_migrations()


@contextmanager
def get_db():
    """Context manager para conexão com DB."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
