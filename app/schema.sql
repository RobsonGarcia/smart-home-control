-- Locais (nível 1 da hierarquia: a casa, o sítio, o escritório)
CREATE TABLE IF NOT EXISTS locais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    descricao TEXT,
    rede_cidr TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_locais_nome
    ON locais(nome COLLATE NOCASE);

-- Cômodos (nível 2: sempre dentro de um local)
CREATE TABLE IF NOT EXISTS comodos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_id INTEGER NOT NULL REFERENCES locais(id),
    nome TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_comodos_local_nome
    ON comodos(local_id, nome COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_comodos_local
    ON comodos(local_id, sort_order);

-- Tabela de dispositivos (inventário)
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY NOT NULL UNIQUE,
    name TEXT NOT NULL,
    local_key TEXT NOT NULL,
    category TEXT,
    product_name TEXT,
    model TEXT,
    mapping_json TEXT,
    is_sub INTEGER DEFAULT 0,
    parent_id TEXT,
    ip TEXT,
    protocol_version REAL DEFAULT 3.4,
    last_seen_at TIMESTAMP,
    -- Atribuicao feita pelo usuario. NAO confundir com parent_id/is_sub, que
    -- sao de sub-dispositivos Tuya. Nunca sobrescrita por scan/reimportacao.
    local_id INTEGER REFERENCES locais(id),
    comodo_id INTEGER REFERENCES comodos(id),
    source TEXT DEFAULT 'cloud' CHECK(source IN ('cloud', 'broadcast')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(id)
);

-- Log de descobertas de rede
CREATE TABLE IF NOT EXISTS discovery_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    device_id TEXT NOT NULL,
    ip TEXT,
    raw_json TEXT,
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

-- Configuração de monitoramento por dispositivo
CREATE TABLE IF NOT EXISTS monitor_configs (
    device_id TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    poll_interval_seconds INTEGER DEFAULT 60,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

-- Leituras de status (dados coletados)
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    dps_json TEXT NOT NULL,
    online INTEGER DEFAULT 1,
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

-- Grupos de comparação de energia
-- scope_local_id NULL = grupo geral (aceita dispositivo de qualquer local).
CREATE TABLE IF NOT EXISTS comparison_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    scope_local_id INTEGER REFERENCES locais(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Séries dentro de um grupo comparativo
CREATE TABLE IF NOT EXISTS comparison_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    dps_code TEXT NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES comparison_groups(id),
    FOREIGN KEY (device_id) REFERENCES devices(id)
);

-- Índices para queries frequentes
CREATE INDEX IF NOT EXISTS idx_readings_device_collected
    ON readings(device_id, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_log_device_timestamp
    ON discovery_log(device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_comparison_series_group
    ON comparison_series(group_id);

-- Os indices que dependem das colunas novas (devices.local_id/comodo_id e
-- comparison_groups.scope_local_id) sao criados em app/migrations.py, depois
-- dos ALTER TABLE: aqui eles quebrariam num banco que ainda nao migrou.
