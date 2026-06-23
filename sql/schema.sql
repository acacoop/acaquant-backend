-- TradingAV — esquema relacional (Postgres / Supabase) — v2 organizado por dominio.
--
-- Propósito: espejo RELACIONAL del núcleo de negocio (clientes, operaciones,
-- portafolio, valuaciones) Y de la capa de mercado (Trading.*). Para el núcleo de
-- negocio (clientes/operaciones/portafolio) Postgres YA es la FUENTE DE VERDAD
-- (escritura+lectura). Para mercado es espejo en vivo (dual-write de motores/jobs,
-- ver core/pg_mirror.py): si Postgres se cae, la mesa (Mongo) sigue intacta.
-- Ver docs/SQL.md y docs/ARQUITECTURA.md §5.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- ORGANIZACIÓN POR SCHEMA (v2, 2026-06-18) — nada en `public`.
-- El search_path (core/postgres.py) resuelve los nombres SIN calificar en este
-- orden, así el código existente sigue andando sin tocar cada query:
--   clientes,operaciones,portafolio,mercado,macro,valuaciones,manager,home,public
--
--   clientes    → cuentas, operadores, comitentes, contrapartes, accionistas, actividad_mensual
--   operaciones → operaciones, negocio_movimientos
--   portafolio  → tenencia, backfill_log, assets
--   valuaciones → consolidado, dolar, portfolio_snapshot
--   mercado     → curvas, market_snapshot, snapshots_cierre,
--                 snapshots_cierre_hist, canje_cierre, mercado_hist
--   macro       → series_macro, rem
--   manager     → manager_users, role_matrix, grupos
--   home        → news_headlines, market_quotes, market_calendar
--
-- Los NOMBRES de tabla son únicos en todo el search_path (no hay colisión entre
-- schemas) → una query sin calificar resuelve siempre a la tabla correcta.
--
-- Diseño (no se copia el desparramo de Mongo — motores distintos, modelo distinto):
--  * DIMENSIONES (cuentas, comitentes, operadores, contrapartes): PK natural.
--  * HECHOS (operaciones, negocio_movimientos): id_cuenta es columna INDEXADA, NO
--    foreign key dura. La fuente Mongo tiene huérfanos; un FK duro los rechazaría.
--    Dejarlo soft permite cargarlos Y AUDITARLOS como feature de calidad de dato.
--  * Las 7 colecciones-serie macro {fecha,valor} colapsan en UNA tabla larga.
--  * market_snapshot es COLUMNAR a propósito (semántica $set parcial de 2 motores).
--  * NO hay tablas rollup (OpsSerieDiaria/ComercialCache de Mongo NO se replican):
--    en Postgres el GROUP BY indexado corre en ms → se agrega EN VIVO.
--
-- Idempotente: se puede correr N veces. CREATE ... IF NOT EXISTS + el bloque de
-- MIGRACIÓN mueve las tablas que todavía estén en `public`/`portafolio` (instalación
-- vieja); en una instalación nueva no hay nada que mover y se crean en su schema.

CREATE SCHEMA IF NOT EXISTS clientes;
CREATE SCHEMA IF NOT EXISTS operaciones;
CREATE SCHEMA IF NOT EXISTS portafolio;
CREATE SCHEMA IF NOT EXISTS mercado;
CREATE SCHEMA IF NOT EXISTS macro;
CREATE SCHEMA IF NOT EXISTS valuaciones;
CREATE SCHEMA IF NOT EXISTS manager;
CREATE SCHEMA IF NOT EXISTS home;
CREATE SCHEMA IF NOT EXISTS partner;

-- ─────────────────────────────────────────────────────────────────────────────
-- MIGRACIÓN idempotente public/portafolio → schemas de dominio (v1 → v2).
-- Mueve cada tabla SOLO si todavía está en su schema viejo. ALTER ... SET SCHEMA
-- arrastra índices y constraints. En fresh install no matchea nada → no-op.
-- Tras moverla, el CREATE TABLE IF NOT EXISTS de más abajo es un no-op.
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
DECLARE mv record;
BEGIN
  FOR mv IN SELECT * FROM (VALUES
    ('public','curvas','mercado'),
    ('public','market_snapshot','mercado'),
    ('public','timesales','mercado'),
    ('public','options_snapshot','mercado'),
    ('public','options_metadata','mercado'),
    ('public','options_data_hist','mercado'),
    ('public','options_vr','mercado'),
    ('public','options_data','mercado'),
    ('public','futuros_dlr_snapshot','mercado'),
    ('public','caucion_snapshot','mercado'),
    ('public','forwards_zscore','mercado'),
    ('public','snapshots_cierre','mercado'),
    ('public','snapshots_cierre_hist','mercado'),
    ('public','canje_cierre','mercado'),
    ('public','mercado_hist','mercado'),
    ('public','cedears','mercado'),
    ('public','cedears_snapshot','mercado'),
    ('public','adr_snapshot','mercado'),
    ('public','precios_acciones','mercado'),
    ('public','day_trading_stats','mercado'),
    ('public','agro_snapshot','mercado'),
    ('public','agro_opciones_snapshot','mercado'),
    ('public','agro_pizarra','mercado'),
    ('public','camara_cereales','mercado'),
    ('public','volumen_mercado_agro','mercado'),
    ('public','movimientos','operaciones'),
    ('public','acreencias','operaciones'),
    ('public','tipos_operacion','operaciones'),
    ('public','series_macro','macro'),
    ('public','rem','macro'),
    ('public','dolar','valuaciones'),
    ('public','portfolio_snapshot','valuaciones'),
    ('portafolio','consolidado','valuaciones'),
    ('public','manager_users','manager'),
    ('public','role_matrix','manager'),
    ('public','grupos','manager'),
    ('public','job_runs','manager'),
    ('public','role_audit','manager'),
    ('public','news_headlines','home'),
    ('public','market_quotes','home'),
    ('public','market_calendar','home')
  ) AS t(src, tbl, dst)
  LOOP
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = mv.src AND table_name = mv.tbl)
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = mv.dst AND table_name = mv.tbl) THEN
      EXECUTE format('ALTER TABLE %I.%I SET SCHEMA %I', mv.src, mv.tbl, mv.dst);
    END IF;
  END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- DIMENSIONES — schema `clientes` (cuentas + segmentación)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS clientes.operadores (
    email   text PRIMARY KEY,
    nombre  text
);

CREATE TABLE IF NOT EXISTS clientes.cuentas (
    id_cuenta    text PRIMARY KEY,          -- clave estable en todo el sistema ("805")
    denominacion text                        -- "[805] NOMBRE" o nombre a secas
);

CREATE TABLE IF NOT EXISTS clientes.comitentes (
    id_cuenta       text PRIMARY KEY REFERENCES clientes.cuentas(id_cuenta),
    operador_email  text REFERENCES clientes.operadores(email),
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
    referido               text,              -- quién refirió al cliente (ficha + filtro comercial)
    -- Segmentación/auditoría (para escribir TODO a SQL — eran subdocs/campos en Mongo):
    observaciones          text,
    sucursal               text,
    segmento_patrimonial   text,
    origen                 text,              -- "aunesa" | "reconciler" | "manual"
    cupo_utilizacion_pct   numeric,
    cupo_cargado_en        timestamptz,
    cupo_fuente            text,
    actualizado_por        text,
    actualizado_at         timestamptz
);
CREATE INDEX IF NOT EXISTS ix_comitentes_operador ON clientes.comitentes(operador_email);
CREATE INDEX IF NOT EXISTS ix_comitentes_nivel1   ON clientes.comitentes(nivel_1);
CREATE INDEX IF NOT EXISTS ix_comitentes_estado   ON clientes.comitentes(estado);
CREATE INDEX IF NOT EXISTS ix_comitentes_alta     ON clientes.comitentes(fecha_alta_legajo);

-- Clientes.ActividadMensual — snapshot point-in-time (operador/segmento CONGELADOS al
-- correr el job). NO derivar en vivo (rompería el congelado). Se espeja tal cual.
CREATE TABLE IF NOT EXISTS clientes.actividad_mensual (
    year_month      text NOT NULL,            -- "YYYY-MM" (comparación lexicográfica = Mongo)
    id_cuenta       text NOT NULL,
    operador_email  text,
    operador_nombre text,
    nivel_1         text,
    n_ops           integer,
    volumen_ars     numeric,
    PRIMARY KEY (year_month, id_cuenta)
);
CREATE INDEX IF NOT EXISTS ix_am_operador ON clientes.actividad_mensual(operador_email, year_month);

CREATE TABLE IF NOT EXISTS clientes.contrapartes (
    id_cuenta       text PRIMARY KEY,        -- en Mongo: CashFlow.Contrapartes.cuenta
    contraparte     text,                    -- nombre
    segmento        text,                    -- grupo (Fondos, ALYC, ...)
    origen          text,                    -- "manual" | "reconciler"
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_contrapartes_segmento ON clientes.contrapartes(segmento);

-- CashFlow.Accionistas — set de cuentas accionistas (para el filtro de cuenta de NEGOCIO/
-- portfolio: accionistas / sin_accionistas / cooperativas). Solo el string `cuenta`.
CREATE TABLE IF NOT EXISTS clientes.accionistas (
    cuenta text PRIMARY KEY
);

-- ─────────────────────────────────────────────────────────────────────────────
-- HECHOS — schema `operaciones`
-- ─────────────────────────────────────────────────────────────────────────────

-- CashFlow.Operaciones (~490k). boleto es único pero hay docs sin boleto → PK
-- surrogate + boleto unique-nullable. La escribe directo jobs/operaciones_informes.py
-- y jobs/fci_bilateral.py (SQL-native, ya no se copia de Mongo).
CREATE TABLE IF NOT EXISTS operaciones.operaciones (
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
    cantidad       numeric,                   -- toneladas agro, drill-down boletos
    instrumento    text,                      -- agro (regex MIN), por_instrumento, boletos
    tipo_operacion text,                      -- output de /ops/boletos
    condiciones    text,                      -- output de /ops/boletos
    ingestado_en   timestamptz                -- /ops/meta (max ingesta del día)
);
-- ALTER idempotentes (tabla preexistente en Supabase no recibe columnas por CREATE IF NOT EXISTS).
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS cantidad       numeric;
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS instrumento    text;
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS tipo_operacion text;
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS condiciones    text;
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS ingestado_en   timestamptz;

CREATE INDEX IF NOT EXISTS ix_ops_concertacion ON operaciones.operaciones(concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_id_cuenta    ON operaciones.operaciones(id_cuenta);
CREATE INDEX IF NOT EXISTS ix_ops_moneda_cierre ON operaciones.operaciones(moneda, es_cierre);
CREATE INDEX IF NOT EXISTS ix_ops_moneda_concert ON operaciones.operaciones(moneda, concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_segmento_concert ON operaciones.operaciones(segmento, concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_ingestado ON operaciones.operaciones(ingestado_en);
CREATE INDEX IF NOT EXISTS ix_ops_commodity_concert ON operaciones.operaciones(commodity, concertacion)
    WHERE commodity IN ('SOJA', 'TRIGO', 'MAIZ');

-- CashFlow.NegocioMovimientos (~339k). Grano único (fecha, comprobante). La escribe
-- directo jobs/negocio_movimientos.py (SQL-native).
CREATE TABLE IF NOT EXISTS operaciones.negocio_movimientos (
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
    cuenta       text,                       -- string "[id] NOMBRE" (clave de la vista + filtros)
    unidad       text,                       -- marker de futuros DLR ("USDL") → se excluyen
    plazo        text,
    lugar        text,
    estado       text,
    informacion  text,
    ingestado_en timestamptz,                -- meta de NEGOCIO (última ingesta del día)
    -- Aranceles (cobro del proyecto por boleto): `arancel` = atajo ARS (lo consume el
    -- auditor /aunesa/boletos/faltantes); `aranceles` = desglose por moneda jsonb.
    arancel      numeric,
    aranceles    jsonb,
    PRIMARY KEY (fecha, comprobante)
);
-- ALTER idempotentes (tabla preexistente).
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS cuenta       text;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS unidad       text;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS plazo        text;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS lugar        text;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS estado       text;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS informacion  text;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS ingestado_en timestamptz;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS arancel      numeric;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS aranceles    jsonb;

CREATE INDEX IF NOT EXISTS ix_nm_id_cuenta ON operaciones.negocio_movimientos(id_cuenta, fecha);
CREATE INDEX IF NOT EXISTS ix_nm_categoria ON operaciones.negocio_movimientos(categoria, fecha);
CREATE INDEX IF NOT EXISTS ix_nm_cuenta    ON operaciones.negocio_movimientos(cuenta, fecha);

-- CashFlow.Movimientos → depósitos / extracciones / transferencias (vista FLUJOS,
-- /api/operaciones/flujos). La escribe jobs/cashflow.py ($setOnInsert por
-- `comprobante`). Passthrough columnar + data jsonb: la vista usa pocos campos
-- (comprobante, cuenta, fecha, informacion, total, unidad) → se materializan para
-- filtrar por cuenta/unidad; el resto del doc va en jsonb. OJO `fecha` viene en
-- string dd/mm/yyyy de Aunesa (NO ISO) → se guarda TAL CUAL (text), el parser a ISO
-- lo hace el read service (igual que el path Mongo, que ordena/filtra en Python).
-- PK = comprobante (índice único del writer).
CREATE TABLE IF NOT EXISTS operaciones.movimientos (
    comprobante text PRIMARY KEY,           -- Mongo `comprobante` → boleto en la vista
    cuenta      text,                        -- "[N] NOMBRE" (filtro + scope)
    fecha       text,                        -- dd/mm/yyyy CRUDO (NO ISO) — parseo a ISO en el read
    informacion text,
    total       numeric,                     -- ya con signo invertido por el writer (entrada +)
    unidad      text,                         -- ARS/USD (filtro)
    data        jsonb                         -- doc Mongo completo (campos extra)
);
CREATE INDEX IF NOT EXISTS ix_mov_cuenta ON operaciones.movimientos(cuenta);

-- CashFlow.Acreencias → proyección de cobros futuros por (cliente, fecha_pago,
-- ticker). La precomputa jobs/acreencias.py (--commit, swap atómico de toda la
-- colección) cruzando tenencia × calendario contractual. Read: back-office
-- (acreencias/*) + comercial (cobros-futuros). Passthrough columnar: las vistas
-- agrupan por fecha_pago / id_cuenta / moneda y suman monto → se materializan esas
-- columnas; `data` jsonb tiene el doc completo (cliente, emisor, cantidad, snapshot).
-- `generado_at` NO se guarda como columna: las lecturas lo proyectan fuera (igual
-- que Mongo). fecha_pago/snapshot son strings ISO 'YYYY-MM-DD'. PK surrogate: el
-- grano (id_cuenta, fecha_pago, ticker) no es único garantizado en la fuente →
-- el writer reemplaza TODO el set en cada corrida (truncate+insert), no upsertea.
CREATE TABLE IF NOT EXISTS operaciones.acreencias (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha_pago  text,                         -- ISO 'YYYY-MM-DD'
    id_cuenta   text,
    ticker      text,
    moneda      text,                         -- ARS | USD (nativa, NO se pesifica)
    monto       numeric,
    data        jsonb                         -- cliente, emisor, cantidad, snapshot, ...
);
CREATE INDEX IF NOT EXISTS ix_acr_fecha_pago ON operaciones.acreencias(fecha_pago);
CREATE INDEX IF NOT EXISTS ix_acr_id_cuenta  ON operaciones.acreencias(id_cuenta);

-- CashFlow.TiposOperacion → catálogo chico (mapeo tipo_operacion → mercado/operacion)
-- que enriquece la ingesta de operaciones. Passthrough jsonb; PK = `tipo_operacion`
-- (la clave del join en operaciones_informes.cargar_maps_enrich). Espejo BASELINE por
-- sync (el enrich de los writers SIGUE leyendo Mongo — corre en el proceso del job, no
-- en una vista con flag; ver docs/SQL.md). Sin lector SQL todavía: se espeja para tenerlo.
CREATE TABLE IF NOT EXISTS operaciones.tipos_operacion (
    tipo_operacion text PRIMARY KEY,
    data           jsonb
);

-- ─────────────────────────────────────────────────────────────────────────────
-- PORTAFOLIO — tenencias + catálogo de títulos (FUENTE DE VERDAD, SQL-native)
-- ─────────────────────────────────────────────────────────────────────────────

-- Tenencia diaria por cuenta/unidad. La escribe jobs/portafolio_backfill.py
-- (--diario, 11 UTC L-V) y la edita el editor HD (api/services/tenencia_hd.py).
-- Reincorporada al schema.sql el 2026-06-18 (se había creado a mano en Supabase;
-- el writer la sigue auto-creando con este mismo DDL en _ensure_schema()).
CREATE TABLE IF NOT EXISTS portafolio.tenencia (
    fecha       date NOT NULL,
    id_cuenta   text NOT NULL,
    cuenta      text,
    unidad      text NOT NULL,
    ticker      text,
    cartera     text,
    cantidad    numeric,
    precio      numeric,
    valuacion   numeric,
    moneda      text,
    aum         text,                          -- 'si'/'no': si la fila cuenta como AuM (_aum_filters)
    tipo_titulo text,                          -- tipoTitulo crudo de Aunesa (motor PnL _aplicar_normalizer)
    PRIMARY KEY (fecha, id_cuenta, unidad)
);
CREATE INDEX IF NOT EXISTS ix_tenencia_cuenta_fecha ON portafolio.tenencia(id_cuenta, fecha);

-- Log self-healing del writer diario (qué cuenta/fecha quedó OK o con timeout).
CREATE TABLE IF NOT EXISTS portafolio.backfill_log (
    fecha       date NOT NULL,
    id_cuenta   text NOT NULL,
    status      text,
    n           integer,
    detalle     text,
    actualizado timestamptz DEFAULT now(),
    PRIMARY KEY (fecha, id_cuenta)
);

-- Valuaciones.Assets → master de instrumentos (UPPERCASE en Mongo → lowercase acá).
-- FUENTE DE VERDAD de la segmentación de títulos (panel Manager → Assets escribe acá;
-- el writer diario auto-da-de-alta unidades nuevas). Join por `unidad` con tenencia.
-- Alimenta carteras (cartera), FCI (cartera/emisor), renta fija (clase_activo) y el
-- normalizer del PnL (ticker/instrumento/cafci).
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

-- ─────────────────────────────────────────────────────────────────────────────
-- VALUACIONES — cache de consolidado + feeds de precio para el PnL
-- ─────────────────────────────────────────────────────────────────────────────

-- Valuaciones.ConsolidadoCuentas — cache iterativo (1 fila/cuenta) para /valuaciones/consolidado.
-- NO es un rollup agregable en vivo: cada fila es el XIRR/TWR/PnL acumulado de la cuenta
-- (Python sobre los cierres SQL), por eso se precalcula. Lo escribe el cron
-- jobs.consolidado_cuentas (dual-write Mongo+SQL); el service SQL solo lo lee + filtra.
-- Migrado de portafolio→valuaciones el 2026-06-18.
CREATE TABLE IF NOT EXISTS valuaciones.consolidado (
    id_cuenta     text PRIMARY KEY,
    cuenta        text,
    ultimo_dia    text,
    valor_ars     numeric,
    valor_usd     numeric,
    base100_ars   numeric,
    base100_usd   numeric,
    pnl_acum_ars  numeric,
    pnl_acum_usd  numeric,
    tem_ars       numeric,
    tem_usd       numeric,
    tea_ars       numeric,
    tea_usd       numeric,
    computed_at   timestamptz DEFAULT now()
);

-- Valuaciones.Dolar — feed MEP (timestamp, mep). get_mep_for_date: último mep <= eod(fecha).
CREATE TABLE IF NOT EXISTS valuaciones.dolar (
    timestamp timestamptz PRIMARY KEY,
    mep       numeric
);
CREATE INDEX IF NOT EXISTS ix_dolar_ts ON valuaciones.dolar(timestamp DESC) WHERE mep IS NOT NULL;

-- Trading.PortfolioSnapshot — precio live por ticker (motor de tenencia). Para PnL no-realizado.
CREATE TABLE IF NOT EXISTS valuaciones.portfolio_snapshot (
    ticker        text PRIMARY KEY,
    last_price    numeric,
    closing_price numeric
);

-- ─────────────────────────────────────────────────────────────────────────────
-- CAPA MERCADO (espejo de Trading.*) — ver docs/SQL.md §Mercado
-- Diseño: NO se copia el desparramo de Mongo. Curvas/BondsMaster materializan lo
-- consultable como columnas y guardan flujos + doc completo en jsonb; market_snapshot
-- es COLUMNAR para replicar la semántica $set parcial de los dos motores.
-- ─────────────────────────────────────────────────────────────────────────────

-- Trading.Curvas — master de instrumentos de renta fija. PK ticker_corto. Flujos como
-- jsonb (shape varía por curva: CER % vs tasa_fija abs — ver CLAUDE.md raíz); `data` =
-- doc completo para no perder campos no materializados.
CREATE TABLE IF NOT EXISTS mercado.curvas (
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
CREATE INDEX IF NOT EXISTS ix_curvas_curva ON mercado.curvas(curva);
CREATE INDEX IF NOT EXISTS ix_curvas_vto   ON mercado.curvas(fecha_vencimiento);

-- Trading.DiasHabiles — calendario hábil argentino (jobs/dias_habiles, holidays.AR).
-- Lo consume la lógica CER-fijado (T-10 hábiles) y el cleanup de curvas. Mongo guarda
-- {fecha:'YYYY-MM-DD'}; acá date tipado. Dual-write desde el job hasta migrar todos
-- los readers → drop Mongo.
CREATE TABLE IF NOT EXISTS mercado.dias_habiles (
    fecha date PRIMARY KEY
);

-- (Trading.BondsMaster → mercado.bonds_master ELIMINADA 2026-06-22: las ONs se
-- consolidaron en mercado.curvas como curva on_<sector>. UNA sola base de bonos.)

-- Trading.MarketSnapshot — estado live por ticker. COLUMNAR a propósito: en Mongo dos
-- motores escriben el mismo doc con $set parcial sin pisarse (valores.py → book/precios
-- cada 1s; curvas.py → analíticos cada 5s). Acá cada escritor upsertea SOLO sus columnas
-- → misma semántica. Un jsonb compartido NO sirve (el merge shallow pisaría al otro motor).
CREATE TABLE IF NOT EXISTS mercado.market_snapshot (
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

-- Trading.TimeSales → tape intradía (stream append-only). Dual-write desde valores.py
-- (flag SNAPSHOT_SQL), retención corta (~7d): el tape muestra SOLO el día y los motores
-- leen el último valor. NO se guardan los enriquecidos (TEA/TEM/duration de curvas.py) —
-- el tape solo usa hora/precio/size/lado. PK surrogate (cada trade es una fila nueva).
CREATE TABLE IF NOT EXISTS mercado.timesales (
    id     bigserial PRIMARY KEY,
    ticker text NOT NULL,
    -- SIN tz a propósito: valores.py guarda el trade como naive en hora ART
    -- (engines/valores.py:296 astimezone(ART).replace(tzinfo=None)). Con timestamptz
    -- psycopg lo etiquetaría UTC y el tape mostraría la hora corrida 3hs. `timestamp`
    -- preserva el mismo valor naive que Mongo → display idéntico.
    ts     timestamp NOT NULL,          -- Mongo: timestamp (naive ART)
    price  numeric,
    size   numeric,
    side   text,                        -- BUY | SELL | MID
    money  numeric
);
CREATE INDEX IF NOT EXISTS ix_timesales_ticker_ts ON mercado.timesales (ticker, ts DESC);
CREATE INDEX IF NOT EXISTS ix_timesales_ts ON mercado.timesales (ts);  -- prune por retención

-- Trading.FuturosDLRSnapshot → snapshot LIVE de futuros DLR (motor reemplaza c/15s).
-- Dual-write desde engines/futuros_dlr.py (flag SNAPSHOT_SQL). El histórico (al cierre)
-- va a mercado_hist (colección FuturosDLR). Passthrough: data jsonb = el doc completo,
-- `vencimiento` columna para filtrar > hoy en la vista.
CREATE TABLE IF NOT EXISTS mercado.futuros_dlr_snapshot (
    ticker      text PRIMARY KEY,
    vencimiento text,                    -- 'YYYYMMDD'
    data        jsonb,
    updated_at  timestamptz DEFAULT now()
);

-- Trading.CaucionSnapshot → snapshot LIVE de caución (motor reemplaza c/15s, 1 doc/moneda).
-- Dual-write desde engines/caucion.py (flag SNAPSHOT_SQL). Histórico al cierre → mercado_hist.
CREATE TABLE IF NOT EXISTS mercado.caucion_snapshot (
    moneda     text PRIMARY KEY,
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);

-- Trading.ForwardsZscore → coeficientes (media/desvío) del z-score por curva (job diario).
-- Dual-write desde jobs/forwards_zscore.py (flag MERCADO_SQL_WRITE).
CREATE TABLE IF NOT EXISTS mercado.forwards_zscore (
    curva text PRIMARY KEY,
    data  jsonb
);

-- Opciones.OptionsSnapshot → chain LIVE de opciones GGAL (grid, motor reemplaza c/1s).
-- Dual-write desde engines/options.py (flag SNAPSHOT_SQL). Greeks (delta/gamma/iv/...) los
-- calcula el motor vía quant/black_scholes — acá se ESPEJAN (no se recalculan). Política
-- "solo strikes vigentes": _purgar_snapshots_fuera_de_mapa borra los symbols que no están
-- en el mapa actual (vencimientos viejos). `updated_at` naive ART/UTC (datetime.now()).
CREATE TABLE IF NOT EXISTS mercado.options_snapshot (
    symbol     text PRIMARY KEY,
    tipo       text,
    vence      text,                    -- 'YYYYMMDD'
    updated_at timestamp,               -- naive (sin tz, como Mongo)
    data       jsonb
);
CREATE INDEX IF NOT EXISTS ix_options_snapshot_updated ON mercado.options_snapshot (updated_at);

-- Opciones.Metadata → 2 docs: config (tasa risk-free + expiries) y vr_ggal (vol referencia
-- ADR/local). Alimenta /opciones/meta. Lo escribe el motor (config) + jobs/volatilidad_ggal
-- (vr_ggal) + la mutación update_opciones_tasa (API). Baseline por sync_postgres; la tasa se
-- dual-writea inmediata al editarla. PK = type, 1 fila por tipo.
CREATE TABLE IF NOT EXISTS mercado.options_metadata (
    type text PRIMARY KEY,                -- 'config' | 'vr_ggal'
    data jsonb
);

-- Opciones.DataHistorica → rollup DIARIO de griegas por contrato (1 fila por fecha+symbol).
-- Alimenta el chart de evolución de griegas (/griegas/opciones). Lo escribe jobs/options_rollup
-- (dual-write incondicional). `fecha` string 'YYYY-MM-DD' (== Mongo, comparación lexicográfica).
CREATE TABLE IF NOT EXISTS mercado.options_data_hist (
    fecha  text NOT NULL,                  -- 'YYYY-MM-DD'
    symbol text NOT NULL,
    data   jsonb,
    PRIMARY KEY (fecha, symbol)
);
CREATE INDEX IF NOT EXISTS ix_options_data_hist_symbol ON mercado.options_data_hist (symbol);

-- Opciones.VR-GGal → serie diaria GGAL local (ARS) + ADR (USD), ~40 ruedas. 2º eje del chart
-- de costo histórico. Lo escribe jobs/volatilidad_ggal (refresca la serie entera c/día). PK =
-- fecha (date). El doc SUMMARY_METRICS de Mongo NO se espeja (no tiene `Date`).
CREATE TABLE IF NOT EXISTS mercado.options_vr (
    fecha date PRIMARY KEY,               -- Mongo: campo `Date`
    data  jsonb
);

-- Opciones.Data → ticks intradía de la chain (1 fila por tick). Alimenta el chart intradía de
-- una opción (/historico/opciones) + el costo histórico de estrategia. Append-only (sin PK
-- natural). Dual-write del motor en guardar_operacion_unica (flag SNAPSHOT_SQL). POLÍTICA
-- "solo vencimiento vigente": _purgar_snapshots_fuera_de_mapa borra los symbols fuera del
-- mapa; jobs/archive_options_data borra ts < hoy ART al cierre → la tabla NO acumula
-- vencimientos viejos. `ts` naive ART (datetime.now()) IGUAL que options_snapshot → timestamp
-- SIN tz, o el chart muestra la hora corrida.
CREATE TABLE IF NOT EXISTS mercado.options_data (
    symbol text NOT NULL,
    ts     timestamp NOT NULL,             -- naive (sin tz, como Mongo)
    data   jsonb
);
CREATE INDEX IF NOT EXISTS ix_options_data_symbol_ts ON mercado.options_data (symbol, ts);
CREATE INDEX IF NOT EXISTS ix_options_data_ts        ON mercado.options_data (ts);

-- Trading.SnapshotsCierre → último cierre por ticker (fallback de precio del PnL).
CREATE TABLE IF NOT EXISTS mercado.snapshots_cierre (
    ticker     text PRIMARY KEY,
    last_price numeric,
    fecha      date
);

-- Trading.SnapshotsCierre → HISTÓRICO completo del cierre diario por bono
-- (jobs/snapshot_cierre.py). Distinto grano/consumidor que mercado.snapshots_cierre.
CREATE TABLE IF NOT EXISTS mercado.snapshots_cierre_hist (
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
CREATE INDEX IF NOT EXISTS ix_sch_ticker ON mercado.snapshots_cierre_hist(ticker, fecha);
CREATE INDEX IF NOT EXISTS ix_sch_curva  ON mercado.snapshots_cierre_hist(curva, fecha);

-- Trading.CanjeCierre — cierre diario de tickers de canje (jobs/cierre_canje.py).
CREATE TABLE IF NOT EXISTS mercado.canje_cierre (
    ticker     text NOT NULL,
    fecha      date NOT NULL,
    price      numeric,
    updated_at timestamptz,
    PRIMARY KEY (ticker, fecha)
);

-- Históricos DIARIOS de la vista mercado (1 tabla genérica): BreakevensHistorico,
-- ForwardsHistorico, FuturosDLR, Caucion, FitParams, FairValueResiduos. Grano:
-- (colección, fecha, subclave curva/ticker/moneda), doc en jsonb. Los snapshots LIVE
-- (BreakevensLive, ForwardsLive, *Snapshot…) NO se espejan acá: migran con su vista vía
-- dual-write del motor (core/pg_mirror, mismo patrón que market_snapshot).
CREATE TABLE IF NOT EXISTS mercado.mercado_hist (
    coleccion text NOT NULL,
    fecha     date NOT NULL,
    k         text NOT NULL DEFAULT '',
    data      jsonb,
    PRIMARY KEY (coleccion, fecha, k)
);

-- ── AGRO / Derivados Agro (vista /derivados → Agro) ──────────────────────────
-- Trading.AgroSnapshot → futuros agro Rosario (Trigo/Maíz/Soja), 1 doc/ticker,
-- motor reemplaza c/5s. Dual-write desde engines/motor_agro.py (flag SNAPSHOT_SQL).
-- Passthrough jsonb; `commodity` columna para filtrar por commodity. NO histórico
-- (mismo criterio que el motor: solo último precio para pase/TNAV).
CREATE TABLE IF NOT EXISTS mercado.agro_snapshot (
    ticker     text PRIMARY KEY,
    commodity  text,                    -- TRIGO | MAIZ | SOJA
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agro_snapshot_commodity ON mercado.agro_snapshot (commodity);

-- Trading.AgroOpcionesSnapshot → opciones agro Rosario (call/put), 1 doc/ticker,
-- motor reemplaza c/5s. Dual-write desde engines/motor_agro_opciones.py (flag
-- SNAPSHOT_SQL). Passthrough jsonb; `commodity` columna para filtrar el panel.
CREATE TABLE IF NOT EXISTS mercado.agro_opciones_snapshot (
    ticker     text PRIMARY KEY,
    commodity  text,                    -- TRIGO | MAIZ | SOJA
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_agro_opc_snapshot_commodity ON mercado.agro_opciones_snapshot (commodity);

-- Derivados.AgroPizarra → carga MANUAL de la mesa (1 doc/commodity). Lo escribe el
-- service api/services/derivados_agro.py::set_pizarra (dual-write incondicional,
-- write_native). Passthrough jsonb; PK = commodity (Mongo _id).
CREATE TABLE IF NOT EXISTS mercado.agro_pizarra (
    commodity text PRIMARY KEY,         -- TRIGO | MAIZ | SOJA
    data      jsonb
);

-- Derivados.CamaraCereales → carga MANUAL de la mesa (5 cereales). Lo escribe el
-- service api/services/camara_cereales.py::set_camara_cereal (dual-write incondicional,
-- write_native). Passthrough jsonb; PK = cereal (Mongo _id).
CREATE TABLE IF NOT EXISTS mercado.camara_cereales (
    cereal text PRIMARY KEY,            -- TRIGO | MAIZ | GIRASOL | SOJA | SORGO
    data   jsonb
);

-- CashFlow.VolumenMercadoAgro → volumen TOTAL del mercado agro por mes/commodity
-- (carga MANUAL mes a mes). Es el DENOMINADOR del share AGRO (/ops/agro serie_share:
-- nuestro / mercado). Lo lee api/services/operaciones_sql.py::ops_agro. Grano
-- (periodo, commodity); `toneladas` materializada (lo único que consume el share),
-- doc completo en jsonb. PK = (periodo, commodity).
CREATE TABLE IF NOT EXISTS mercado.volumen_mercado_agro (
    periodo   text NOT NULL,            -- 'YYYY-MM'
    commodity text NOT NULL,            -- SOJA | TRIGO | MAIZ (MAYÚSCULA, para el join)
    toneladas numeric,
    data      jsonb,
    PRIMARY KEY (periodo, commodity)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- RENTA VARIABLE — Scanner CEDEARs (Trading.{Cedears, CedearsSnapshot,
-- PreciosAcciones, AdrSnapshot, DayTradingStats}). Espejo del dominio scanner;
-- lectura SQL bajo flag SCANNER_SQL (api/services/scanner_sql.py).
-- ─────────────────────────────────────────────────────────────────────────────

-- Trading.Cedears → master categórico de CEDEARs (sector/industria/region/pais,
-- ratio, underlying). Chico (~73 docs). Passthrough: `data jsonb` = doc completo
-- (el scanner usa muchos campos: nombre, ratio_cedear, sector, industria, ...).
-- Columnas materializadas para filtrar el universo activo y joinear por ticker.
CREATE TABLE IF NOT EXISTS mercado.cedears (
    ticker       text PRIMARY KEY,           -- ticker BYMA completo (PK del snapshot)
    ticker_corto text,
    underlying   text,
    activo       boolean,
    data         jsonb
);
CREATE INDEX IF NOT EXISTS ix_cedears_corto ON mercado.cedears (ticker_corto);

-- Trading.CedearsSnapshot → snapshot LIVE del CEDEAR en ARS (motor reescribe c/1s,
-- 1 doc por ticker). Dual-write desde engines/motor_cedears.py (flag SNAPSHOT_SQL).
-- Passthrough: data jsonb = doc completo (last/open/high/low/close/bid/offer/
-- spread/vwap/volume/total_money/updated_at). `updated_at` AWARE UTC (Mongo:
-- datetime.now(UTC)) → timestamptz (no corre la hora).
CREATE TABLE IF NOT EXISTS mercado.cedears_snapshot (
    ticker     text PRIMARY KEY,
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);

-- Trading.AdrSnapshot → quote LIVE USD del subyacente (Finnhub, jobs/adr_live.py
-- cada 15 min, 1 doc por underlying). Dual-write bajo SNAPSHOT_SQL. Passthrough:
-- data jsonb = {ticker,c,pc,o,h,l,t,updated_at}. `updated_at` AWARE UTC.
CREATE TABLE IF NOT EXISTS mercado.adr_snapshot (
    ticker     text PRIMARY KEY,             -- underlying (US symbol)
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);

-- Trading.PreciosAcciones → velas DIARIAS (EOD) del subyacente USD (Yahoo,
-- jobs/precios_acciones_daily.py). Timeseries en Mongo; acá tabla columnar normal.
-- Grano (ticker, fecha). `fecha` es DATE (en Mongo es datetime naive UTC a las 00h
-- US — el scanner ya lo trata como anchor de día; date evita el problema de tz).
CREATE TABLE IF NOT EXISTS mercado.precios_acciones (
    ticker text NOT NULL,                    -- underlying (US symbol)
    fecha  date NOT NULL,
    open   numeric,
    high   numeric,
    low    numeric,
    close  numeric,
    volume numeric,
    PRIMARY KEY (ticker, fecha)
);
CREATE INDEX IF NOT EXISTS ix_precios_acciones_ticker_fecha
    ON mercado.precios_acciones (ticker, fecha);

-- Trading.DayTradingStats → resumen diario de scalping por CEDEAR (jobs/
-- day_trading_stats.py, post-cierre, 1 doc por fecha+ticker). Grano (fecha,ticker).
-- `fecha` es DATE (en Mongo es string 'YYYY-MM-DD'). `data jsonb` = doc completo
-- (vueltas_05/075/10/15, rango_pct, total_money, flujo_compra_pct, n_minutos).
CREATE TABLE IF NOT EXISTS mercado.day_trading_stats (
    fecha  date NOT NULL,
    ticker text NOT NULL,                    -- ticker_corto
    data   jsonb,
    PRIMARY KEY (fecha, ticker)
);
CREATE INDEX IF NOT EXISTS ix_dts_ticker_fecha
    ON mercado.day_trading_stats (ticker, fecha DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- MACRO — series económicas (BCRA / argentina_datos / REM)
-- ─────────────────────────────────────────────────────────────────────────────

-- Trading.{CER, DOLAR, BADLAR, TAMAR, RiesgoPais, InflacionMensual, InflacionInteranual}
-- (jobs.bcra + jobs.argentina_datos, shape {fecha:'YYYY-MM-DD', valor}). Las 7
-- colecciones-serie colapsan en UNA tabla larga; `serie` = nombre de la colección Mongo.
CREATE TABLE IF NOT EXISTS macro.series_macro (
    serie text NOT NULL,
    fecha date NOT NULL,
    valor numeric,
    PRIMARY KEY (serie, fecha)
);

-- Trading.REM (jobs.argentina_datos) — consenso IPC INDEC por informe/período.
CREATE TABLE IF NOT EXISTS macro.rem (
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
CREATE INDEX IF NOT EXISTS ix_rem_periodo ON macro.rem(periodo);

-- ─────────────────────────────────────────────────────────────────────────────
-- MANAGER — usuarios de la app (RBAC), matriz de roles, grupos de scope
-- ─────────────────────────────────────────────────────────────────────────────

-- Manager.Users — usuarios de la app (RBAC). email lowercased.
CREATE TABLE IF NOT EXISTS manager.manager_users (
    email text PRIMARY KEY
);
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS role            text;
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS enabled         boolean;
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS auto_registered boolean;
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS notes           text;
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS last_seen_at    timestamptz;
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS created_at      timestamptz;
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS updated_at      timestamptz;

-- Manager.RoleMatrix — qué módulos ve cada rol. Filas-largas (role, module). En Mongo es
-- 1 doc por rol con un array modules. Vacío → DEFAULT_MATRIX (en core/roles.py).
CREATE TABLE IF NOT EXISTS manager.role_matrix (
    role   text NOT NULL,
    module text NOT NULL,
    PRIMARY KEY (role, module)
);

-- Manager.Grupos — scope de cuentas por usuario. emails/id_cuentas son arrays (lowercased
-- los emails). cuentas_visibles: 0 grupos → None (ve todo); ≥1 → unión de id_cuentas.
CREATE TABLE IF NOT EXISTS manager.grupos (
    id         text PRIMARY KEY,        -- str(ObjectId) de Mongo
    nombre     text,
    emails     text[] NOT NULL DEFAULT '{}',
    id_cuentas text[] NOT NULL DEFAULT '{}',
    creado_por text,
    creado_at  timestamptz,
    updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_grupos_emails ON manager.grupos USING gin(emails);

-- Manager.JobRuns — historial de corridas de jobs/crons (lo escribe core/job_runs.py
-- JobRunLogger). Lo leen el panel Diagnóstico (/jobs/history, /jobs/history/stats),
-- el registry de frescura y el informe de salud. PK = run_id (str(ObjectId) Mongo del
-- doc, o uuid si SQL-native). Columnas materializadas para filtrar/ordenar/agrupar:
-- tipo (nombre del job), started_at/finished_at (timestamptz: el writer usa
-- datetime.now(UTC) AWARE), status (ok|partial|error). El resto del doc (stats,
-- errors, log, elapsed_s) vive en `data` jsonb. TTL 60d lo aplica el writer/cleanup.
CREATE TABLE IF NOT EXISTS manager.job_runs (
    run_id      text PRIMARY KEY,            -- str(ObjectId) del doc Mongo (o uuid SQL-native)
    tipo        text,
    started_at  timestamptz,
    finished_at timestamptz,
    status      text,                        -- ok | partial | error
    data        jsonb                        -- doc completo (stats, errors, log, elapsed_s)
);
CREATE INDEX IF NOT EXISTS ix_job_runs_tipo_started ON manager.job_runs(tipo, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_job_runs_started      ON manager.job_runs(started_at DESC);

-- Manager.RoleAudit — append-only de cambios de roles/usuarios (lo escribe
-- core/roles.py en upsert_user/delete_user/set_role_modules). Lo lee el panel
-- /manager/roles/audit. PK = audit_id (str(ObjectId) Mongo). Columnas materializadas
-- ts (timestamptz: el writer usa datetime.now(UTC) AWARE), actor, action, target;
-- before/after quedan en `data` jsonb.
CREATE TABLE IF NOT EXISTS manager.role_audit (
    audit_id text PRIMARY KEY,               -- str(ObjectId) del doc Mongo
    ts       timestamptz,
    actor    text,
    action   text,                           -- upsert_user | delete_user | set_role_modules
    target   text,
    data     jsonb                           -- {before, after} + el resto del doc
);
CREATE INDEX IF NOT EXISTS ix_role_audit_ts ON manager.role_audit(ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- HOME — watchlist + noticias + calendario económico
-- ─────────────────────────────────────────────────────────────────────────────

-- News.Headlines — RSS/Finnhub (home). key=url. data jsonb = doc completo (fechas a ISO).
CREATE TABLE IF NOT EXISTS home.news_headlines (
    url               text PRIMARY KEY,
    fecha_publicacion timestamptz,
    fuente            text,
    categoria         text,
    titulo            text,
    data              jsonb
);
CREATE INDEX IF NOT EXISTS ix_news_fecha ON home.news_headlines(fecha_publicacion DESC);

-- Market.Quotes — watchlist /argy (key=symbol). data jsonb = doc completo (anchors).
CREATE TABLE IF NOT EXISTS home.market_quotes (
    symbol text PRIMARY KEY,
    grupo  text,
    data   jsonb
);

-- Market.EconomicCalendar — eventos macro. PK NATURAL (evt_ts, country, event) = el
-- unique index del ESCRITOR (jobs/economic_calendar upsertea por time/country/event) →
-- dual-write limpio. Migración v1 (hkey=md5 del doc, generaba fila nueva por update):
-- si quedó alguna instalación con la columna hkey, se dropea y la repuebla el sync.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_name = 'market_calendar' AND column_name = 'hkey') THEN
    EXECUTE 'DROP TABLE ' || (SELECT table_schema FROM information_schema.tables
                              WHERE table_name = 'market_calendar' LIMIT 1) || '.market_calendar';
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS home.market_calendar (
    evt_ts  timestamptz NOT NULL,            -- el filtro de rango usa el prefijo de la PK
    country text NOT NULL,
    event   text NOT NULL,
    impact  integer,
    data    jsonb,
    PRIMARY KEY (evt_ts, country, event)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- PARTNER — espejo de la base Mongo `ACAPortfolio` del servicio externo
-- (app SEPARADA `partner_api/`, NO se monta en api/main). Migración Mongo→SQL
-- para poder apagar Mongo por completo. Ver docs/PARTNER_API.md y docs/SQL.md.
--
-- Schema PROPIO `partner` (no se mezcla con el núcleo de la mesa): el dato es de
-- un tercero, lo escribe/lee un proceso aparte. Las tablas se crean con este DDL
-- (idempotente) y/o por el writer/auth en _ensure_schema(). search_path de
-- core/postgres.py NO incluye `partner` a propósito → el partner_api usa su
-- propia conexión (partner_api/pg.py) y SIEMPRE califica `partner.<tabla>`, así
-- la API de la mesa nunca resuelve sin querer una tabla de un tercero.
-- ─────────────────────────────────────────────────────────────────────────────

-- ACAPortfolio.Cartera → snapshot diario de posiciones de las cuentas habilitadas
-- (1 fila por fecha+id_cuenta+unidad). Lo escribe jobs/partner_export.py (2×/día,
-- dual-write Mongo+SQL bajo flag PARTNER_SQL_WRITE). Shape FIJO y conocido (el job
-- arma el doc con campos explícitos) → columnas materializadas, sin jsonb. `fecha`
-- es string ISO 'YYYY-MM-DD' del día hábil ARGENTINO (NO date naive) → se guarda
-- como `date` tipado (parse trivial, sin tz). `id_cuenta` es string ("463"). PK
-- natural = el grano del export (idempotente por fecha/cuenta).
CREATE TABLE IF NOT EXISTS partner.cartera (
    fecha       date NOT NULL,            -- Mongo: string 'YYYY-MM-DD' (día hábil AR)
    id_cuenta   text NOT NULL,
    unidad      text NOT NULL,
    cuenta      text,                     -- "[id] NOMBRE"
    cantidad    numeric,
    precio      numeric,
    valuacion   numeric,
    exported_at timestamptz,              -- Mongo: datetime AWARE UTC → timestamptz
    PRIMARY KEY (fecha, id_cuenta, unidad)
);
CREATE INDEX IF NOT EXISTS ix_partner_cartera_cuenta_fecha
    ON partner.cartera (id_cuenta, fecha DESC);

-- ACAPortfolio.ApiUsers → credenciales del proveedor (usuario/hash/enabled). Lo
-- escribe scripts/partner_user.py (dual-write Mongo+SQL bajo flag PARTNER_SQL_WRITE);
-- lo lee partner_api/auth.py + odata.py (login + Basic). password_hash es el
-- "salt_hex$hash_hex" de partner_api/security.py — se espeja TAL CUAL. PK = username.
CREATE TABLE IF NOT EXISTS partner.api_users (
    username      text PRIMARY KEY,
    password_hash text,
    enabled       boolean,
    created_at    timestamptz             -- Mongo: datetime AWARE UTC → timestamptz
);
