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
CREATE SCHEMA IF NOT EXISTS mcp;
CREATE SCHEMA IF NOT EXISTS research;
CREATE SCHEMA IF NOT EXISTS ia;

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
    ('public','rubros','mercado'),
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
    ('public','ordenes_live','operaciones'),
    ('public','ordenes_audit','operaciones'),
    ('public','motor_heartbeat','operaciones'),
    ('public','operativas_mep','operaciones'),
    ('public','brackets_live','operaciones'),
    ('public','triggers_mep','operaciones'),
    ('public','ordenes_idempotency','operaciones'),
    ('public','accounts_descubiertas','operaciones'),
    ('public','series_macro','macro'),
    ('public','rem','macro'),
    ('public','dolar','valuaciones'),
    ('public','portfolio_snapshot','valuaciones'),
    ('public','pnl_totales_cache','valuaciones'),
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

-- ACA VALORES — set de cuentas editable desde el Manager (Manager → CLIENTES → ACA VALORES).
-- Filtro por id_cuenta en la vista OPERACIONES: ver Todas / Solo ACA VALORES / Sin ACA VALORES.
CREATE TABLE IF NOT EXISTS clientes.aca_valores (
    id_cuenta       text PRIMARY KEY,
    denominacion    text,
    actualizado_por text,
    actualizado_at  timestamptz
);

-- Objetivos comerciales (CONTROL COMERCIAL) — los carga la jefatura in-view. Granularidad
-- mensual por comercial; la vista los agrega por el período elegido. Self-create también en
-- api/services/control_comercial_sql.py.
CREATE TABLE IF NOT EXISTS clientes.objetivos_comerciales (
    operador_email      text NOT NULL,
    anio                int  NOT NULL,
    mes                 int  NOT NULL CHECK (mes BETWEEN 1 AND 12),
    volumen_objetivo    numeric,
    comisiones_objetivo numeric,
    actualizado_por     text,
    actualizado_at      timestamptz DEFAULT now(),
    PRIMARY KEY (operador_email, anio, mes)
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
-- ARANCEL/comisiones (perf 2026-06-29): el predicado `arancel<>0 AND etapa<>'solicitud'`
-- se repite en informe_comercial, _rollup_por_cuenta, _aranceles_por_cuenta,
-- informe_segmento_detalle, ops_aranceles y control_comercial. Sin índice = scan de
-- ~490k filas. Parciales (chicos: solo las filas con arancel real).
CREATE INDEX IF NOT EXISTS ix_ops_arancel_cuenta ON operaciones.operaciones(id_cuenta)
    WHERE arancel <> 0 AND etapa IS DISTINCT FROM 'solicitud';
CREATE INDEX IF NOT EXISTS ix_ops_arancel_concert ON operaciones.operaciones(concertacion)
    WHERE arancel <> 0 AND etapa IS DISTINCT FROM 'solicitud';

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

-- ── MOTOR DE ÓRDENES (OPERAR) — base Mongo `Operaciones.*` (TRANSACCIONAL, real-time).
-- Migrado 2026-06-23. Dual-write BEST-EFFORT del motor/services bajo flag ORDENES_SQL_WRITE
-- (try/except, DESPUÉS del write a Mongo → un fallo de SQL NUNCA bloquea ni afecta la orden
-- real al broker). Lectura dual-run bajo ORDENES_SQL. Passthrough jsonb + columnas clave
-- para filtrar (account/estado/ts). Timestamps aware UTC (datetime.now(UTC)) → timestamptz.
CREATE TABLE IF NOT EXISTS operaciones.ordenes_live (
    cl_ord_id  text PRIMARY KEY,
    account    text,
    ticker     text,
    estado     text,
    updated_at timestamptz,
    data       jsonb
);
CREATE INDEX IF NOT EXISTS ix_ordenes_live_account ON operaciones.ordenes_live (account, updated_at DESC);

CREATE TABLE IF NOT EXISTS operaciones.ordenes_audit (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          timestamptz,
    kind        text,
    cl_ord_id   text,
    account     text,
    actor_email text,
    data        jsonb              -- payload + ws_cl_ord_id + resto del doc
);
CREATE INDEX IF NOT EXISTS ix_ordenes_audit_clord ON operaciones.ordenes_audit (cl_ord_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_ordenes_audit_ts    ON operaciones.ordenes_audit (ts DESC);

CREATE TABLE IF NOT EXISTS operaciones.motor_heartbeat (
    id         text PRIMARY KEY,           -- 'current' (singleton)
    updated_at timestamptz,
    data       jsonb
);

CREATE TABLE IF NOT EXISTS operaciones.operativas_mep (
    id      text PRIMARY KEY,              -- str(_id) Mongo
    account text,
    rueda   text,
    ts      timestamptz,
    data    jsonb
);
CREATE INDEX IF NOT EXISTS ix_operativas_mep_account ON operaciones.operativas_mep (account, ts DESC);

CREATE TABLE IF NOT EXISTS operaciones.brackets_live (
    cl_ord_id text PRIMARY KEY,
    account   text,
    estado    text,
    data      jsonb
);

CREATE TABLE IF NOT EXISTS operaciones.triggers_mep (
    id      text PRIMARY KEY,
    account text,
    data    jsonb
);

CREATE TABLE IF NOT EXISTS operaciones.ordenes_idempotency (
    clave text PRIMARY KEY,
    ts    timestamptz,
    data  jsonb
);

CREATE TABLE IF NOT EXISTS operaciones.accounts_descubiertas (
    account_id         text PRIMARY KEY,
    activa             boolean,
    last_discovered_at timestamptz,
    data               jsonb               -- last_snapshot (ars/usd/n_pos) + resto
);
CREATE INDEX IF NOT EXISTS ix_accounts_desc_activa ON operaciones.accounts_descubiertas (activa);

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

-- Marca DURABLE de alquiler por (título, cuenta) — cuentas propias 100/255/256.
-- La escribe/lee la vista "Títulos en Alquiler" (api/services/tenencia_hd.py, que
-- además la self-crea). Netea la Tenencia Valorizada dentro de [desde, hasta].
CREATE TABLE IF NOT EXISTS portafolio.alquiler (
    id_cuenta   text NOT NULL,
    unidad      text NOT NULL,
    en_alquiler boolean DEFAULT false,
    cantidad    numeric,
    desde       date,
    hasta       date,
    updated_by  text,
    updated_at  timestamptz,
    PRIMARY KEY (id_cuenta, unidad)
);

-- PORTFOLIO ALQUILER (tab de "Títulos en Alquiler"): lista CURADA de títulos que
-- el back office elige a mano (fila "+" con buscador). La vista los muestra como
-- Tenencia Valorizada (serie diaria desde 01/06/2026 + PX/100/255/256/Total).
-- La escribe/lee api/services/tenencia_hd.py (que además la self-crea).
CREATE TABLE IF NOT EXISTS portafolio.alquiler_portfolio (
    unidad      text PRIMARY KEY,
    updated_by  text,
    updated_at  timestamptz
);

-- Nominales EN ALQUILER por (título, cuenta, fecha) — editables desde la tab.
-- Semántica CARRY-FORWARD: la edición de un día rige de ese día en adelante
-- hasta la próxima edición (0 = apaga). Es la fuente del filtro SIN ALQUILER
-- de Tenencia Valorizada (base − alquiler, puede dar negativo).
CREATE TABLE IF NOT EXISTS portafolio.alquiler_portfolio_nominales (
    unidad      text NOT NULL,
    id_cuenta   text NOT NULL,
    fecha       date NOT NULL,
    cantidad    numeric NOT NULL,
    updated_by  text,
    updated_at  timestamptz,
    PRIMARY KEY (unidad, id_cuenta, fecha)
);

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

-- Valuaciones.PnLTotalesCache — cache del PnL de TODAS las cuentas (vista TOTALES,
-- /api/portfolio/pnl-todas). NO es agregable en vivo: cada fila es el cost-basis
-- weighted-average de la cuenta (Python, recorre boletos+posición), por eso se precalcula.
-- Lo escribe el cron jobs.pnl_totales_precompute (dual-write Mongo+SQL, swap atómico cada
-- 30min); el service SQL solo lo lee + filtra. Passthrough jsonb (rows incluye el detalle de
-- boletos por ticker, demasiado anidado/variable para columnar). PK = id_cuenta; `cuenta`
-- materializado (no se filtra/ordena por él en SQL — se lee de jsonb). computed_at: el writer
-- usa datetime.now(UTC) AWARE → timestamptz (el cast no corre la hora).
CREATE TABLE IF NOT EXISTS valuaciones.pnl_totales_cache (
    id_cuenta   text PRIMARY KEY,
    cuenta      text,
    rows        jsonb,        -- una entrada por ticker (con boletos anidados)
    totales     jsonb,        -- agregados ARS+USD de la cuenta
    computed_at timestamptz DEFAULT now()
);

-- valuaciones.pnl_historico — cuaderno de PnL diario de carga MANUAL (vista TRADING
-- → PNL HISTÓRICO). NO lo alimenta el motor de PnL: el usuario tipea el PnL de cada
-- día hábil y el acumulado (total desde el 1-jul-2026 + mensual) se calcula al leer.
-- `cuenta` es etiqueta LIBRE ('General' por defecto); cada cuenta es su propio
-- cuaderno → clave (fecha, cuenta). Borrar el monto = borrar la fila.
CREATE TABLE IF NOT EXISTS valuaciones.pnl_historico (
    fecha        date        NOT NULL,
    cuenta       text        NOT NULL DEFAULT 'General',
    monto        numeric     NOT NULL,
    actualizado  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, cuenta)
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

-- options_data_hist → rollup DIARIO de griegas por contrato (1 fila por fecha+symbol).
-- Alimenta el chart de evolución de griegas (/griegas/opciones). SQL-NATIVE desde el cutover
-- 2026-06-24: lo escribe jobs/options_rollup (write_native, Opciones.DataHistorica Mongo dropeada);
-- lo lee api/services/opciones_sql. `fecha` string 'YYYY-MM-DD' (comparación lexicográfica).
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
-- get_forwards: `DISTINCT ON (k) ... WHERE coleccion=X ORDER BY k, fecha DESC`. La PK
-- (coleccion,fecha,k) NO sirve para ese orden (k antes que fecha) → sort en memoria.
-- Este índice lo cubre y crece bien con el histórico (perf 2026-06-29).
CREATE INDEX IF NOT EXISTS ix_mercado_hist_col_k_fecha
    ON mercado.mercado_hist (coleccion, k, fecha DESC);

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

-- Breakevens overrides → curaduría manual de pares Lecap↔CER (Manager). Una fila
-- por par EXCLUIDO. El reader api/services/mercado_hist_sql.get_breakevens filtra
-- estos pares (el motor los sigue calculando; se ocultan en la vista). Lo escribe
-- api/services/breakevens_admin.py (self-create + upsert/delete). PK = (lecap, cer)
-- en tickers CORTOS (S13N6, TX26).
CREATE TABLE IF NOT EXISTS mercado.breakevens_overrides (
    lecap      text NOT NULL,
    cer        text NOT NULL,
    updated_by text,
    updated_at timestamptz,
    PRIMARY KEY (lecap, cer)
);

-- Tasas de cobertura ON / Pagaré → 2 inputs manuales GLOBALES de la tab DATOS
-- (una sola fila, id='GLOBAL'). Alimentan las columnas Pagaré/ON del "Pase con
-- Cobertura". Lo escribe api/services/camara_cereales.py::set_tasas_cobertura
-- (self-create + upsert). data jsonb = {tasa_on, tasa_pagare, updated_by, updated_at}.
CREATE TABLE IF NOT EXISTS mercado.agro_tasas_cobertura (
    id         text PRIMARY KEY,        -- siempre 'GLOBAL'
    data       jsonb,
    updated_at timestamptz
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
    rubro        text,                        -- clasificación de negocio (reemplaza "sector"),
                                              -- controlada por mercado.rubros, editable en Manager
    es_ia        boolean,                     -- pertenece a la cadena de valor de IA (filtro/mapeo)
    data         jsonb
);
CREATE INDEX IF NOT EXISTS ix_cedears_corto ON mercado.cedears (ticker_corto);
-- Columnas agregadas 2026-06-24 (renta variable: rubro + es_ia). ADD COLUMN IF NOT EXISTS
-- para instalaciones donde la tabla ya existía sin estas columnas.
ALTER TABLE mercado.cedears ADD COLUMN IF NOT EXISTS rubro text;
ALTER TABLE mercado.cedears ADD COLUMN IF NOT EXISTS es_ia boolean;
-- RIC (Refinitiv Instrument Code): identidad del subyacente en Refinitiv/LSEG
-- (ej. RKLB → 'RKLB.O'). Carga MANUAL. Clave para la capa ANÁLISIS/RESEARCH
-- (fundamentals via lseg-data). Ver docs/RESEARCH_REFINITIV.md.
ALTER TABLE mercado.cedears ADD COLUMN IF NOT EXISTS ric text;
-- Ratio de conversión del CEDEAR (cuántos CEDEARs = 1 acción del subyacente,
-- ej. AAPL 10:1 → 10). Carga MANUAL en Manager → TÍTULOS → RENTA VARIABLE.
-- Insumo del CCL implícito de la vista TRADING → REUTERS:
--   ccl = (precio_cedear_ars × ratio) / precio_adr_usd
ALTER TABLE mercado.cedears ADD COLUMN IF NOT EXISTS ratio numeric;

-- Catálogo CONTROLADO de rubros (renta variable). Lista cerrada que alimenta el dropdown
-- del editor en Manager → TÍTULOS → RENTA VARIABLE (no se escribe libre: se elige uno o se
-- crea con el botón). Análogo a los niveles de segmentación de clientes.
CREATE TABLE IF NOT EXISTS mercado.rubros (
    rubro      text PRIMARY KEY,
    es_ia_def  boolean DEFAULT false,         -- sugerencia de es_ia al asignar este rubro (editable por cuenta)
    creado_at  timestamptz DEFAULT now()
);

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

-- PRUEBA feed Eikon/Workspace (2026-07-16): quote LIVE del subyacente US de cada
-- CEDEAR, alimentado por scripts/eikon_feed_simple.py (PC oficina, Workspace logueado)
-- vía POST /api/ingest/eikon/quotes — mismo patrón que el dólar MAE. Camino
-- SEPARADO de adr_snapshot (Finnhub 15 min): conviven, nadie lee esta tabla
-- todavía. Passthrough: data jsonb = {ticker, ric, last, bid, ask, ...}.
CREATE TABLE IF NOT EXISTS mercado.eikon_snapshot (
    ticker     text PRIMARY KEY,             -- underlying (US symbol)
    ric        text,                          -- identidad Refinitiv (ej. AAPL.O)
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);

-- Fundamentals CURADOS por subyacente (ficha de empresa del tab REUTERS,
-- 2026-07-17). El feed de oficina los trae 1 vez por día por RIC suscripto
-- (TR.* de Eikon: valuación, márgenes, salud financiera, consenso, serie 5
-- años) vía POST /api/ingest/eikon/fundamentals. Passthrough jsonb — el
-- contrato de campos vive en el feed (scripts/eikon_feed_simple.py).
CREATE TABLE IF NOT EXISTS mercado.eikon_fundamentals (
    ticker     text PRIMARY KEY,             -- underlying (US symbol)
    ric        text,
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
-- Permiso per-usuario (no por rol) para VER/EDITAR Control Comercial. Lo tilda el
-- admin en /manager → Usuarios. NULL/false = sin acceso (default-deny; admin siempre entra).
ALTER TABLE manager.manager_users ADD COLUMN IF NOT EXISTS control_comercial boolean;

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

-- ============================================================================
-- DECOMISO MONGO — tablas faltantes (2026-06-28). Cada una es el destino SQL de
-- una colección Mongo que hasta hoy NO tenía espejo. Shapes derivados del CÓDIGO
-- del writer (no inventados). Al aplicarlas, cada motor/job pasa a SQL-native.
-- ============================================================================

-- Trading.CedearsTimeSales → tape intradía de CEDEARs (engines/motor_cedears.py,
-- insert_many cada ~1s; alto throughput). Append-only, se VACÍA al cierre (cron
-- jobs/cleanup_cedears_timesales.py → DELETE/TRUNCATE). Sin clave natural única →
-- PK identity; el índice (ticker_corto, ts) sirve a scanner/day_trading.
CREATE TABLE IF NOT EXISTS mercado.cedears_time_sales (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticker       text,
    ticker_corto text,
    ts           timestamptz,            -- Mongo: "timestamp" (UTC aware)
    price        numeric,
    size         numeric,
    side         text,                   -- BUY | SELL | MID
    money        numeric
);
CREATE INDEX IF NOT EXISTS ix_cedears_ts_corto_ts
    ON mercado.cedears_time_sales (ticker_corto, ts DESC);

-- OHLC diario por CEDEAR (ventana móvil ~20 ruedas) para pivots sobre el CEDEAR
-- en ARS. Lo escribe jobs/cedears_ohlc_daily.py tras el cierre, copiando OP/HI/LO
-- del snapshot + el last como close del día (el `close` del snapshot es el cierre
-- de AYER, no se usa). El job poda las ruedas más viejas → la tabla no crece.
CREATE TABLE IF NOT EXISTS mercado.cedears_ohlc_daily (
    ticker_corto text NOT NULL,
    fecha        date NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    PRIMARY KEY (ticker_corto, fecha)
);
CREATE INDEX IF NOT EXISTS ix_cedears_ohlc_tk_fecha
    ON mercado.cedears_ohlc_daily (ticker_corto, fecha DESC);

-- Trading.SnapshotsSinteticos → histórico diario de sintéticos (jobs/snapshot_sinteticos.py,
-- upsert por (ts_snapshot, tipo_sintetico, ticker)). Campos comunes columnar + el
-- resto (px/vto/te/tna/cobro/descalce/...) en `data` jsonb (varían por tipo).
CREATE TABLE IF NOT EXISTS mercado.snapshots_sinteticos (
    ts_snapshot    date NOT NULL,        -- Mongo: string ISO 'YYYY-MM-DD'
    tipo_sintetico text NOT NULL,        -- long_lecap | short_dlk | ...
    ticker         text NOT NULL,
    spot           numeric,
    spot_source    text,
    spot_ts        timestamptz,
    te             numeric,
    tna            numeric,
    data           jsonb,                -- campos específicos del tipo
    PRIMARY KEY (ts_snapshot, tipo_sintetico, ticker)
);

-- Trading.UVA → valor UVA del BCRA (carga manual, 1 valor por fecha). Lo lee
-- macro.py::get_ultimo_uva (último por fecha) y jobs/segmentar_patrimonial.py.
CREATE TABLE IF NOT EXISTS macro.uva (
    fecha date PRIMARY KEY,
    valor numeric                        -- Mongo: valor_uva
);

-- Valuaciones.DolarSnapshot → snapshot LIVE MEP/CCL (engines/dolares.py, replace
-- cada 5s, 1 doc fijo _id='current'). Una sola fila viva.
CREATE TABLE IF NOT EXISTS valuaciones.dolar_snapshot (
    id         text PRIMARY KEY DEFAULT 'current',
    ts         timestamptz,             -- Mongo: "timestamp"
    al30_offer numeric,
    al30d_bid  numeric,
    al30c_bid  numeric,
    mep        numeric,
    ccl        numeric,
    canje      numeric,
    source     text
);

-- Valuaciones.DolarOficialLive → feed MAE mayorista (la PC de oficina pega a
-- POST /api/ingest/dolar-oficial → la API escribe). Upsert por instrumento.
-- `data` = dict crudo de MAE (precioUltimo/variacion/...); el filtro canónico del
-- oficial es ticker=UST$T, segmento=M, plazo=000.
CREATE TABLE IF NOT EXISTS valuaciones.dolar_oficial_live (
    ticker          text NOT NULL,
    codigo_segmento text NOT NULL,
    codigo_plazo    text NOT NULL,
    data            jsonb,
    updated_at      timestamptz,
    PRIMARY KEY (ticker, codigo_segmento, codigo_plazo)
);

-- Valuaciones.Dolar (histórico MEP/CCL, append-only cada 15min) — la tabla ya
-- existía con (timestamp, mep). Se agregan las columnas del doc completo para que
-- el motor escriba SQL-native sin perder al30/ccl/canje.
ALTER TABLE valuaciones.dolar ADD COLUMN IF NOT EXISTS al30_offer numeric;
ALTER TABLE valuaciones.dolar ADD COLUMN IF NOT EXISTS al30d_bid  numeric;
ALTER TABLE valuaciones.dolar ADD COLUMN IF NOT EXISTS al30c_bid  numeric;
ALTER TABLE valuaciones.dolar ADD COLUMN IF NOT EXISTS ccl        numeric;
ALTER TABLE valuaciones.dolar ADD COLUMN IF NOT EXISTS canje      numeric;

-- Trading.PortfolioSnapshot — la tabla ya existía con (ticker, last/closing). Falta
-- updated_at (el motor lo escribe cada 1s).
ALTER TABLE valuaciones.portfolio_snapshot ADD COLUMN IF NOT EXISTS updated_at timestamptz;

-- CashFlow.Accionistas — la tabla ya existía con solo `cuenta`. El router lee también
-- el nombre del accionista.
ALTER TABLE clientes.accionistas ADD COLUMN IF NOT EXISTS accionista text;

-- Trading.AdhocSubscriptions → suscripciones efímeras del operador (core/adhoc_subscriptions.py,
-- CRUD + TTL 7d). Postgres no tiene TTL nativo → el barrido lo hace un cron sobre expires_at.
CREATE TABLE IF NOT EXISTS mercado.adhoc_subscriptions (
    ticker       text PRIMARY KEY,
    created_at   timestamptz,
    last_used_at timestamptz,
    expires_at   timestamptz
);
CREATE INDEX IF NOT EXISTS ix_adhoc_subs_expires ON mercado.adhoc_subscriptions (expires_at);

-- Manager.PyRofexInstruments → lista canónica de instrumentos operables por CFI code
-- (valida órdenes ANTES de enviarlas). CRÍTICA: si queda sin poblar, el envío de
-- órdenes falla. La repuebla scripts/discovery_pyrofex.py (sesión pyRofex). PK = CFI.
CREATE TABLE IF NOT EXISTS manager.pyrofex_instruments (
    cficode     text PRIMARY KEY,        -- Mongo: _id
    count       integer,
    underlyings jsonb,                   -- [str]
    instruments jsonb                    -- [{ticker, underlying, maturity, currency, ...}]
);

-- Manager.PyRofexDiscovery → resumen singleton del último discovery (totales + por CFI).
CREATE TABLE IF NOT EXISTS manager.pyrofex_discovery (
    id                text PRIMARY KEY DEFAULT 'current',
    total_instruments integer,
    by_cficode        jsonb,             -- [{cficode, count, sample_tickers}]
    generated_at      timestamptz
);

-- Manager.HealthReports → informe de salud (jobs/informe_salud.py, insert cada hora,
-- TTL 45d via cron). Cabecera columnar + cuerpo (motores/bases/jobs/sql_sync/mongo) en jsonb.
CREATE TABLE IF NOT EXISTS manager.health_reports (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts        timestamptz,
    en_rueda  boolean,
    veredicto text,                      -- 🟢 | 🟡 | 🔴
    problemas jsonb,
    data      jsonb                      -- {motores, bases, jobs, sql_sync, mongo}
);
CREATE INDEX IF NOT EXISTS ix_health_reports_ts ON manager.health_reports (ts DESC);

-- Manager.WatchdogAlertas → cooldown por job/motor (jobs/watchdog.py, upsert por _id).
CREATE TABLE IF NOT EXISTS manager.watchdog_alertas (
    id            text PRIMARY KEY,      -- job name | 'motor:<x>' | 'db_scan'
    last_alert_at timestamptz,
    etimes        integer,
    pid           integer,
    edad_s        integer
);

-- Auto-control de calidad de datos (jobs/controles_datos.py): una fila por anomalía
-- detectada (control_id = chequeo, item_key = clave estable: ticker/unidad/cuenta).
-- El runner diffea contra este estado para alertar SOLO lo nuevo y marcar resueltos.
CREATE TABLE IF NOT EXISTS manager.controles_datos (
    control_id  text NOT NULL,
    item_key    text NOT NULL,
    detalle     text,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    resuelto_at timestamptz,              -- NULL = anomalía vigente
    PRIMARY KEY (control_id, item_key)
);
CREATE INDEX IF NOT EXISTS ix_controles_datos_activos
    ON manager.controles_datos(control_id) WHERE resuelto_at IS NULL;

-- Cuarentena de símbolos que ROFEX rechaza ("Product don't exist"). El WS los
-- persiste (core/simbolos_cuarentena) y las suscripciones los excluyen mientras
-- last_seen < 7 días (después se reintentan solos). Nunca se borra: reversible.
CREATE TABLE IF NOT EXISTS mercado.simbolos_cuarentena (
    ticker     text PRIMARY KEY,
    motivo     text,
    first_seen timestamptz NOT NULL DEFAULT now(),
    last_seen  timestamptz NOT NULL DEFAULT now(),
    rechazos   integer NOT NULL DEFAULT 1
);

-- Manager.AranceelesJobRuns → tracking UI del backfill de aranceles (jobs/aranceles.py,
-- 1 doc/run + updates). Cabecera columnar + stats/ejemplos/errores en jsonb.
CREATE TABLE IF NOT EXISTS manager.aranceles_job_runs (
    id          text PRIMARY KEY,        -- UUID
    status      text,
    actor       text,
    desde       text,
    hasta       text,
    apply       boolean,
    started_at  timestamptz,
    updated_at  timestamptz,
    finished_at timestamptz,
    data        jsonb                    -- {cuentas, workers, stats, ejemplos, errores, ...}
);

-- Manager.PortfolioSnapshotLog → audit del refresh de tenencia (engines/portfolio_snapshot.py,
-- 1 doc cada 60min). Audit-only, sin readers.
CREATE TABLE IF NOT EXISTS manager.portfolio_snapshot_log (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          timestamptz,
    n_actuales  integer,
    n_nuevos    integer,
    n_sin_match integer,
    data        jsonb                    -- {nuevos, sin_match}
);

-- ── TELEMETRÍA DE USO (usuario × módulo, agregado por HORA) ──────────────────
-- Contador agregado, NO log por request (no crece sin control: ~usuarios ×
-- módulos × horas activas). Lo escribe el flush del middleware (api/telemetria.py,
-- upsert incremental cada ~60s, best-effort); lo lee GET /api/manager/uso.
-- Observabilidad de producto: qué usuario pasa tiempo en qué módulo.
CREATE TABLE IF NOT EXISTS manager.uso_modulos (
    email   text NOT NULL,
    modulo  text NOT NULL,
    hora    timestamptz NOT NULL,        -- truncado a la hora
    hits    integer NOT NULL DEFAULT 0,
    PRIMARY KEY (email, modulo, hora)
);
CREATE INDEX IF NOT EXISTS ix_uso_modulos_hora ON manager.uso_modulos (hora DESC);

-- ── ASISTENTE DE NEGOCIO (QuantAI P7, docs/QUANTAI.md) ────────────────────────
-- Mapping ficha↔identidad de la ADUANA (core/pii_gateway.py). Es la tabla de
-- traducción CLIENTE_1 → nombre real de cada chat: VIVE EN EL PERÍMETRO y
-- JAMÁS viaja al proveedor LLM. TTL corto (48h) — la limpia oportunista
-- guardar_mapping() en cada escritura.
CREATE TABLE IF NOT EXISTS manager.asistente_mappings (
    chat_id     text PRIMARY KEY,
    email       text NOT NULL,             -- dueño del chat (nadie continúa el de otro)
    mapping     jsonb NOT NULL,            -- {fichas, valores, contadores}
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_asistente_mappings_updated
    ON manager.asistente_mappings (updated_at);

-- ── FAIR VALUE (curva cuadrática TEA=β0+β1·d+β2·d² + residuos + z-scores) ─────
-- Decomiso 2026-06-28: jobs/fair_value.py pasa de Mongo a SQL-NATIVE (write_native,
-- incondicional). Antes escribía Trading.{FitParams,FairValueResiduos} (Mongo) y leía
-- su propia historia (ventana 30d para z_temporal) de Mongo; ahora todo vive acá.
-- api/services/fair_value.py lee estas 2 tablas (live: β del último cierre + TEAs vivas
-- de market_snapshot; cierre: residuos persistidos; histórico: serie del bono). NO se
-- espejan por sync_mercado_hist (las entries FitParams/FairValueResiduos se retiran).

-- Trading.FitParams → β de la cuadrática por (curva, cierre). 1 fila por curva/día.
-- ts_cierre date (Mongo: string ISO 'YYYY-MM-DD'). Todos los campos los lee el service.
CREATE TABLE IF NOT EXISTS mercado.fit_params (
    ts_cierre                   date NOT NULL,   -- Mongo: ts_cierre string 'YYYY-MM-DD'
    curva                       text NOT NULL,
    beta0                       numeric,
    beta1                       numeric,
    beta2                       numeric,
    r2                          numeric,
    n_bonos_universo            integer,
    vol_min_aplicado            numeric,
    sigma_dia_bps               numeric,
    media_residuos_universo_bps numeric,
    updated_at                  timestamptz,
    data                        jsonb,           -- forward-compat (campos nuevos del fit)
    PRIMARY KEY (curva, ts_cierre)
);

-- Trading.FairValueResiduos → residuo + z-scores por (curva, ticker, cierre). El writer
-- RELEE esta tabla (ventana 30d, ts_cierre DESC) para el z_temporal del día; el service
-- la lee para el cierre persistido + la serie de drill-down del bono.
CREATE TABLE IF NOT EXISTS mercado.fair_value_residuos (
    ts_cierre    date NOT NULL,                  -- Mongo: ts_cierre string 'YYYY-MM-DD'
    curva        text NOT NULL,
    ticker       text NOT NULL,
    ticker_corto text,
    duration     numeric,
    tea_obs      numeric,
    tea_teorica  numeric,
    residuo_bps  numeric,
    z_estatico   numeric,
    z_temporal   numeric,
    n_obs        integer,
    en_universo  boolean,
    data         jsonb,                           -- forward-compat
    PRIMARY KEY (curva, ticker, ts_cierre)
);
-- PK (curva, ticker, ts_cierre) ya sirve el lookup del z_temporal (curva+ticker+rango).
-- Índice extra para la serie de drill-down por ticker SIN curva (get_fair_value_historico_bono).
CREATE INDEX IF NOT EXISTS ix_fvr_ticker_ts
    ON mercado.fair_value_residuos (ticker, ts_cierre);

-- Trading.OnsIgnoradas → tickers marcados "no es ON" en el conciliador de cobertura
-- (api/services/ons.py). Metadata pura (cero plata): excluye un ticker del gap HD/DL.
-- Writer: ignorar_concil (upsert por ticker). Doc Mongo: {ticker, ignorado_por, at}.
CREATE TABLE IF NOT EXISTS mercado.ons_ignoradas (
    ticker       text PRIMARY KEY,
    ignorado_por text,
    at           timestamptz                 -- Mongo: at (UTC aware)
);

-- ── MCP — OAuth 2.1 provider del MCP server (api/mcp/oauth.py) ────────────────
-- Decomiso 2026-06-29: MCP.{OAuthClients,OAuthCodes,OAuthTokens} (Mongo) → schema `mcp`.
-- Era el ÚNICO dominio con 0% de camino a SQL → el último bloqueante para apagar Atlas.
-- Postgres NO tiene TTL index: el vencimiento se filtra por `expires_at` en la lectura
-- y un prune oportunista (en api/mcp/oauth.py::_ensure_sql, sobre register/authorize)
-- borra lo vencido (codes 10min, tokens 1h). Las tablas se referencian SIEMPRE
-- calificadas (`mcp.*`) en el código → `mcp` NO entra al search_path de core/postgres.
-- Lectura/escritura por flag MCP_SQL=1 (default Mongo → rollback = sacar el flag).

-- MCP.OAuthClients → registros DCR (RFC 7591). Persistente (no expira).
CREATE TABLE IF NOT EXISTS mcp.oauth_clients (
    client_id     text PRIMARY KEY,
    redirect_uris text[] NOT NULL DEFAULT '{}',
    client_name   text,
    created_at    timestamptz DEFAULT now()
);

-- MCP.OAuthCodes → authorization codes (single-use, TTL 10min). El consume es
-- DELETE ... RETURNING (atómico, equivale al find_one_and_delete de Mongo).
CREATE TABLE IF NOT EXISTS mcp.oauth_codes (
    code                  text PRIMARY KEY,
    client_id             text,
    redirect_uri          text,
    scope                 text,
    subject               text,
    code_challenge        text,
    code_challenge_method text,
    created_at            timestamptz DEFAULT now(),
    expires_at            timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mcp_codes_expires ON mcp.oauth_codes (expires_at);

-- MCP.OAuthTokens → access tokens vivos (TTL 1h). is_token_revoked = NO existe fila
-- viva (borrar la fila = revocar el JWT). Misma semántica que el TTL de Mongo.
CREATE TABLE IF NOT EXISTS mcp.oauth_tokens (
    jti        text PRIMARY KEY,
    subject    text,
    client_id  text,
    scope      text,
    created_at timestamptz DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mcp_tokens_expires ON mcp.oauth_tokens (expires_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- RESEARCH — capa ANÁLISIS: fundamentals de empresas desde Refinitiv/LSEG (Eikon).
-- Solo datos REALES (nada de estimados/consenso/futuro). Se ingesta MANUAL desde la
-- PC con Workspace abierto (scripts/refinitiv_fundamentals.py). Ver docs/RESEARCH_REFINITIV.md.
--
-- Tres tablas, cada una con la FORMA que le corresponde a su dato:
--   companies       → catálogo (1 fila/RIC): el universo que seguimos.
--   fundamentals    → LARGA (EAV): estados + ratios + segmentos por período. Cientos
--                     de líneas que varían por empresa → agregar concepto = insertar fila.
--   market_snapshot → ANCHA (1 fila/RIC, se pisa): foto de mercado ACTUAL (pocos campos
--                     fijos que cambian todo el tiempo → no tiene sentido "larga").
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS research.companies (
    ric           text PRIMARY KEY,            -- 'RKLB.O' (identidad Refinitiv, clave)
    ticker        text,                          -- 'RKLB'
    nombre        text,
    sector        text,
    pais          text,
    bolsa         text,
    moneda        text,                          -- moneda de reporte de los estados
    cedear_ticker text,                          -- link opcional a mercado.cedears.ticker_corto
    activo        boolean DEFAULT true,
    updated_at    timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS research.fundamentals (
    ric        text NOT NULL,                    -- join a research.companies.ric
    statement  text NOT NULL,                    -- income | balance | cashflow | ratios | segment
    freq       text NOT NULL,                    -- 'Q' | 'FY'
    period_end date NOT NULL,                     -- fin de período (2026-03-31)
    fiscal_period text,                           -- '2026 Q1' | '2025'
    item       text NOT NULL,                     -- concepto ('Revenue', 'EBITDA', ...)
    segment    text NOT NULL DEFAULT '',          -- segmento (solo statement='segment'); '' en el resto
    value      numeric,
    currency   text,
    source     text DEFAULT 'refinitiv',
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (ric, statement, freq, period_end, item, segment)
);
CREATE INDEX IF NOT EXISTS ix_fundamentals_ric ON research.fundamentals (ric, statement, freq, period_end);

CREATE TABLE IF NOT EXISTS research.market_snapshot (
    ric         text PRIMARY KEY,
    price       numeric,
    high_52w    numeric,
    low_52w     numeric,
    market_cap  numeric,
    ev          numeric,
    shares      numeric,
    div_yield   numeric,
    currency    text,
    updated_at  timestamptz DEFAULT now()
);

-- ── Market Data 1816 (vista RESEARCH — laboratorio de series y spreads) ───────
-- API de 1816 (docs/VISTA_RESEARCH.md). Feed SEPARADO de mercado.curvas (que es
-- nuestro motor RF de HOY): acá vive la HISTORIA consistente de 1816 para comparar
-- activos y spreads en el tiempo. Se puebla por job (throttled, EOD + snapshot).

-- Series históricas EOD (tidy: 1 fila por activo × fecha × campo). Escritura por
-- jobs/mercado_1816_series.py (backfill 1 año + append diario, idempotente por PK).
CREATE TABLE IF NOT EXISTS research.mkt_1816_series (
    ticker         text NOT NULL,
    fecha          date NOT NULL,
    campo          text NOT NULL,            -- tea/tna/paridad/precioClean/duration/spread/…
    valor          double precision,
    fuente         text NOT NULL DEFAULT 'byma',
    moneda         text NOT NULL DEFAULT 'ars',
    plazo          smallint NOT NULL DEFAULT 1,
    convencion_tna text,
    ingestado_en   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, fecha, campo, fuente, moneda, plazo)
);
CREATE INDEX IF NOT EXISTS ix_mkt1816_series_tk_campo
    ON research.mkt_1816_series (ticker, campo, fecha);

-- Universo curado de la vista (qué activos se bajan). Editable; si está vacío el
-- job usa un seed por defecto (soberanos + HD).
CREATE TABLE IF NOT EXISTS research.mkt_1816_watch (
    ticker      text PRIMARY KEY,
    curva       text,
    curva_id    int,
    activo      boolean NOT NULL DEFAULT true,
    orden       int,
    agregado_en timestamptz NOT NULL DEFAULT now()
);

-- Catálogo de instrumentos (denominación, ISIN, vencimientos) de /instrumentos.
CREATE TABLE IF NOT EXISTS research.mkt_1816_instrumentos (
    ticker            text PRIMARY KEY,
    denominacion      text,
    curva             text,
    curva_id          int,
    isin              text,
    fecha_emision     date,
    fecha_vencimiento date,
    moneda_denom      text,
    moneda_pago       text,
    emisor            text,
    activo            boolean,
    actualizado_en    timestamptz NOT NULL DEFAULT now()
);

-- ── BCRA (tab BCRA de la vista RESEARCH — docs/RESEARCH_BCRA.md) ─────────────
-- Feed SEPARADO de macro.series_macro (que alimenta motores vía jobs/bcra.py y
-- NO se toca). Solo Monetarias v4; cambiarias descartadas (user 2026-07-18).

-- Espejo del catálogo (~1.581 filas de metadata — chico; para explorar/curar).
CREATE TABLE IF NOT EXISTS research.bcra_variables (
    id_variable     int PRIMARY KEY,
    descripcion     text,
    categoria       text,
    periodicidad    text,
    unidad          text,
    moneda          text,
    primer_fecha    date,
    ultima_fecha    date,
    ultimo_valor    double precision,
    actualizado_en  timestamptz NOT NULL DEFAULT now()
);

-- Universo CURADO (jamás las 1.581): qué series se sincronizan y en qué BLOQUE
-- (sub-tab) de la vista viven. Editable; seed en jobs/bcra_research.py.
CREATE TABLE IF NOT EXISTS research.bcra_watch (
    id_variable  int PRIMARY KEY,
    bloque       text NOT NULL,      -- reservas / tipo_cambio / tasas / dinero / inflacion / indexacion / depositos
    etiqueta     text NOT NULL,      -- label corto para la UI ("Reservas", "BADLAR"…)
    unidad       text,               -- "M USD" / "ARS" / "%" / "M ARS" / "índice"
    orden        int,
    activo       boolean NOT NULL DEFAULT true
);

-- Los puntos de las series del watch (tidy, idempotente por PK).
CREATE TABLE IF NOT EXISTS research.bcra_series (
    id_variable   int NOT NULL,
    fecha         date NOT NULL,
    valor         double precision,
    ingestado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id_variable, fecha)
);
CREATE INDEX IF NOT EXISTS ix_bcra_series_id_fecha
    ON research.bcra_series (id_variable, fecha DESC);

-- ── FRED (tab "Datos Internacionales" — docs/RESEARCH_FRED.md) ────────────────
-- Feed SEPARADO de jobs/bcra.py y jobs/argentina_datos.py. Molde = las bcra_*
-- de arriba, con 2 diferencias no inferibles: (1) series_id es TEXTO (ej DGS10),
-- no int; (2) frecuencias MIXTAS D/W/M/Q → freq vive en el watch (clave para el
-- colchón del watermark y para saber si graficar continuo o escalonado).
-- El catálogo FRED es ~800k series → NO se espeja entero: fred_series guarda
-- metadata SOLO de las series del watch (poblada vía /fred/series por id).

CREATE TABLE IF NOT EXISTS research.fred_series (
    series_id         text PRIMARY KEY,
    title             text,
    frequency         text,        -- Daily / Weekly / Monthly / Quarterly
    units             text,        -- units de origen ("Percent", "Index 2017=100"…)
    seasonal_adj      text,        -- SA / NSA
    observation_start date,
    observation_end   date,
    last_updated      timestamptz, -- de FRED (para detectar cambios / calendario)
    notes             text,
    actualizado_en    timestamptz NOT NULL DEFAULT now()
);

-- Universo CURADO (jamás las 800k): qué series se sincronizan y en qué BLOQUE
-- (sub-tab) viven. Editable; seed en jobs/fred_research.py.
CREATE TABLE IF NOT EXISTS research.fred_watch (
    series_id text PRIMARY KEY,
    bloque    text NOT NULL,      -- tasas_usa / commodities / eeuu_macro / china …
    etiqueta  text NOT NULL,      -- label corto para la UI ("UST 10Y", "Soja")
    unidad    text,               -- unidad DISPLAY nuestra ("%", "USD/t", "índice")
    freq      text,               -- D / W / M / Q (clave por frecuencias mixtas)
    pais      text,               -- EEUU / Global / China / …
    orden     int,
    activo    boolean NOT NULL DEFAULT true
);

-- Los puntos del watch (tidy, idempotente por PK). valor NULL permitido (aunque
-- el cliente descarta los "." de FRED antes de insertar).
CREATE TABLE IF NOT EXISTS research.fred_observations (
    series_id    text NOT NULL,
    fecha        date NOT NULL,
    valor        double precision,
    ingestado_en timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, fecha)
);
CREATE INDEX IF NOT EXISTS ix_fred_obs_id_fecha
    ON research.fred_observations (series_id, fecha DESC);

-- ── Documentos MANUALES de la vista REPORTES FINANCIEROS ─────────────────────
-- Contexto que NO llega por mail: PDFs (ej. el "Semanal") y comentarios sueltos,
-- cargados a mano desde Manager. El PDF se guarda como bytea (decisión: cero infra;
-- si crece se migra a un bucket sin que cambie la UX). Lo escribe el Manager
-- (api/routers/manager/documentos.py, gate manager), lo lee la vista Research
-- (api/routers/research_docs.py, gate research). Doc: docs/RESEARCH_FRED.md.
CREATE TABLE IF NOT EXISTS research.documentos (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    titulo         text NOT NULL,
    fecha          date NOT NULL,          -- fecha del CONTENIDO (no la de carga)
    tipo           text NOT NULL,          -- 'pdf' | 'comentario'
    fuente         text,                   -- etiqueta libre ("Semanal", "Nota mesa"…)
    comentario     text,                   -- cuerpo (comentario) o nota del PDF
    archivo        bytea,                  -- el PDF (tipo=pdf); NULL si comentario
    mime           text,                   -- 'application/pdf'
    nombre_archivo text,
    autor          text,                   -- email de quien lo cargó
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_research_docs_fecha ON research.documentos (fecha DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- PERFORMANCE — autovacuum agresivo + fillfactor en tablas de ALTA ROTACIÓN
-- (perf 2026-06-29). Los motores upsertean estas tablas cada ~1s; con el
-- autovacuum default (20%) se bloatean (market_snapshot llegó a 434MB para 310
-- filas → seq scan 38ms). fillfactor 80 deja lugar para UPDATE HOT (sin bloat de
-- índice) y el autovacuum al 2% las mantiene chicas. Idempotente. El mismo seteo
-- lo aplica `scripts/sql_vacuum_tune.py --tune` (+ --vacuum-full para compactar).
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE mercado.market_snapshot        SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.cedears_snapshot       SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.adr_snapshot           SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.agro_snapshot          SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.agro_opciones_snapshot SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.futuros_dlr_snapshot   SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.caucion_snapshot       SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.options_snapshot       SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE mercado.snapshots_sinteticos   SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE home.market_quotes             SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);
ALTER TABLE valuaciones.portfolio_snapshot SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_vacuum_threshold=50, autovacuum_analyze_scale_factor=0.02, fillfactor=80);

-- ─────────────────────────────────────────────────────────────────────────────
-- IA — observabilidad del gateway core/ai.py (QuantAI Fase 0, docs/QUANTAI.md)
-- Cada llamada a un LLM deja una fila acá (el "job_runs" de la IA). El
-- presupuesto diario del gateway (global y por usuario) se calcula sumando los
-- tokens de HOY sobre esta tabla. `feedback` guarda el 👍(1)/👎(-1) del usuario
-- en outputs interactivos (NULL = sin feedback). Retención: cleanup de Postgres
-- (no TTL nativo), igual que manager.job_runs.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ia.trazas (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    tarea       text NOT NULL,
    modelo      text NOT NULL,
    usuario     text,
    tokens_in   integer,
    tokens_out  integer,
    latencia_ms integer,
    ok          boolean NOT NULL,
    error       text,
    feedback    smallint,
    detalle     text,   -- extracto del pedido (ej. la pregunta), cap en core/ai
    respuesta   text,   -- extracto de la respuesta del modelo, cap en core/ai
    razonamiento text,  -- extracto del reasoning_content (thinking), cap en core/ai
    conv_id     text    -- conversación del copiloto (cada chat su mundo)
);
-- Telemetría del caché de prefijo del proveedor (2026-07-20): el proveedor
-- cobra ~10x menos los tokens servidos desde caché — estas columnas miden
-- cuánto del prompt pegó en caché (valida el diseño prefijo-estable del
-- copiloto y muestra el ahorro real en OBSERVABILIDAD).
ALTER TABLE ia.trazas ADD COLUMN IF NOT EXISTS cache_hit_tokens  integer;
ALTER TABLE ia.trazas ADD COLUMN IF NOT EXISTS cache_miss_tokens integer;
CREATE INDEX IF NOT EXISTS ix_ia_trazas_ts ON ia.trazas (ts);
CREATE INDEX IF NOT EXISTS ix_ia_trazas_usuario_ts ON ia.trazas (usuario, ts);
-- Columnas agregadas 2026-07-11 (panel OBSERVABILIDAD → IA: detalle por llamada)
ALTER TABLE ia.trazas ADD COLUMN IF NOT EXISTS detalle text;
ALTER TABLE ia.trazas ADD COLUMN IF NOT EXISTS respuesta text;
ALTER TABLE ia.trazas ADD COLUMN IF NOT EXISTS razonamiento text;
-- 2026-07-12: conversaciones separadas del copiloto (cada chat su mundo)
ALTER TABLE ia.trazas ADD COLUMN IF NOT EXISTS conv_id text;

-- Extremos históricos PRE-serie (2005 → arranque de la serie diaria) por
-- underlying. Decisión user 2026-07-11: NO cargar 20 años de velas — el
-- script scripts/backfill_extremos_hist.py releva el máximo y el mínimo de
-- Yahoo y guarda SOLO los lados que SUPERAN a los de la serie viva (si el
-- extremo ya está en 2024+, no se guarda nada). Lector:
-- api/services/copiloto._extremos_serie (merge con la serie).
CREATE TABLE IF NOT EXISTS mercado.precios_extremos_hist (
    ticker      text PRIMARY KEY,
    max_high    numeric,
    max_fecha   date,
    min_low     numeric,
    min_fecha   date,
    desde       date NOT NULL,
    hasta       date NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Config editable del gateway de IA (presupuestos de tokens). Se edita desde
-- Manager → OBSERVABILIDAD → IA (solo admin). Precedencia en core/ai.py:
-- esta tabla > env var > default del código. Claves: budget_dia_global,
-- budget_dia_usuario. El GLOBAL es techo duro del día: aunque la suma de los
-- topes por usuario lo supere en papel, el gasto total no puede pasarlo
-- (cada llamada chequea los dos).
CREATE TABLE IF NOT EXISTS ia.config (
    clave       text PRIMARY KEY,
    valor       bigint NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text
);

-- Triage de incidentes (QuantAI P2, docs/QUANTAI.md) — REACTIVO. jobs/triage.py
-- lee las fallas nuevas de manager.job_runs, las agrupa por FIRMA (dedup) y SOLO
-- una firma NUEVA gasta un diagnóstico del LLM (guarda de costo). La memoria
-- experiencial ("¿esto ya pasó?") vive acá: una firma conocida NO se re-diagnostica,
-- solo suma ocurrencias — con el tiempo, más determinista y menos tokens.
CREATE TABLE IF NOT EXISTS ia.triage_incidentes (
    firma          text PRIMARY KEY,          -- 'tipo::firma-normalizada' del error
    tipo           text NOT NULL,             -- job que falla
    primera_vez    timestamptz NOT NULL DEFAULT now(),
    ultima_vez     timestamptz NOT NULL DEFAULT now(),
    ocurrencias    integer NOT NULL DEFAULT 1,
    muestra_error  text,                      -- muestra del error crudo (PII scrubeada)
    diagnostico    jsonb,                     -- {causa, hecho, hipotesis, recomendacion, confianza}
    modelo         text,                      -- modelo que diagnosticó (NULL = sin diagnosticar)
    estado         text NOT NULL DEFAULT 'nuevo',   -- nuevo | diagnosticado | playbook | resuelto
    notificado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_triage_ultima ON ia.triage_incidentes (ultima_vez DESC);

-- Watermark del triage: hasta qué started_at ya procesó (idempotencia + reactividad).
CREATE TABLE IF NOT EXISTS ia.triage_estado (
    id                text PRIMARY KEY,        -- 'watermark'
    ultimo_procesado  timestamptz
);

-- Research diario de mercado (QuantAI — memoria de mercado, docs/QUANTAI.md).
-- jobs/research_mail.py lee la casilla por IMAP, persiste el mail CRUDO (fuente
-- de verdad, siempre citable) + un DESTILADO del LLM (resumen/temas/hechos) que
-- es lo que se inyecta barato como contexto al copiloto/briefing. destilado NULL
-- = pendiente (el LLM falló al ingestar; el próximo run lo reintenta).
CREATE TABLE IF NOT EXISTS ia.research (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha        date NOT NULL,          -- día del research (header Date del mail, ART)
    fuente       text NOT NULL,          -- remitente
    asunto       text,
    message_id   text UNIQUE,            -- dedup (Message-ID del mail): re-correr no duplica
    cuerpo       text NOT NULL,          -- texto completo del mail (fuente de verdad)
    destilado    jsonb,                  -- {resumen, temas[], hechos[]} — generado por LLM
    destilado_modelo text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ia_research_fecha ON ia.research (fecha DESC);
-- Full-text español sobre el cuerpo ("¿qué decía el research sobre X?") —
-- ADOPTAR YA de QUANTAI.md: FTS antes que vectores.
CREATE INDEX IF NOT EXISTS ix_ia_research_fts ON ia.research
    USING gin (to_tsvector('spanish', cuerpo));
