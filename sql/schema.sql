-- TradingAV — esquema relacional (Postgres / Supabase) — v1 capa analítica.
--
-- Propósito: espejo RELACIONAL de solo-lectura del núcleo de negocio que hoy vive
-- en Mongo (clientes, cuentas, operaciones, AuM, contrapartes). NO es la base
-- operativa — la mesa sigue contra Mongo. Esto habilita reportería con SQL real,
-- cruces baratos y, a futuro, BI/ML. Ver docs/ARQUITECTURA.md §5.
--
-- Diseño:
--  * DIMENSIONES (cuentas, comitentes, operadores, contrapartes): PK natural.
--  * HECHOS (operaciones, aum, negocio_movimientos): id_cuenta es columna
--    INDEXADA, NO foreign key dura. La fuente Mongo tiene huérfanos (operaciones
--    con id_cuenta que no está en Comitentes); un FK duro los rechazaría. Dejarlo
--    soft nos deja, además, AUDITAR esos huérfanos como feature de calidad de dato.
--  * Tipos/nullability marcados con TODO:validar — confirmar contra un diag
--    read-only de Mongo antes de cargar en serio (REGLA #2). Es v1.
--
-- Idempotente: DROP ... IF EXISTS + CREATE. El sync (jobs/sync_postgres.py, Fase B)
-- hace UPSERT por PK.

-- ─────────────────────────────────────────────────────────────────────────────
-- DIMENSIONES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS operadores (
    email   text PRIMARY KEY,
    nombre  text
);

CREATE TABLE IF NOT EXISTS cuentas (
    id_cuenta    text PRIMARY KEY,          -- clave estable en todo el sistema ("805")
    denominacion text                        -- "[805] NOMBRE" o nombre a secas
);

CREATE TABLE IF NOT EXISTS comitentes (
    id_cuenta       text PRIMARY KEY REFERENCES cuentas(id_cuenta),
    operador_email  text REFERENCES operadores(email),
    tipo_doc        text,
    nro_doc         text,
    nivel_1         text,                    -- segmentación (MAYÚSCULAS): PRODUCTORES, etc.
    nivel_2         text,
    nivel_3         text,
    estado_comercial text,                   -- ACTIVA / ENFRIANDOSE / DORMIDA / NUEVA (derivado)
    -- Agregados para la vista COMERCIAL:
    estado            text,                   -- legal Aunesa ("Activa") — distinto de estado_comercial
    fecha_alta_legajo date,                   -- alta del legajo (informe por segmento)
    telefono          text,
    email             text,
    nivel_4           text,
    nivel_5           text,
    primer_contacto_comercial text,
    riesgo_la_ft      text,
    division          text,
    adc               text,
    dma               text,
    cupo_transaccional_ars numeric,           -- cupo.transaccional_ars (subdoc Mongo)
    cupo_usado_ars         numeric,           -- cupo.usado_ars
    referido               text               -- quién refirió al cliente (ficha + filtro comercial)
);
-- La tabla ya existe en Supabase → ALTER idempotente agrega las columnas nuevas.
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS estado                    text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS fecha_alta_legajo         date;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS telefono                  text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS email                     text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS nivel_4                   text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS nivel_5                   text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS primer_contacto_comercial text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS riesgo_la_ft              text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS division                  text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS adc                       text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS dma                       text;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS cupo_transaccional_ars    numeric;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS cupo_usado_ars            numeric;
ALTER TABLE comitentes ADD COLUMN IF NOT EXISTS referido                  text;
CREATE INDEX IF NOT EXISTS ix_comitentes_operador ON comitentes(operador_email);
CREATE INDEX IF NOT EXISTS ix_comitentes_nivel1   ON comitentes(nivel_1);
CREATE INDEX IF NOT EXISTS ix_comitentes_estado   ON comitentes(estado);
CREATE INDEX IF NOT EXISTS ix_comitentes_alta     ON comitentes(fecha_alta_legajo);

-- Manager.Users — usuarios de la app (RBAC). email lowercased. Antes solo `email` (flag
-- huérfanas comercial); ahora con role/enabled/etc. para la migración de AUTH a SQL.
CREATE TABLE IF NOT EXISTS manager_users (
    email text PRIMARY KEY
);
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS role            text;
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS enabled         boolean;
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS auto_registered boolean;
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS notes           text;
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS last_seen_at    timestamptz;
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS created_at      timestamptz;
ALTER TABLE manager_users ADD COLUMN IF NOT EXISTS updated_at      timestamptz;

-- Manager.RoleMatrix — qué módulos ve cada rol. Filas-largas (role, module). En Mongo es
-- 1 doc por rol con un array modules. Vacío → DEFAULT_MATRIX (en core/roles.py).
CREATE TABLE IF NOT EXISTS role_matrix (
    role   text NOT NULL,
    module text NOT NULL,
    PRIMARY KEY (role, module)
);

-- Manager.Grupos — scope de cuentas por usuario. emails/id_cuentas son arrays (lowercased
-- los emails). cuentas_visibles: 0 grupos → None (ve todo); ≥1 → unión de id_cuentas.
CREATE TABLE IF NOT EXISTS grupos (
    id         text PRIMARY KEY,        -- str(ObjectId) de Mongo
    nombre     text,
    emails     text[] NOT NULL DEFAULT '{}',
    id_cuentas text[] NOT NULL DEFAULT '{}',
    creado_por text,
    creado_at  timestamptz,
    updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_grupos_emails ON grupos USING gin(emails);

-- Clientes.ActividadMensual — snapshot point-in-time (operador/segmento CONGELADOS al
-- correr el job). NO derivar en vivo (rompería el congelado). Se espeja tal cual.
CREATE TABLE IF NOT EXISTS actividad_mensual (
    year_month      text NOT NULL,            -- "YYYY-MM" (comparación lexicográfica = Mongo)
    id_cuenta       text NOT NULL,
    operador_email  text,
    operador_nombre text,
    nivel_1         text,
    n_ops           integer,
    volumen_ars     numeric,
    PRIMARY KEY (year_month, id_cuenta)
);
CREATE INDEX IF NOT EXISTS ix_am_operador ON actividad_mensual(operador_email, year_month);

CREATE TABLE IF NOT EXISTS contrapartes (
    id_cuenta    text PRIMARY KEY,           -- en Mongo: CashFlow.Contrapartes.cuenta
    contraparte  text,                       -- nombre
    segmento     text                        -- grupo (Fondos, ALYC, ...)
);
CREATE INDEX IF NOT EXISTS ix_contrapartes_segmento ON contrapartes(segmento);

-- ─────────────────────────────────────────────────────────────────────────────
-- HECHOS
-- ─────────────────────────────────────────────────────────────────────────────

-- CashFlow.Operaciones (~490k). boleto es único (uq_boleto_full) pero hay docs
-- sin boleto → PK surrogate + boleto unique-nullable.
CREATE TABLE IF NOT EXISTS operaciones (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    boleto         text UNIQUE,
    concertacion   date,                      -- Mongo guarda 'YYYY-MM-DD' string → casteado a date
    id_cuenta      text,                      -- soft ref (puede ser huérfano). Mongo: campo `cuenta`
    denominacion   text,
    moneda         text,
    mercado        text,
    operacion      text,
    segmento       text,
    nivel_3        text,
    commodity      text,
    es_cierre      boolean,
    etapa          text,
    bruto          numeric,
    arancel        numeric,                   -- siempre en ARS
    mep            numeric,                   -- TC snapshot del boleto (concertacion) → dolarizar volumen
    -- Agregados en la migración de la vista OPERACIONES a SQL (ver docs/SQL.md):
    cantidad       numeric,                   -- toneladas agro, drill-down boletos
    instrumento    text,                      -- agro (regex MIN), por_instrumento, boletos
    tipo_operacion text,                      -- output de /ops/boletos
    condiciones    text,                      -- output de /ops/boletos
    ingestado_en   timestamptz                -- /ops/meta (max ingesta del día)
);
-- La tabla ya estaba creada en Supabase → CREATE IF NOT EXISTS NO agrega columnas.
-- Estos ALTER son idempotentes y SÍ las agregan a la tabla existente.
ALTER TABLE operaciones ADD COLUMN IF NOT EXISTS cantidad       numeric;
ALTER TABLE operaciones ADD COLUMN IF NOT EXISTS instrumento    text;
ALTER TABLE operaciones ADD COLUMN IF NOT EXISTS tipo_operacion text;
ALTER TABLE operaciones ADD COLUMN IF NOT EXISTS condiciones    text;
ALTER TABLE operaciones ADD COLUMN IF NOT EXISTS ingestado_en   timestamptz;

CREATE INDEX IF NOT EXISTS ix_ops_concertacion ON operaciones(concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_id_cuenta    ON operaciones(id_cuenta);
CREATE INDEX IF NOT EXISTS ix_ops_moneda_cierre ON operaciones(moneda, es_cierre);
-- Patrones de la vista OPERACIONES en SQL (agregar live, sin rollup):
CREATE INDEX IF NOT EXISTS ix_ops_moneda_concert ON operaciones(moneda, concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_segmento_concert ON operaciones(segmento, concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_ingestado ON operaciones(ingestado_en);
CREATE INDEX IF NOT EXISTS ix_ops_commodity_concert ON operaciones(commodity, concertacion)
    WHERE commodity IN ('SOJA', 'TRIGO', 'MAIZ');

-- Valuaciones.AuM (~291k). Grano único (fecha_snapshot, id_cuenta, unidad).
CREATE TABLE IF NOT EXISTS aum (
    fecha_snapshot date NOT NULL,
    id_cuenta      text NOT NULL,            -- soft ref
    unidad         text NOT NULL,
    cuenta         text,
    cantidad       numeric,
    precio         numeric,
    valuacion      numeric,
    tipo_titulo    text,                     -- Mongo tipoTitulo (total_snapshot + normalizer PnL)
    PRIMARY KEY (fecha_snapshot, id_cuenta, unidad)
);
ALTER TABLE aum ADD COLUMN IF NOT EXISTS tipo_titulo text;
CREATE INDEX IF NOT EXISTS ix_aum_id_cuenta ON aum(id_cuenta, fecha_snapshot);
CREATE INDEX IF NOT EXISTS ix_aum_unidad    ON aum(unidad, fecha_snapshot);

-- Valuaciones.Assets — master de instrumentos (UPPERCASE en Mongo → lowercase acá).
-- Join por `unidad` con aum. Alimenta carteras (cartera), FCI (cartera/emisor),
-- renta fija (clase_activo) y el normalizer del PnL (ticker/instrumento/cafci).
-- Master de metadatos de títulos. Vive en el schema `portafolio` (junto a
-- `portafolio.tenencia`) — es la FUENTE DE VERDAD de la segmentación (panel
-- Manager → Assets escribe acá; el writer diario auto-da-de-alta unidades nuevas).
-- Migración public→portafolio: scripts/migrar_assets_a_portafolio.py.
CREATE SCHEMA IF NOT EXISTS portafolio;
CREATE TABLE IF NOT EXISTS portafolio.assets (
    unidad         text PRIMARY KEY,
    cartera        text,
    clase_activo   text,
    emisor         text,
    ticker         text,
    instrumento    text,
    calificacion   text,
    cafci          text,
    vencimiento    text,
    codigo_cnv     text,
    fee_admin      numeric,          -- fracción (0.01 = 1%), FCI
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_assets_cartera ON portafolio.assets(cartera);
CREATE INDEX IF NOT EXISTS ix_assets_clase   ON portafolio.assets(clase_activo);
CREATE INDEX IF NOT EXISTS ix_assets_ticker  ON portafolio.assets(ticker);

-- Valuaciones.Dolar — feed MEP (timestamp, mep). get_mep_for_date: último mep <= eod(fecha).
CREATE TABLE IF NOT EXISTS dolar (
    timestamp timestamptz PRIMARY KEY,
    mep       numeric
);
CREATE INDEX IF NOT EXISTS ix_dolar_ts ON dolar(timestamp DESC) WHERE mep IS NOT NULL;

-- Trading.PortfolioSnapshot — precio live por ticker (motor de tenencia). Para PnL no-realizado.
CREATE TABLE IF NOT EXISTS portfolio_snapshot (
    ticker        text PRIMARY KEY,
    last_price    numeric,
    closing_price numeric
);

-- Trading.SnapshotsCierre — último cierre por ticker (fallback de precio del PnL).
CREATE TABLE IF NOT EXISTS snapshots_cierre (
    ticker     text PRIMARY KEY,
    last_price numeric,
    fecha      date
);

-- News.Headlines — RSS/Finnhub (home). key=url. data jsonb = doc completo (fechas a ISO).
CREATE TABLE IF NOT EXISTS news_headlines (
    url               text PRIMARY KEY,
    fecha_publicacion timestamptz,
    fuente            text,
    categoria         text,
    titulo            text,
    data              jsonb
);
CREATE INDEX IF NOT EXISTS ix_news_fecha ON news_headlines(fecha_publicacion DESC);

-- Market.Quotes — watchlist /argy (key=symbol). data jsonb = doc completo (anchors).
CREATE TABLE IF NOT EXISTS market_quotes (
    symbol text PRIMARY KEY,
    grupo  text,
    data   jsonb
);

-- Market.EconomicCalendar — eventos macro. v2: PK NATURAL (evt_ts, country, event),
-- igual al unique index del ESCRITOR (jobs/economic_calendar upsertea por time/
-- country/event) → dual-write limpio. La v1 (hkey = md5 del doc) generaba una fila
-- nueva en cada update del evento. Migración guardada: si existe la v1 se dropea y
-- recrea (espejo descartable — el próximo sync_postgres la repuebla entera).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'market_calendar' AND column_name = 'hkey') THEN
    DROP TABLE market_calendar;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS market_calendar (
    evt_ts  timestamptz NOT NULL,            -- el filtro de rango usa el prefijo de la PK
    country text NOT NULL,
    event   text NOT NULL,
    impact  integer,
    data    jsonb,
    PRIMARY KEY (evt_ts, country, event)
);

-- CashFlow.NegocioMovimientos (~339k). Grano único (fecha, comprobante).
CREATE TABLE IF NOT EXISTS negocio_movimientos (
    fecha        date NOT NULL,
    comprobante  text NOT NULL,
    id_cuenta    text,                       -- soft ref
    categoria    text,
    op           text,
    ticker       text,
    cantidad     numeric,
    precio       numeric,
    importe      numeric,
    moneda       text,
    mep          numeric,                    -- snapshot del MEP del día (pesificación)
    -- Agregados para la migración de la vista NEGOCIO:
    cuenta       text,                       -- string "[id] NOMBRE" (clave de la vista + filtros)
    unidad       text,                       -- marker de futuros DLR ("USDL") → se excluyen
    plazo        text,
    lugar        text,
    estado       text,
    informacion  text,
    ingestado_en timestamptz,                -- meta de NEGOCIO (última ingesta del día)
    PRIMARY KEY (fecha, comprobante)
);
-- La tabla ya existe en Supabase → ALTER idempotente agrega las columnas nuevas.
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS cuenta       text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS unidad       text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS plazo        text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS lugar        text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS estado       text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS informacion  text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS ingestado_en timestamptz;

CREATE INDEX IF NOT EXISTS ix_nm_id_cuenta ON negocio_movimientos(id_cuenta, fecha);
CREATE INDEX IF NOT EXISTS ix_nm_categoria ON negocio_movimientos(categoria, fecha);
CREATE INDEX IF NOT EXISTS ix_nm_cuenta    ON negocio_movimientos(cuenta, fecha);

-- CashFlow.Accionistas — set de cuentas accionistas (para el filtro de cuenta de NEGOCIO/
-- portfolio: accionistas / sin_accionistas / cooperativas). Solo el string `cuenta`.
CREATE TABLE IF NOT EXISTS accionistas (
    cuenta text PRIMARY KEY
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CAPA MERCADO (espejo de Trading.*) — ver docs/SQL.md §Mercado
-- Diseño: NO se copia el desparramo de Mongo. Las 7 colecciones-serie
-- {fecha, valor} colapsan en UNA tabla larga; Curvas/BondsMaster materializan
-- lo consultable como columnas y guardan flujos + doc completo en jsonb;
-- market_snapshot es COLUMNAR para replicar la semántica $set parcial de los
-- dos motores (cada uno escribe SOLO sus columnas, sin pisarse).
-- ─────────────────────────────────────────────────────────────────────────────

-- Trading.{CER, DOLAR, BADLAR, TAMAR, RiesgoPais, InflacionMensual, InflacionInteranual}
-- (jobs.bcra + jobs.argentina_datos, shape {fecha:'YYYY-MM-DD', valor}).
-- `serie` = nombre de la colección Mongo (clave compartida por sync y dual-write).
CREATE TABLE IF NOT EXISTS series_macro (
    serie text NOT NULL,
    fecha date NOT NULL,
    valor numeric,
    PRIMARY KEY (serie, fecha)
);

-- Trading.REM (jobs.argentina_datos) — consenso IPC INDEC por informe/período.
CREATE TABLE IF NOT EXISTS rem (
    informe       text NOT NULL,            -- 'YYYY-MM' del informe
    periodo       text NOT NULL,            -- 'YYYY-MM' normalizado (orden lexicográfico)
    periodo_tipo  text NOT NULL,            -- 'mensual' | 'trimestral'
    fecha_informe date,
    mediana numeric, promedio numeric, desvio numeric,
    minimo  numeric, maximo   numeric,
    p10 numeric, p25 numeric, p75 numeric, p90 numeric,
    participantes numeric,
    updated_at timestamptz,
    PRIMARY KEY (informe, periodo, periodo_tipo)
);
CREATE INDEX IF NOT EXISTS ix_rem_periodo ON rem(periodo);

-- Trading.Curvas — master de instrumentos de renta fija. PK ticker_corto (clave
-- del upsert de ons.sync_ons_to_curvas; el sync saltea docs sin ticker_corto y
-- los cuenta). Flujos como jsonb (shape varía por curva: CER % vs tasa_fija abs
-- — ver CLAUDE.md raíz); `data` = doc completo para no perder campos no
-- materializados.
CREATE TABLE IF NOT EXISTS curvas (
    ticker_corto      text PRIMARY KEY,
    ticker            text,
    curva             text,                  -- tasa_fija | cer | soberanos | on_<sector> | ...
    tipo              text,                  -- Bono | Lecap | Boncap | Soberano | ON
    moneda_flujo      text,
    valor_nominal     numeric,
    fecha_emision     date,
    fecha_vencimiento date,
    cupon_anual       numeric,
    cer_emision       numeric,
    flujo_vencimiento numeric,
    emisor            text,
    sector            text,
    flujos            jsonb,
    data              jsonb
);
CREATE INDEX IF NOT EXISTS ix_curvas_curva ON curvas(curva);
CREATE INDEX IF NOT EXISTS ix_curvas_vto   ON curvas(fecha_vencimiento);

-- Trading.BondsMaster — master editable de ONs (panel Manager → TÍTULOS).
CREATE TABLE IF NOT EXISTS bonds_master (
    asset           text PRIMARY KEY,
    emisor          text,
    sector          text,
    moneda_flujo    text,
    tasa_cupon      numeric,
    vencimiento     date,
    tickers         jsonb,                   -- {ARS: ticker, USD: ticker}
    flujos          jsonb,
    actualizado_por text,
    actualizado_at  timestamptz,
    data            jsonb
);

-- Trading.MarketSnapshot — estado live por ticker. COLUMNAR a propósito: en Mongo
-- dos motores escriben el mismo doc con $set parcial sin pisarse (valores.py →
-- book/precios cada 1s; curvas.py → analíticos cada 5s). Acá cada escritor
-- upsertea SOLO sus columnas → misma semántica. Un jsonb compartido NO sirve
-- (el merge shallow de `metrics` pisaría los campos del otro motor).
CREATE TABLE IF NOT EXISTS market_snapshot (
    ticker         text PRIMARY KEY,
    -- engines/valores.py (motor_rofex):
    book           jsonb,                    -- {bids: [...], offers: [...]}
    last_price     numeric,
    open_price     numeric,
    high_price     numeric,
    low_price      numeric,
    closing_price  numeric,
    vwap           numeric,
    total_nominals numeric,
    updated_at     timestamptz,
    -- engines/curvas.py (motor_curvas):
    tea            numeric,
    tem            numeric,
    duration       numeric,
    mod_duration   numeric,
    convexity      numeric,
    paridad        numeric
);

-- Trading.SnapshotsCierre — HISTÓRICO completo del cierre diario por bono
-- (jobs/snapshot_cierre.py; en Mongo ts_cierre es string 'YYYY-MM-DD' → date).
-- La tabla `snapshots_cierre` existente (último cierre por ticker, fallback del
-- PnL) se mantiene aparte: grano distinto, consumidor distinto.
CREATE TABLE IF NOT EXISTS snapshots_cierre_hist (
    fecha              date NOT NULL,        -- Mongo: ts_cierre
    curva              text NOT NULL,
    ticker             text NOT NULL,
    ticker_corto       text,
    tipo               text,
    fecha_vencimiento  date,
    fecha_emision      date,
    ultimo_precio      numeric,
    tea numeric, tem numeric, paridad numeric,
    duration numeric, mod_duration numeric, convexity numeric,
    total_nominals_dia numeric,
    is_zero_coupon     boolean,
    PRIMARY KEY (fecha, curva, ticker)
);
CREATE INDEX IF NOT EXISTS ix_sch_ticker ON snapshots_cierre_hist(ticker, fecha);
CREATE INDEX IF NOT EXISTS ix_sch_curva  ON snapshots_cierre_hist(curva, fecha);

-- Trading.CanjeCierre — cierre diario de tickers de canje (jobs/cierre_canje.py).
CREATE TABLE IF NOT EXISTS canje_cierre (
    ticker     text NOT NULL,
    fecha      date NOT NULL,
    price      numeric,
    updated_at timestamptz,
    PRIMARY KEY (ticker, fecha)
);

-- Históricos DIARIOS de la vista mercado (1 tabla genérica): BreakevensHistorico,
-- ForwardsHistorico, FuturosDLR, Caucion, FitParams, FairValueResiduos. Grano:
-- (colección, fecha, subclave) — la subclave (`k`) es curva/ticker/moneda según la
-- colección ('' si el día es la clave entera). Doc completo en jsonb. Los snapshots
-- LIVE (BreakevensLive, ForwardsLive, *Snapshot, DolarSnapshot…) NO se espejan por
-- sync (una foto horaria de un dato por-segundo no sirve): migran con su vista vía
-- dual-write del motor (core/pg_mirror, mismo patrón que market_snapshot).
CREATE TABLE IF NOT EXISTS mercado_hist (
    coleccion text NOT NULL,
    fecha     date NOT NULL,
    k         text NOT NULL DEFAULT '',
    data      jsonb,
    PRIMARY KEY (coleccion, fecha, k)
);
