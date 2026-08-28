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
    -- NULL para fontes sem chave Tuya (inversores solares).
    local_key TEXT,
    category TEXT,
    product_name TEXT,
    model TEXT,
    -- Identificador do PRODUTO no Tuya (nao do aparelho). E a chave para o
    -- perfil de DPs em app/modelos.py, usado quando a nuvem nao descreve o
    -- produto e o mapping_json vem vazio.
    product_id TEXT,
    mapping_json TEXT,
    -- Imagem do produto publicada pelo Tuya (campo `icon` do devices.json).
    -- E uma URL do CDN deles: some numa rede sem internet, e a tela cai no
    -- icone SVG por tipo. Por isso nada depende dela.
    icon_url TEXT,
    is_sub INTEGER DEFAULT 0,
    parent_id TEXT,
    ip TEXT,
    protocol_version REAL DEFAULT 3.4,
    last_seen_at TIMESTAMP,
    -- Atribuicao feita pelo usuario. NAO confundir com parent_id/is_sub, que
    -- sao de sub-dispositivos Tuya. Nunca sobrescrita por scan/reimportacao.
    local_id INTEGER REFERENCES locais(id),
    comodo_id INTEGER REFERENCES comodos(id),
    source TEXT DEFAULT 'cloud'
        CHECK(source IN ('cloud', 'broadcast', 'solar', 'camera')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Tipo fixado a mao pelo usuario. Vazio = vale o derivado por
    -- app/capacidades.classificar() a partir da categoria e do mapping.
    tipo_manual TEXT,
    -- Opt-in de acionamento: NINGUEM nasce acionavel, nem a lampada.
    -- confirmar_acao pede o dialogo de confirmacao antes de cada comando.
    acionavel INTEGER NOT NULL DEFAULT 0,
    confirmar_acao INTEGER NOT NULL DEFAULT 1,
    UNIQUE(id)
);

-- Vinculo de VIDEO de um dispositivo: como chegar na imagem de uma camera.
-- A camera em si e uma linha em devices; isto e o que o Tuya nao entrega.
-- As credenciais (usuario/senha da camera) NUNCA saem em log ou API.
CREATE TABLE IF NOT EXISTS cameras (
    device_id TEXT PRIMARY KEY REFERENCES devices(id),
    driver TEXT NOT NULL,
    host TEXT,
    porta INTEGER,
    credenciais_json TEXT,
    perfil_token TEXT,
    perfil_nome TEXT,
    snapshot_uri TEXT,
    stream_uri TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Auditoria de acionamento: o que foi mandado, por onde, e se funcionou.
CREATE TABLE IF NOT EXISTS command_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    dp TEXT NOT NULL,
    code TEXT,
    valor_json TEXT,
    transporte TEXT,
    origem TEXT NOT NULL DEFAULT 'painel',
    ok INTEGER NOT NULL DEFAULT 0,
    erro TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_command_log_device
    ON command_log(device_id, created_at DESC);

-- Integracoes de energia solar: uma linha = uma conta/planta num fabricante
-- (driver em app/solar/). Credenciais ficam no banco de proposito -- o *.db e
-- gitignorado -- e NUNCA podem aparecer em log ou resposta de API.
CREATE TABLE IF NOT EXISTS solar_integracoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    driver TEXT NOT NULL,
    nome TEXT NOT NULL,
    credenciais_json TEXT NOT NULL,
    planta_apikey TEXT,
    planta_nome TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    backfill_feito INTEGER NOT NULL DEFAULT 0,
    -- Nivel de acesso da conta no fabricante (ex.: AiSWEI 'pro' x 'comum').
    -- O driver declara as capacidades de cada nivel e o resto se limita.
    nivel_acesso TEXT NOT NULL DEFAULT 'pro',
    -- Parametros de coleta definidos NA integracao: os inversores herdam o
    -- local e o intervalo dela ao serem descobertos (e re-descobertos).
    local_id INTEGER REFERENCES locais(id),
    poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Vinculo inversor -> integracao. O inversor em si e uma linha em devices
-- (source = 'solar'); aqui fica so o que e especifico da coleta cloud.
CREATE TABLE IF NOT EXISTS solar_inversores (
    device_id TEXT PRIMARY KEY,
    integracao_id INTEGER NOT NULL REFERENCES solar_integracoes(id),
    sn TEXT NOT NULL UNIQUE,
    psn TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_solar_inversores_integracao
    ON solar_inversores(integracao_id);

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

-- Paineis de um grupo: cada um e um GRAFICO, com as series dele.
-- Existem porque um eixo Y so serve a uma grandeza: potencia em W e geracao
-- em kWh no mesmo grafico deixam a curva de kWh colada no chao. Exatamente um
-- painel por grupo tem principal=1 -- ele abre a pagina; os outros vem abaixo,
-- na ordem de sort_order.
CREATE TABLE IF NOT EXISTS comparison_panels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES comparison_groups(id),
    nome TEXT NOT NULL,
    principal INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_comparison_panels_nome
    ON comparison_panels(group_id, nome COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_comparison_panels_group
    ON comparison_panels(group_id, sort_order);

-- Séries dentro de um grupo comparativo
CREATE TABLE IF NOT EXISTS comparison_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    dps_code TEXT NOT NULL,
    label TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    -- Em qual grafico do grupo esta serie aparece. NULL cai no principal.
    panel_id INTEGER REFERENCES comparison_panels(id),
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
CREATE INDEX IF NOT EXISTS idx_comparison_series_panel
    ON comparison_series(panel_id, sort_order);

-- Os indices que dependem das colunas novas (devices.local_id/comodo_id e
-- comparison_groups.scope_local_id) sao criados em app/migrations.py, depois
-- dos ALTER TABLE: aqui eles quebrariam num banco que ainda nao migrou.
