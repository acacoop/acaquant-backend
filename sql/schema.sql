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
    estado_comercial text                    -- ACTIVA / ENFRIANDOSE / DORMIDA / NUEVA (derivado)
);
CREATE INDEX IF NOT EXISTS ix_comitentes_operador ON comitentes(operador_email);
CREATE INDEX IF NOT EXISTS ix_comitentes_nivel1   ON comitentes(nivel_1);

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
    PRIMARY KEY (fecha_snapshot, id_cuenta, unidad)
);
CREATE INDEX IF NOT EXISTS ix_aum_id_cuenta ON aum(id_cuenta, fecha_snapshot);
CREATE INDEX IF NOT EXISTS ix_aum_unidad    ON aum(unidad, fecha_snapshot);

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
    plazo        text,
    lugar        text,
    estado       text,
    informacion  text,
    PRIMARY KEY (fecha, comprobante)
);
-- La tabla ya existe en Supabase → ALTER idempotente agrega las columnas nuevas.
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS cuenta      text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS plazo       text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS lugar       text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS estado      text;
ALTER TABLE negocio_movimientos ADD COLUMN IF NOT EXISTS informacion text;

CREATE INDEX IF NOT EXISTS ix_nm_id_cuenta ON negocio_movimientos(id_cuenta, fecha);
CREATE INDEX IF NOT EXISTS ix_nm_categoria ON negocio_movimientos(categoria, fecha);
CREATE INDEX IF NOT EXISTS ix_nm_cuenta    ON negocio_movimientos(cuenta, fecha);

-- CashFlow.Accionistas — set de cuentas accionistas (para el filtro de cuenta de NEGOCIO/
-- portfolio: accionistas / sin_accionistas / cooperativas). Solo el string `cuenta`.
CREATE TABLE IF NOT EXISTS accionistas (
    cuenta text PRIMARY KEY
);
