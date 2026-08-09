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
--   home        → news_headlines, market_quotes
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
    ('public','market_quotes','home')
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
    -- Nº del DESTINO en el MAE (2026-08-05) — SOLO el número (ej. '062'): la
    -- letra la agrega el sistema según la orden (interno → 'F' fondo). Es un
    -- atributo de la contraparte (como nombre/segmento), editable en Manager →
    -- CONTRAPARTES. El Excel MAE de SENEBIS resuelve DESTINO en vivo: cc de la
    -- orden → id_cuenta → codigo_mae. No es derivable: lo asigna el MAE.
    codigo_mae      text,
    origen          text,                    -- "manual" | "reconciler"
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_contrapartes_segmento ON clientes.contrapartes(segmento);
ALTER TABLE clientes.contrapartes ADD COLUMN IF NOT EXISTS codigo_mae text;

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
    tipo_agro      text,                      -- FUTURO | OPCION | NULL (solo boletos agro clasificados)
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
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS tipo_agro      text;
-- TASA (2026-08-03): el "precio" de los boletos MAV (pagarés / cheques). Esos
-- instrumentos NO tienen precio unitario — se negocian a tasa, y la tasa vive
-- embebida en el texto `negocio_movimientos.informacion` ('...100.000,00@6%...').
-- La rellena `jobs/ops_tasa_mav.py`, encadenado al negocio_chain. En PORCENTAJE
-- tal cual figura en el texto (6 = 6%), NO en tanto por uno.
-- Deliberadamente NO se llama `precio`: mezclar un precio en ARS y una tasa en %
-- en una sola columna numérica invita a que alguien las sume o promedie junto.
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS tasa           numeric;
-- ANULADOS (2026-08-04): mismo problema que en negocio_movimientos — jobs/
-- operaciones_informes upsertea por boleto y nunca borraba. Medido: 450 boletos
-- anulados fosilizados, $824 MM de volumen falso YTD (3,69%). La reconciliación
-- corre POR CUENTA y sólo sobre las que Aunesa respondió OK (una cuenta con
-- timeout no se toca nunca).
ALTER TABLE operaciones.operaciones ADD COLUMN IF NOT EXISTS anulado_en     timestamptz;
CREATE INDEX IF NOT EXISTS ix_ops_anulado ON operaciones.operaciones(concertacion)
    WHERE anulado_en IS NOT NULL;
-- La reconciliación busca por (cuenta, día) y `ix_ops_id_cuenta` es sólo por cuenta →
-- traía toda la historia de la cuenta para después filtrar por fecha.
CREATE INDEX IF NOT EXISTS ix_ops_cuenta_concert
    ON operaciones.operaciones(id_cuenta, concertacion);

CREATE INDEX IF NOT EXISTS ix_ops_concertacion ON operaciones.operaciones(concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_id_cuenta    ON operaciones.operaciones(id_cuenta);
CREATE INDEX IF NOT EXISTS ix_ops_moneda_cierre ON operaciones.operaciones(moneda, es_cierre);
CREATE INDEX IF NOT EXISTS ix_ops_moneda_concert ON operaciones.operaciones(moneda, concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_segmento_concert ON operaciones.operaciones(segmento, concertacion);
CREATE INDEX IF NOT EXISTS ix_ops_ingestado ON operaciones.operaciones(ingestado_en);

-- HOT/COLD (decisión user 2026-08-04): los días CERRADOS de operaciones se
-- pre-agregan acá y las series los leen de una pasada; HOY se agrega en vivo.
-- NO es el viejo rollup (que recalculaba a ciegas y drifteaba): jobs/ops_agregado
-- recomputa POR DÍA SUCIO — cualquier día cuyo ingestado_en sea posterior al
-- último cálculo (backfills incluidos) se re-agrega entero. Idempotente.
-- moneda_calc = el param `moneda` de las vistas: ARS / USD nativos, USD_DOL
-- dolarizado por mep del boleto. `bruto` excluye cierres (es_cierre=false);
-- `arancel` los INCLUYE cuando traen arancel (misma regla que _ops_where).
CREATE TABLE IF NOT EXISTS operaciones.ops_agregado_diario (
    fecha          date NOT NULL,
    moneda_calc    text NOT NULL,             -- 'ARS' | 'USD' | 'USD_DOL'
    bruto          numeric NOT NULL DEFAULT 0,
    arancel        numeric NOT NULL DEFAULT 0,
    n_boletos      integer NOT NULL DEFAULT 0,
    actualizado_en timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, moneda_calc)
);
-- MERCADO (2026-08-03): `_ops_where` filtra por `mercado = X` en toda la vista
-- MOVIMIENTOS y no había índice — cada filtro por mercado era un scan. Lo usa
-- además `jobs/ops_tasa_mav.py` para encontrar los boletos MAV pendientes de tasa.
CREATE INDEX IF NOT EXISTS ix_ops_mercado_concert ON operaciones.operaciones(mercado, concertacion);
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
-- DÓLAR FUTURO (perf 2026-07-28, medido con EXPLAIN): la vista /ops/dolar-futuro corre
-- 5 agregaciones con `instrumento ILIKE '%DLR%'` — comodín adelante = no indexable →
-- leía 92k filas YTD para quedarse con 1k (1,1%). Parcial: solo filas DLR. Las queries
-- de ops_dolar_futuro llevan el literal inline para que el planner matchee el predicado.
CREATE INDEX IF NOT EXISTS ix_ops_dlr_concert ON operaciones.operaciones(concertacion)
    WHERE instrumento ILIKE '%DLR%';

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
-- ARANCELES (perf 2026-07-29, medido con pg_stat_statements + hypopg): el UPDATE de
-- aranceles (api/services/aunesa_aranceles.py) filtra SOLO por `comprobante`, pero la
-- PK es (fecha, comprobante) con `fecha` primero → NO usable → seq scan de 123MB en
-- cada una de las ~361k llamadas = 2,3 hs acumuladas (#2 consumidor de toda la DB).
-- Índice dedicado por comprobante → lookup directo (seq scan → index scan).
CREATE INDEX IF NOT EXISTS ix_nm_comprobante ON operaciones.negocio_movimientos(comprobante);
-- DIFERENCIAS DIARIAS (perf 2026-07-28, medido con EXPLAIN): el planner elegía la PKEY
-- (fecha, comprobante) para el rango → 89k buffers y 5 queries de ~290ms por request.
-- Parcial que calza EXACTO con la vista (moneda + fecha, solo filas de diferencias).
-- ops_diferencias_diarias/_fechas llevan el ILIKE literal inline para matchear.
CREATE INDEX IF NOT EXISTS ix_nm_dif ON operaciones.negocio_movimientos(moneda, fecha)
    WHERE categoria = 'otro' AND informacion ILIKE 'Diferencias diarias%';
-- MATERIALIZACIÓN (2026-07-28): el instrumento/producto de una diferencia diaria vivía
-- atrapado en el texto `informacion` ("Diferencias diarias - [SOJ.ROS/MAY26] - ...") →
-- cada query lo re-extraía con regex sobre ~45k filas. Columnas GENERADAS: Postgres las
-- calcula en cada INSERT/UPDATE y en el ALTER inicial (backfill automático) — una sola
-- fuente de verdad, sin cambios en el job de ingesta. NULL para filas no-diferencia.
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS dif_instrumento text
    GENERATED ALWAYS AS (CASE WHEN informacion LIKE 'Diferencias diarias%'
        THEN substring(informacion from '\[([^\]]+)\]') END) STORED;
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS dif_producto text
    GENERATED ALWAYS AS (CASE WHEN informacion LIKE 'Diferencias diarias%'
        THEN substring(informacion from '\[([A-Za-z]+)') END) STORED;
-- ANULADOS (2026-08-04): Aunesa a veces carga un boleto mal, lo ANULA y emite uno
-- corregido. La ingesta hace upsert y NUNCA borraba → el anulado quedaba pegado para
-- siempre inflando volumen (medido: 855 fantasmas YTD, $591 MM falsos). Ahora
-- jobs/negocio_movimientos reconcilia cada fecha: lo que Aunesa deja de devolver se
-- MARCA (no se borra — trazabilidad + el fantasma a veces es la versión vieja de algo
-- real). Si el boleto reaparece, el upsert lo revive (anulado_en=NULL).
ALTER TABLE operaciones.negocio_movimientos ADD COLUMN IF NOT EXISTS anulado_en timestamptz;
CREATE INDEX IF NOT EXISTS ix_nm_anulado ON operaciones.negocio_movimientos(fecha)
    WHERE anulado_en IS NOT NULL;

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

-- ── MESA DE DINERO (vista NEGOCIO → /mesa-dinero) — carga MANUAL de la mesa.
-- Cada registro = pata compra + pata venta del mismo activo (monto = vn×px/100;
-- resultado = monto_venta − monto_compra; pct = resultado/monto_compra). Los
-- registros sin patas (ej. "Pase OPS") cargan `resultado` directo. El resultado
-- DIARIO no se persiste: se deriva SUM(resultado) por fecha; el TC del día es
-- carga manual (mesa_dinero_tc) y convierte a USD. Escritura: allowlist
-- per-usuario (mesa_dinero_escritores) + admin; catálogo de traders y allowlist
-- se gestionan en Manager → MESA. Todo cambio queda en mesa_dinero_audit.
CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fecha           date NOT NULL,
    trader          text NOT NULL,          -- de mesa_dinero_traders (validado en el service)
    activo          text,                    -- ticker/etiqueta libre (TZXD6, Dolar mep, Pase OPS…)
    vn_compra       numeric,
    px_compra       numeric,
    monto_compra    numeric,                 -- derivado: vn_compra × px_compra / 100
    vn_venta        numeric,
    px_venta        numeric,
    monto_venta     numeric,                 -- derivado: vn_venta × px_venta / 100
    resultado       numeric NOT NULL,        -- monto_venta − monto_compra (o manual sin patas)
    pct             numeric,                 -- resultado / monto_compra
    cliente         text,
    observacion     text,                    -- "Mesa" u operador comercial (validado)
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_mesa_dinero_fecha ON operaciones.mesa_dinero (fecha);

CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero_tc (
    fecha           date PRIMARY KEY,
    tc              numeric NOT NULL,        -- carga manual (decisión 2026-07-29: NO auto-MEP)
    actualizado_por text,
    actualizado_at  timestamptz
);

CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero_traders (
    nombre     text PRIMARY KEY,
    creado_por text,
    creado_at  timestamptz
);

CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero_escritores (
    email        text PRIMARY KEY,           -- usuario de la app con permiso de ESCRITURA
    agregado_por text,
    agregado_at  timestamptz
);

CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero_audit (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     timestamptz,
    actor  text,                             -- email de quién hizo el cambio
    action text,                             -- create_op/update_op/delete_op/set_tc/add_trader/…
    target text,                             -- id de la op / fecha / nombre / email
    data   jsonb                             -- before/after
);
CREATE INDEX IF NOT EXISTS ix_mesa_dinero_audit_ts ON operaciones.mesa_dinero_audit (ts DESC);

-- ACA VALORES RETORNO TOTAL — operaciones bursátiles del fondo "ACA R.TOTAL".
-- Se cargan EXCLUSIVAMENTE desde el Excel "OP Aca Valores FCI - <mes>.xls"
-- (informe de operaciones, NO diario) vía scripts/import_acavalores_retorno.py.
-- Cada fila = una operación del archivo. La carga es idempotente por `periodo`
-- (YYYY-MM del archivo): re-importar un mes borra y reinserta ese mes. Alimenta
-- la tab "ACA VALORES RETORNO TOTAL" de Mesa de Dinero (Σ Valor Nominal por
-- operación / agente / papel). Lectura: módulo `operaciones` (solo lectura).
CREATE TABLE IF NOT EXISTS operaciones.acavalores_retorno (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    periodo            text NOT NULL,        -- 'YYYY-MM' (de fecha_concertacion) — clave de re-import
    fondo              text,                 -- Fondo Nombre (ej. "ACA R.TOTAL")
    operacion          text,                 -- Operación Descripción (Compra, Venta, Caución…)
    fecha_concertacion date,
    plazo              int,
    fecha_liquidacion  date,
    papel_numero       text,
    papel_descripcion  text,                 -- Papel Descripción (FCI…, Especies Varias, GOB…)
    depositario        text,
    valor_nominal      numeric,              -- Valor Nominal (la métrica que se suma)
    moneda_simbolo     text,                 -- Moneda de Concertación Símbolo ($)
    precio             numeric,              -- Moneda de Concertación Precio
    bruto              numeric,              -- Moneda de Concertación Bruto
    gastos_total       numeric,              -- Gastos Total
    isin               text,                 -- Papel ISIN Code
    agente_descripcion text,                 -- Agente Descripción (broker/ALyC)
    liq_total          numeric,              -- Moneda de Liquidación Total
    papel_codigo       text,                 -- Papel Código
    liq_neto           numeric,              -- Moneda de Liquidación Neto
    liq_precio         numeric,              -- Moneda de Liquidación Precio
    fondo_neto         numeric,              -- Moneda del Fondo Neto
    tipo_especie       text,                 -- Tipo de Especie Descripción
    archivo            text,                 -- nombre del .xls de origen (trazabilidad)
    importado_en       timestamptz
);
CREATE INDEX IF NOT EXISTS ix_acaret_periodo ON operaciones.acavalores_retorno (periodo);
CREATE INDEX IF NOT EXISTS ix_acaret_operacion ON operaciones.acavalores_retorno (operacion);
CREATE INDEX IF NOT EXISTS ix_acaret_agente ON operaciones.acavalores_retorno (agente_descripcion);
CREATE INDEX IF NOT EXISTS ix_acaret_papel ON operaciones.acavalores_retorno (papel_descripcion);

-- ── SENEBIS (vista BACK OFFICE → SENEBIS) — órdenes de traders.
-- Flujo: el TRADER carga la orden → aparece 'pendiente' en la vista → el BACK
-- OFFICE la procesa en el sistema externo y la marca 'completada'. Dos estados,
-- nada más. Derivados/defaults server-side: monto = vn×px/100 (px cada 100 VN),
-- concertacion = HOY, cp = '255', plazo (CI/24) ↔ liquidacion se infieren entre
-- sí con día hábil. El export .xlsx (ID · OPERACION · INSTRUMENTO · PLAZO ·
-- PRECIO · CANTIDAD · CONTRAPARTE · COMITENTE · CARTERA PROPIA · MERCADO) es lo
-- que el back office carga en el sistema destino; el ID es esta secuencia
-- GLOBAL (espeja la numeración Quantex, NO se resetea nunca; mientras conviva
-- el Excel viejo se alinea desde la vista — "PRÓXIMO ID", POST /proximo-id).
-- Lectura/escritura: módulo `back-office` (traders y back office lo tienen).
-- Todo cambio queda en senebis_audit (before/after).
CREATE TABLE IF NOT EXISTS operaciones.senebis (
    id              bigint GENERATED BY DEFAULT AS IDENTITY (START WITH 13630) PRIMARY KEY,
    operacion       text NOT NULL,           -- COMPRA / VENTA
    concertacion    date NOT NULL,           -- default HOY (lo pone el service)
    liquidacion     date,                    -- inferible de plazo (y viceversa)
    plazo           text,                    -- 'CI' | '24'
    especie         text NOT NULL,           -- ticker (T15D5, TZXM7, AL30C…), MAYÚSCULAS
    vn              numeric,                 -- valor nominal (cantidad)
    px              numeric,                 -- precio cada 100 VN
    monto           numeric,                 -- derivado vn×px/100 (override manual permitido)
    cp              text,                    -- cartera propia (default '255')
    cc              text,                    -- cuenta comitente (texto o número)
    contraparte     text,                    -- texto libre (suele venir vacío)
    nro_contraparte text,                    -- ídem
    mercado         text,                    -- GARANTIZADO / NO GARANTIZADO / vacío
    -- SI/NO: la orden la carga la CONTRAPARTE en Quantex → no sale en el
    -- Excel/espejo (igual que MAE), pero sí en la vista y sigue su flujo
    -- pendiente → completada normal.
    cargan_ellos    boolean NOT NULL DEFAULT false,
    tipo            text,                    -- observación (texto libre, ej. 'pasada')
    -- Contraparte del senebi: 'interno' (cliente de la ALyC → cc) o 'externo'
    -- (agente/ALyC de afuera → agente del catálogo senebis_agentes). Manda en
    -- el Excel: externo → COMITENTE vacío + CONTRAPARTE = número del agente;
    -- interno GARANTIZADO → COMITENTE = cp; interno NO GARANTIZADO → COMITENTE = cc.
    tipo_contraparte text NOT NULL DEFAULT 'interno',   -- 'interno' | 'externo'
    agente          text,                    -- nombre del agente (externo, validado vs catálogo)
    agente_numero   text,                    -- snapshot del número al guardar (va al Excel)
    cc_denominacion text,                    -- snapshot denominación clientes.cuentas (interno)
    -- Orden MAE: se carga en el MAE (otro sistema) → NO sale en el Excel
    -- Quantex ni en su espejo; tipo se fija 'MAE' automático al guardar.
    es_mae          boolean NOT NULL DEFAULT false,
    -- Marcas de edición (persistentes, = el amarillo que el back office pintaba
    -- a mano en la planilla vieja). campos_editados acumula qué campos se
    -- tocaron post-alta (el front les pone *); editada_completada se prende si
    -- se editó algo que YA estaba completada → el back office la cargó en
    -- Quantex y tiene que revisarla (fila amarilla).
    campos_editados text[] NOT NULL DEFAULT '{}',
    editada_completada boolean NOT NULL DEFAULT false,
    estado          text NOT NULL DEFAULT 'pendiente',  -- 'pendiente' | 'completada'
    completada_por  text,                    -- email del back office que la completó
    completada_at   timestamptz,
    creado_por      text,                    -- email del trader que la cargó
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_senebis_concertacion ON operaciones.senebis (concertacion DESC);
CREATE INDEX IF NOT EXISTS ix_senebis_estado ON operaciones.senebis (estado);
-- La tabla ya corre en prod desde 2026-08-05: las columnas de contraparte se
-- agregan también por ALTER (idempotente) para los deploys que ya la tienen.
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS tipo_contraparte text NOT NULL DEFAULT 'interno';
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS agente text;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS agente_numero text;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS cc_denominacion text;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS es_mae boolean NOT NULL DEFAULT false;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS campos_editados text[] NOT NULL DEFAULT '{}';
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS editada_completada boolean NOT NULL DEFAULT false;
-- Migración 2026-08-05: cargan_ellos era text (observación libre) y pasó a
-- boolean SI/NO (True = la carga la contraparte → fuera del Excel/espejo).
-- Guardado en un DO para ser idempotente: solo convierte si todavía es text.
-- Texto no vacío (y distinto de 'no') se interpreta como SI.
DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'operaciones' AND table_name = 'senebis'
          AND column_name = 'cargan_ellos') = 'text' THEN
        ALTER TABLE operaciones.senebis ALTER COLUMN cargan_ellos TYPE boolean
            USING (lower(COALESCE(btrim(cargan_ellos), '')) NOT IN ('', 'no'));
        ALTER TABLE operaciones.senebis ALTER COLUMN cargan_ellos SET DEFAULT false;
        ALTER TABLE operaciones.senebis ALTER COLUMN cargan_ellos SET NOT NULL;
    END IF;
END $$;

-- Catálogo de AGENTES externos (ALyCs de afuera: COCOS, ALLARIA…) con el NÚMERO
-- que espera el sistema destino en CONTRAPARTE. Lo gestiona el back office desde
-- la vista (el nombre puede coincidir con clientes.contrapartes, pero el número
-- del sistema destino solo vive acá). El trader elige por nombre (desplegable).
CREATE TABLE IF NOT EXISTS operaciones.senebis_agentes (
    nombre          text PRIMARY KEY,
    numero          text NOT NULL,
    -- Nº MAE del agente — SOLO el número: la letra 'A' la agrega el sistema
    -- al armar el DESTINO del Excel MAE. Convive con `numero` (BYMA/Quantex).
    codigo_mae      text,
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
ALTER TABLE operaciones.senebis_agentes ADD COLUMN IF NOT EXISTS codigo_mae text;

-- Presencia en la vista SENEBIS: quién la tiene abierta AHORA (heartbeat del
-- front cada vez que pollea la lista; conectado = visto_at en los últimos 90s).
-- Evita que dos personas del back office procesen la misma orden sin saberlo.
CREATE TABLE IF NOT EXISTS operaciones.senebis_presencia (
    email    text PRIMARY KEY,
    visto_at timestamptz NOT NULL
);

-- Allowlist de ESCRITURA de SENEBIS (crear/editar/borrar órdenes), separada de
-- la de Mesa de Dinero: son equipos distintos (traders + back office) aunque se
-- gestionen en la misma pantalla (Manager → MESA). admin siempre puede.
CREATE TABLE IF NOT EXISTS operaciones.senebis_escritores (
    email        text PRIMARY KEY,
    agregado_por text,
    agregado_at  timestamptz
);

-- Semilla de una sola vez: SENEBIS usaba la allowlist de Mesa de Dinero, así
-- que al separarlas hereda exactamente los mismos usuarios y nadie pierde el
-- permiso que ya tenía. Corre solo si la tabla está vacía.
INSERT INTO operaciones.senebis_escritores (email, agregado_por, agregado_at)
SELECT email, 'migracion:mesa_dinero', now() FROM operaciones.mesa_dinero_escritores
WHERE NOT EXISTS (SELECT 1 FROM operaciones.senebis_escritores)
ON CONFLICT (email) DO NOTHING;

CREATE TABLE IF NOT EXISTS operaciones.senebis_audit (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     timestamptz,
    actor  text,                             -- email de quién hizo el cambio
    action text,                             -- create_op/update_op/delete_op/set_estado
    target text,                             -- id de la orden
    data   jsonb                             -- before/after
);
CREATE INDEX IF NOT EXISTS ix_senebis_audit_ts ON operaciones.senebis_audit (ts DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- TESORERÍA (Back Office → Tesorería). Los movimientos del día se sirven LIVE
-- desde Aunesa y no se persisten; lo único que Aunesa NO da es el SALDO INICIAL
-- de cada cuenta operativa (banco), que se carga a mano por día para que la card
-- cierre en saldo final = inicial + ingresos − egresos.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_saldos (
    fecha            date NOT NULL,
    cuenta_operativa text NOT NULL,        -- denominación (ej. 'BANCO MARIVA TERCEROS')
    unidad           text NOT NULL,        -- ARS / USD
    saldo_inicial    numeric NOT NULL,
    actualizado_por  text,
    actualizado_at   timestamptz,
    PRIMARY KEY (fecha, cuenta_operativa, unidad)
);

-- Allowlist de ESCRITURA del saldo inicial (admin siempre puede). Ver la vista lo
-- da el módulo `back-office`; esto decide quién puede cargar los saldos.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_escritores (
    email        text PRIMARY KEY,
    agregado_por text,
    agregado_at  timestamptz
);

CREATE TABLE IF NOT EXISTS operaciones.tesoreria_audit (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     timestamptz,
    actor  text,
    action text,                             -- set_saldo_inicial/add_escritor/remove_escritor
    target text,                             -- fecha|cuenta_operativa|unidad o email
    data   jsonb
);
CREATE INDEX IF NOT EXISTS ix_tesoreria_audit_ts ON operaciones.tesoreria_audit (ts DESC);

-- Presencia en la vista TESORERÍA (mismo patrón que senebis_presencia): el poll
-- de la vista marca el último visto_at; conectado = visto en los últimos 90s.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_presencia (
    email    text PRIMARY KEY,
    visto_at timestamptz NOT NULL
);

-- Catálogo de CUENTAS OPERATIVAS (bancos). Aunesa no expone un endpoint de cuentas:
-- el universo se descubre viendo movimientos. Sin esto la grilla BANCOS mostraría
-- solo los bancos que ya operaron HOY (aparecen a media rueda) en vez del panel
-- completo. Se auto-registra en cada carga de la vista y se puede sembrar hacia
-- atrás con `python -m scripts.diag_tesoreria_cuentas --registrar`.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_cuentas (
    cuenta_operativa text NOT NULL,
    unidad           text NOT NULL,
    aunesa_id        text,
    activa           boolean NOT NULL DEFAULT true,  -- false = ocultar de la grilla
    primera_vez      date,
    ultima_vez       date,
    PRIMARY KEY (cuenta_operativa, unidad)
);

-- FOTO de la grilla BANCOS. La vista del día es casi toda LIVE contra Aunesa y no
-- se persiste: una vez que pasa el día no hay forma de reconstruir lo que mostró la
-- pantalla. Esto lo congela para auditoría.
--
-- `datos` guarda las filas de la grilla BANCOS de ese día (todos los bancos del
-- catálogo × moneda, con sus 10 filas) y, por cada CELDA, los movimientos que la
-- componen — lo mismo que muestra el modal de auditoría. Así el histórico se navega
-- desde BANCOS: elegís la fecha y clickeás la celda. La tab MOVIMIENTOS queda
-- SIEMPRE en el día y no se guarda aparte: sería la misma data dos veces.
--
-- UNA POR DÍA: `fecha` es única — volver a sacarla PISA la del día (el historial de
-- quién la tomó y cuándo queda en `tesoreria_audit`). TTL de 30 fechas: al insertar
-- se borra lo que quede fuera de las últimas 30, así la tabla no puede crecer.
--
-- `hash_sha256` es del payload serializado: si alguien edita la fila, el hash deja
-- de cerrar. Se guarda para poder DETECTAR una alteración, no para impedirla —
-- impedirla es cosa de los permisos de la DB.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_snapshots (
    id           bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    fecha        date NOT NULL UNIQUE,    -- día fotografiado (una foto por día)
    tomado_at    timestamptz NOT NULL,
    tomado_por   text,                    -- email, o 'cron' si lo tomó el job
    origen       text NOT NULL,           -- 'cron' | 'manual'
    hash_sha256  text NOT NULL,
    datos        jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tesoreria_snapshots_fecha
    ON operaciones.tesoreria_snapshots (fecha DESC);

-- EXCLUSIONES del saldo (modal de auditoría de la grilla BANCOS). Cada movimiento
-- se puede destildar para que NO cuente en el saldo final, y queda la traza de quién
-- y cuándo. Solo se guardan los OVERRIDES: si no hay fila acá, manda el default.
--
-- Default por movimiento: los de Aunesa SIN HORA arrancan DESTILDADOS (el `id` no
-- trae fecha-hora → no se puede distinguir de un duplicado). Tildarlos explícitamente
-- inserta una fila con excluido=false y ahí sí cuentan.
--
-- `fuente` + `ref` identifican el movimiento: aunesa→id de Aunesa, y para el resto
-- el id de su tabla (cheque / mercado / bb / registro).
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_exclusiones (
    fecha       date NOT NULL,
    fuente      text NOT NULL,           -- aunesa | cheque | mercado | bb | registro
    ref         text NOT NULL,
    excluido    boolean NOT NULL DEFAULT true,
    observacion text,                    -- "anulado por x@y a las 14:32"
    actor       text,
    actualizado_at timestamptz,
    PRIMARY KEY (fecha, fuente, ref)
);

-- Nº de cuenta en HYGIRUS (2026-08-06): identificador del banco en el otro sistema.
-- NO se muestra en la grilla (a diferencia de `numero_cuenta`): se guarda para una
-- funcionalidad futura y se edita desde el ABM de bancos.
ALTER TABLE operaciones.tesoreria_cuentas ADD COLUMN IF NOT EXISTS numero_hygirus text;

-- REGISTROS MANUALES (modal REGISTROS MANUALES de la tab BANCOS). Es una FUENTE
-- NUEVA de movimientos que NO viene de la API: la carga el equipo. Impacta el saldo
-- del banco elegido según `sentido` (egreso por default), y el detalle de la celda
-- los muestra marcados como manuales para que se distingan de los de Aunesa.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_registros (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    fecha           date NOT NULL,
    tipo            text NOT NULL,           -- PROVEEDORES / VEP / HABERES / …
    banco           text NOT NULL,           -- cuenta operativa
    unidad          text NOT NULL DEFAULT 'ARS',
    importe         numeric NOT NULL,
    sentido         text NOT NULL DEFAULT 'egreso',   -- egreso | ingreso
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_tesoreria_registros_fecha
    ON operaciones.tesoreria_registros (fecha DESC, banco);
-- El modal tiene DOS tabs, y `grupo` es lo que las separa:
--   'rescate' (default) → panel RESCATE ACA VALORES, `tipo` acotado al catálogo fijo.
--   'otros'             → OTROS REGISTROS, `tipo` es texto LIBRE.
-- Los dos impactan igual el saldo del banco: la diferencia es que 'otros' NO entra
-- al resumen ni al TOTAL de Rescate ACA Valores (el que se ve en la barra).
ALTER TABLE operaciones.tesoreria_registros
    ADD COLUMN IF NOT EXISTS grupo text NOT NULL DEFAULT 'rescate';

-- Fila SALDOS del resumen: es MANUAL y no sale de los registros (por eso no vive
-- en la tabla de arriba). Uno por día y moneda.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_registros_saldo (
    fecha           date NOT NULL,
    unidad          text NOT NULL,
    importe         numeric NOT NULL,
    actualizado_por text,
    actualizado_at  timestamptz,
    PRIMARY KEY (fecha, unidad)
);

-- BANCO A BANCO (tab BANCO A BANCO). Transferencias INTERNAS entre cuentas propias:
-- el equipo mueve saldo de un banco a otro para dejarlos cubiertos. Carga manual,
-- del DÍA. Una fila toca DOS bancos, así que en la grilla BANCOS se abre en dos:
--   banco_a_banco (+)  → la cuenta CRÉDITO recibe  (suma)
--   banco_a_banco (−)  → la cuenta DÉBITO  entrega (resta)
-- Las dos cuentas DEBEN existir en `tesoreria_cuentas` y ser de la MISMA moneda:
-- con un solo `importe` no se puede representar un cambio de divisa (sería otra
-- operación, con dos importes y un TC). Validado server-side.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_banco_a_banco (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    fecha           date NOT NULL,
    cta_debito      text NOT NULL,           -- de dónde sale (cuenta operativa)
    cta_credito     text NOT NULL,           -- a dónde entra
    unidad          text NOT NULL DEFAULT 'ARS',
    importe         numeric NOT NULL,
    estado          text NOT NULL DEFAULT 'pendiente',  -- pendiente | completado
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz,
    CONSTRAINT ck_bb_distintas CHECK (cta_debito <> cta_credito)
);
CREATE INDEX IF NOT EXISTS ix_tesoreria_bb_fecha
    ON operaciones.tesoreria_banco_a_banco (fecha DESC);

-- CHEQUES (tab CHEQUES) — TODO se carga a MANO, nada viene de Aunesa. Una sola
-- tabla para los dos lados; `lado` los separa:
--   'emitido'  → estados pendiente | emitido | completado. Es un TABLERO DE
--                SEGUIMIENTO, NO un listado del día: la vista NO filtra por fecha
--                (un cheque de hace un año que no se cerró tiene que seguir ahí, y
--                uno con fecha_pago futura también). 'completado' lo saca de la
--                vista — la fila igual se conserva para auditoría.
--   'recibido' → estados pendiente | finalizado, y `tipo` echeq | fisico.
--                Los finalizados alimentan la fila "Ingresos e-cheqs" de BANCOS,
--                imputados al día en que se los marcó (por eso `cerrado_at`).
-- `banco` es una cuenta operativa de `tesoreria_cuentas` (las cards de BANCOS) y
-- `comitente` un id_cuenta de `clientes.cuentas`; ambos se snapshotean con su
-- denominación para que la fila siga legible si el catálogo cambia.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_cheques (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    lado            text NOT NULL DEFAULT 'emitido',   -- 'emitido' | 'recibido'
    tipo            text,                    -- recibidos: 'echeq' | 'fisico'
    comitente       text,                    -- id_cuenta de clientes.cuentas
    comitente_denominacion text,             -- snapshot del nombre al guardar
    cuit            text,
    banco           text NOT NULL,           -- cuenta operativa (denominación)
    unidad          text NOT NULL DEFAULT 'ARS',  -- moneda
    importe         numeric NOT NULL,
    estado          text NOT NULL DEFAULT 'pendiente',
    fecha_pago      date,                    -- emitidos: futura = fila naranja
    -- Momento en que se cerró (emitido→completado / recibido→finalizado). Es la
    -- fecha con la que un recibido finalizado entra a la grilla BANCOS.
    cerrado_at      timestamptz,
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
-- CATÁLOGO de MERCADOS y FCI (tab MERCADOS). Igual que `tesoreria_cuentas` para los
-- bancos: se gestiona desde la vista (ABM en modal, solo con permiso de escritura) y
-- toda alta/edición/baja queda en `tesoreria_audit`. `codigo` es el que usa el sistema
-- de origen ('ROFX1172', 'BYMA', 'MAEClearB'…) y `nombre` el legible ('ACSA', 'MAV').
-- Baja LÓGICA (`activa=false`): un mercado que ya se usó en movimientos históricos no
-- se borra nunca, se oculta del desplegable.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_entidades (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    bloque          text NOT NULL,           -- 'mercado' | 'fci'
    codigo          text,                    -- ROFX1172 / BYMA / XROX / A3…
    nombre          text NOT NULL,           -- ACSA / BYMA / MAV / MAE A3…
    activa          boolean NOT NULL DEFAULT true,
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
-- Un mismo código no se repite dentro de un bloque (varios ROFX distintos SÍ pueden
-- compartir nombre: [ROFX1172] ACSA y [ROFX146410] ACSA son dos cuentas distintas).
CREATE UNIQUE INDEX IF NOT EXISTS ux_tesoreria_entidades_codigo
    ON operaciones.tesoreria_entidades (bloque, codigo) WHERE codigo IS NOT NULL;
-- Sin código (típico de FCI) la clave es el nombre.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tesoreria_entidades_nombre
    ON operaciones.tesoreria_entidades (bloque, nombre) WHERE codigo IS NULL;

-- Semilla de una sola vez: los mercados con los que arranca la mesa. Idempotente.
INSERT INTO operaciones.tesoreria_entidades (bloque, codigo, nombre, creado_por, creado_at)
VALUES ('mercado', 'ROFX1172',   'ACSA',              'seed', now()),
       ('mercado', 'ROFX146410', 'ACSA',              'seed', now()),
       ('mercado', 'ROFX274947', 'ACSA',              'seed', now()),
       ('mercado', 'ROFX218115', 'ACSA',              'seed', now()),
       ('mercado', 'ROFX305217', 'ACSA',              'seed', now()),
       ('mercado', 'BYMA',       'BYMA',              'seed', now()),
       ('mercado', 'XROX',       'MAV',               'seed', now()),
       ('mercado', 'MAEClearB',  'MAE A3',            'seed', now()),
       ('mercado', 'A3',         'A3 Mercados S.A.',  'seed', now())
ON CONFLICT DO NOTHING;

-- Número de cuenta del banco (2026-08-06): la denominación sola no alcanza para
-- operar, el back office necesita el número. Se edita desde el ABM de la vista.
ALTER TABLE operaciones.tesoreria_cuentas ADD COLUMN IF NOT EXISTS numero_cuenta text;

-- MERCADOS (tab MERCADOS de Tesorería). Carga MANUAL, del DÍA — mismo espíritu que
-- los cheques recibidos: se registra intradía y no se arrastra. Cuatro tableros que
-- son el mismo modelo con distinto `tipo`:
--   bloque MERCADO → 'ingreso'  (izq) | 'pago'        (der)
--   bloque FCI     → 'rescate'  (izq) | 'suscripcion' (der)
-- `entidad` es el mercado (BYMA, MAE…) o el nombre del FCI según el bloque; `banco`
-- es una cuenta operativa de `tesoreria_cuentas` (validada server-side), de donde
-- sale también la moneda.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_mercados (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    fecha           date NOT NULL,
    tipo            text NOT NULL,           -- ingreso | pago | rescate | suscripcion
    entidad         text,                    -- mercado o FCI
    banco           text NOT NULL,           -- cuenta operativa (denominación)
    unidad          text NOT NULL DEFAULT 'ARS',
    importe         numeric NOT NULL,
    estado          text NOT NULL DEFAULT 'pendiente',  -- pendiente | completado
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_tesoreria_mercados_fecha
    ON operaciones.tesoreria_mercados (fecha DESC, tipo);

CREATE INDEX IF NOT EXISTS ix_tesoreria_cheques_fecha_pago
    ON operaciones.tesoreria_cheques (fecha_pago DESC);
CREATE INDEX IF NOT EXISTS ix_tesoreria_cheques_lado_estado
    ON operaciones.tesoreria_cheques (lado, estado);
-- La tabla nació el 2026-08-06 con solo el lado emitido y estados pendiente/pagado.
-- ALTERs idempotentes para los deploys que ya la tienen creada.
ALTER TABLE operaciones.tesoreria_cheques ADD COLUMN IF NOT EXISTS lado text NOT NULL DEFAULT 'emitido';
ALTER TABLE operaciones.tesoreria_cheques ADD COLUMN IF NOT EXISTS tipo text;
ALTER TABLE operaciones.tesoreria_cheques ADD COLUMN IF NOT EXISTS cerrado_at timestamptz;
UPDATE operaciones.tesoreria_cheques SET estado = 'completado' WHERE estado = 'pagado';

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

-- Cámara de Cereales de BAHÍA BLANCA → carga MANUAL de la mesa (5 cereales, SOLO
-- USD; la ARS se deriva con el dólar BNA). Lo escribe el service
-- api/services/camara_cereales.py::set_camara_cereal_bahia (self-create + write_native).
-- Passthrough jsonb; PK = cereal. data jsonb = {cereal, precio_usd, updated_by, updated_at}.
CREATE TABLE IF NOT EXISTS mercado.camara_cereales_bahia (
    cereal     text PRIMARY KEY,        -- TRIGO | MAIZ | GIRASOL | SOJA | SORGO
    data       jsonb,
    updated_at timestamptz
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

-- Pares Lecap↔CER agregados A MANO desde Manager → TÍTULOS → BREAKEVENS. El motor
-- empareja por vto más cercano (±20d) y deja un solo Lecap por CER, así que hay
-- pares útiles que nunca arma. Éstos NO los conoce el motor: el BE se calcula en la
-- LECTURA (`breakevens_admin._calcular_manuales`, misma función que el motor), por
-- eso aparecen en Renta Fija sin reiniciar nada. Contracara de breakevens_overrides.
CREATE TABLE IF NOT EXISTS mercado.breakevens_manuales (
    lecap      text NOT NULL,
    cer        text NOT NULL,
    creado_por text,
    creado_at  timestamptz,
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

-- Futuros de commodities de CHICAGO (CBOT) del feed Eikon de oficina —
-- AGRO → tab CHICAGO (2026-07-24). 1 fila por RIC de continuación (Sc1, BOc4…),
-- valores CRUDOS de Eikon (¢/bushel, ¢/libra, USD/short ton): los factores a
-- USD/tonelada viven en core/eikon_chicago.py::FAMILIAS y se aplican al leer.
-- POST /api/ingest/eikon/chicago/quotes. Passthrough jsonb (mes/last/var_neta).
CREATE TABLE IF NOT EXISTS mercado.eikon_chicago_snapshot (
    ric        text PRIMARY KEY,             -- RIC de continuación CBOT (ej. Sc1)
    familia    text,                          -- soja | aceite_soja | maiz | trigo | harina_soja
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);

-- Precio OFFSHORE de los soberanos ARG (páginas contribuidas MarketAxess) del
-- feed Eikon de oficina — watchlist HOME ("GD30 OFF") + briefing (2026-07-24).
-- 1 fila por RIC "=1M", crudo; mapeo RIC→bono en core/eikon_bonos.py::BONOS_OFF.
-- POST /api/ingest/eikon/bonos/quotes. Passthrough jsonb.
CREATE TABLE IF NOT EXISTS mercado.eikon_bonos_snapshot (
    ric        text PRIMARY KEY,             -- RIC página offshore (ej. 040114HS2=1M)
    bono       text,                          -- ticker local (GD30, AL30, AE38…)
    data       jsonb,
    updated_at timestamptz DEFAULT now()
);

-- Cierres DIARIOS de los feeds Eikon nuevos (bonos offshore + Chicago), para
-- anchors 7D/MTD/YTD y series (2026-07-24). Writer: jobs/eikon_cierres.py
-- (cron 21:10 UTC L-V — post cierre NY y de la rueda diurna CBOT). `valor` es
-- el precio "display": bonos en USD; Chicago ya convertido a USD/tonelada.
CREATE TABLE IF NOT EXISTS mercado.eikon_cierres (
    grupo text NOT NULL,                     -- 'bonos_off' | 'chicago'
    ric   text NOT NULL,
    fecha date NOT NULL,
    valor numeric,
    PRIMARY KEY (grupo, ric, fecha)
);

-- TITULARES Reuters del feed Eikon de oficina — tab NOTICIAS de la watchlist
-- HOME (2026-07-24). Solo titulares (sin nota completa); retención 7 días
-- aplicada por core/eikon_news.upsert_news → tabla acotada a pocos MB.
CREATE TABLE IF NOT EXISTS mercado.eikon_news (
    story_id   text PRIMARY KEY,             -- URN de Reuters (dedup natural)
    ric        text,                          -- RIC por el que entró el titular
    fecha      timestamptz,                   -- versionCreated de Reuters
    titular    text,
    fuente     text,                          -- sourceCode (ej. NS:RTRS)
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_eikon_news_fecha ON mercado.eikon_news (fecha DESC);

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

-- INGRESOS POR SEGMENTO (familia TR.BGS.* de Eikon, validada en vivo 2026-08-07
-- — ver docs/INTEGRACION_REUTERS.md §8). NO va en el jsonb de eikon_fundamentals
-- porque el grano es otro: ticker × tipo × período × segmento (una fila por
-- pata del desglose). El feed la manda 1 vez por día vía
-- POST /api/ingest/eikon/segmentos.
--
-- OJO con dos cosas que se verificaron y no son inferibles:
--   · `codigo` (segmentCode) trae NAICS para los segmentos REALES y códigos
--     especiales para las filas de cierre: SEGMTL (Segment Total) y CONSTL
--     (Consolidated Total) — que NO se guardan, son totales, sumarlas duplica —
--     más ICELIM (eliminaciones entre segmentos, puede ser negativa) y EXPOTH
--     (corporate/otros), que SÍ se guardan porque son las que hacen cerrar la
--     cuenta: Σ(filas guardadas) = ingresos consolidados exactos.
--   · Los nombres de segmento CAMBIAN con el tiempo (Reuters re-expresa: NVDA
--     tiene un trimestre con "Data Center - Hyperscale" y el resto con
--     "Compute & Networking"). Por eso el segmento es parte de la PK y no hay
--     catálogo cerrado: la vista tiene que bancar segmentos que aparecen y
--     desaparecen.
CREATE TABLE IF NOT EXISTS mercado.eikon_segmentos (
    ticker     text NOT NULL,                -- underlying (US symbol)
    tipo       text NOT NULL,                -- negocio | geografico
    periodo    text NOT NULL,                -- anual | trimestral
    fecha      date NOT NULL,                -- cierre del período fiscal
    segmento   text NOT NULL,                -- nombre tal cual lo publica Reuters
    codigo     text,                         -- segmentCode (NAICS | ICELIM | EXPOTH)
    orden      integer,                      -- segmentDetailsOrder (orden de la memoria)
    ingresos   numeric,                      -- MILLONES de USD (Scale=6, Curn=USD)
    updated_at timestamptz DEFAULT now(),
    PRIMARY KEY (ticker, tipo, periodo, fecha, segmento)
);
CREATE INDEX IF NOT EXISTS ix_eikon_segmentos_ticker
    ON mercado.eikon_segmentos (ticker, tipo, periodo, fecha DESC);

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

-- home.market_calendar — ELIMINADA 2026-08-03. El calendario económico dependía de
-- FMP, que dejó de servir el endpoint (HTTP 402 "Restricted Endpoint") en el plan
-- contratado; la tabla nunca llegó a tener una sola fila. Feature dada de baja.
DROP TABLE IF EXISTS home.market_calendar;

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

-- OHLC diario por CEDEAR (ventana móvil ~60 ruedas) para pivots sobre el CEDEAR
-- en ARS. Lo escribe jobs/cedears_ohlc_daily.py tras el cierre, copiando OP/HI/LO
-- del snapshot + el last como close del día (el `close` del snapshot es el cierre
-- de AYER, no se usa). El job poda las ruedas más viejas → la tabla no crece.
-- `atr` = ATR-20 en ARS (rango típico diario, promedio de los últimos 20 true
-- ranges) que el mismo job calcula una vez por rueda (quant.rango.atr); NULL
-- hasta tener 21 ruedas de historia. La ventana subió de 20→60 justamente para
-- que el ATR-20 sea calculable y quede algo de historia de ATR.
CREATE TABLE IF NOT EXISTS mercado.cedears_ohlc_daily (
    ticker_corto text NOT NULL,
    fecha        date NOT NULL,
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    atr          numeric,                 -- ATR-20 en ARS (quant.rango.atr), NULL sin historia
    PRIMARY KEY (ticker_corto, fecha)
);
ALTER TABLE mercado.cedears_ohlc_daily ADD COLUMN IF NOT EXISTS atr numeric;
CREATE INDEX IF NOT EXISTS ix_cedears_ohlc_tk_fecha
    ON mercado.cedears_ohlc_daily (ticker_corto, fecha DESC);

-- Barras de 1 MINUTO por CEDEAR, ARCHIVO PERMANENTE. Resamplea el tape intradía
-- (mercado.cedears_time_sales, que se VACÍA al cierre) a OHLCV por minuto ANTES
-- del cleanup y lo guarda para siempre (ventana móvil ~60 ruedas). Lo escribe
-- jobs/cedears_bars_1m.py (cron 20:20 UTC L-V, con el motor ya parado → el tape
-- es el día completo). Es la FUENTE DE VERDAD para derivar el Efficiency Ratio
-- intradía (quant.rango.efficiency_ratio) en vivo e histórico, sin depender de
-- que el tape siga vivo. `minuto` = inicio del minuto (UTC).
CREATE TABLE IF NOT EXISTS mercado.cedears_bars_1m (
    ticker_corto text NOT NULL,
    minuto       timestamptz NOT NULL,     -- inicio del minuto (UTC)
    open         numeric,
    high         numeric,
    low          numeric,
    close        numeric,
    volume       numeric,                  -- Σ size de los ticks del minuto
    trades       integer,                  -- # de ticks agregados
    PRIMARY KEY (ticker_corto, minuto)
);
CREATE INDEX IF NOT EXISTS ix_cedears_bars_1m_tk_min
    ON mercado.cedears_bars_1m (ticker_corto, minuto DESC);

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
-- manager.uso_modulos — ELIMINADA 2026-08-04. La telemetría de USO
-- (usuario × módulo) nunca se usó; la reemplaza la de LATENCIA (abajo).
DROP TABLE IF EXISTS manager.uso_modulos;

-- manager.latencia_endpoints — telemetría de LATENCIA por endpoint × hora.
-- La alimenta el middleware de api/main.py vía api/telemetria.py (flush
-- incremental cada ~60s, best-effort); la lee GET /api/manager/latencia.
CREATE TABLE IF NOT EXISTS manager.latencia_endpoints (
    endpoint text NOT NULL,                 -- path normalizado ({id} en segmentos variables)
    hora     timestamptz NOT NULL,          -- bucket horario UTC
    n        bigint  NOT NULL DEFAULT 0,    -- requests
    total_ms bigint  NOT NULL DEFAULT 0,    -- suma de duraciones (avg = total/n)
    max_ms   integer NOT NULL DEFAULT 0,
    lentas   integer NOT NULL DEFAULT 0,    -- requests > 1s
    errores  integer NOT NULL DEFAULT 0,    -- status >= 500
    PRIMARY KEY (endpoint, hora)
);
CREATE INDEX IF NOT EXISTS ix_latencia_endpoints_hora ON manager.latencia_endpoints (hora DESC);

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

-- Transcript del asistente de negocio (nombres REALES — perímetro; el acceso
-- lo gatea el módulo RBAC `asistente`, admin-only). Escrito por
-- api/services/asistente.py en cada turno; el historial que se re-inyecta al
-- LLM se RE-tokeniza al cargar (jamás viaja crudo).
CREATE TABLE IF NOT EXISTS manager.asistente_chats (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    chat_id   text NOT NULL,
    email     text NOT NULL,
    ts        timestamptz NOT NULL DEFAULT now(),
    rol       text NOT NULL,               -- 'user' | 'assistant'
    contenido text NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_asistente_chats_chat  ON manager.asistente_chats (chat_id, ts);
CREATE INDEX IF NOT EXISTS ix_asistente_chats_email ON manager.asistente_chats (email, ts);

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
-- RESEARCH — schema de la vista Research (1816 / BCRA). Las tablas Refinitiv
-- (companies/fundamentals/market_snapshot) se ELIMINARON 2026-07-24 junto con
-- la tab Análisis Fundamental de /renta-variable (scripts/drop_research_refinitiv.py).
-- ─────────────────────────────────────────────────────────────────────────────

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
-- negocio_movimientos: upserts cada 30-60 min generan muertas (medido 2026-08-04:
-- 77k muertas vs 408k vivas, autovacuum una vez por semana con el default 20%).
-- Umbral 2% → vacuum varias veces por día, bloat controlado.
ALTER TABLE operaciones.negocio_movimientos SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_analyze_scale_factor=0.02);
ALTER TABLE operaciones.operaciones         SET (autovacuum_vacuum_scale_factor=0.05, autovacuum_analyze_scale_factor=0.05);
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

-- CONTROL DE CALIDAD de las conversaciones de IA (jobs/ia_calidad.py). Cierra el
-- loop: en vez de que un humano lea trazas a mano, un job diario marca las
-- respuestas sospechosas (👎 del usuario + un crítico barato que busca los modos
-- de falla YA conocidos: ranking a mano, deflexión, causalidad inventada, tool
-- muda…). NO auto-corrige nada — solo las trae a revisión.
CREATE TABLE IF NOT EXISTS ia.calidad_flags (
    traza_id    bigint PRIMARY KEY,            -- la llamada marcada (ia.trazas.id)
    ts          timestamptz NOT NULL DEFAULT now(),
    tarea       text,
    usuario     text,
    modo        text,                          -- ranking_a_mano | deflexion | causalidad | tool_muda | voto_negativo | otro
    severidad   text,                          -- alto | medio | bajo
    nota        text,                          -- por qué (una línea del crítico)
    revisado    boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_calidad_flags_ts ON ia.calidad_flags (ts DESC);

CREATE TABLE IF NOT EXISTS ia.calidad_estado (
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

-- ─────────────────────────────────────────────────────────────────────────────
-- Schema ESTRATEGIA — modelo de señal intradía con trazabilidad
-- (docs/ESTRATEGIA_QUANT.md, vista TRADING → tab ESTRATEGIA, 2026-07-29).
-- Ciclo: engines/estrategia.py EMITE (ledger inmutable) → jobs/estrategia_resolver.py
-- RESUELVE resultados INTRADÍA (el tape cedears_time_sales se VACÍA al cierre —
-- si el resolver espera a la noche, la evidencia ya no existe) → la vista mide
-- el edge → con data suficiente se CALIBRAN pesos (nueva versión en modelo_pesos).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS estrategia;

-- Ledger de señales — APPEND-ONLY, INMUTABLE. Cada fila = una llamada del modelo
-- con el valor crudo de cada factor (auditable factor por factor). NUNCA se
-- edita: el resultado va en estrategia.resultados (join por senal_id).
CREATE TABLE IF NOT EXISTS estrategia.senales (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts             timestamptz NOT NULL DEFAULT now(),
    ticker         text NOT NULL,           -- ticker_corto del CEDEAR
    indice_ref     text NOT NULL,           -- QQQ | SPY (mayor |corr| del papel)
    direccion      text NOT NULL,           -- LONG | SHORT
    score          numeric NOT NULL,        -- -100..100 (signo = dirección)
    precio         numeric,                 -- last ARS al momento de la señal
    pesos_version  text NOT NULL,           -- versión de estrategia.modelo_pesos usada
    factores       jsonb NOT NULL           -- {recorrido_indice, alineacion, nafta,
                                            --  confluencia, inputs:{...crudos}}
);
CREATE INDEX IF NOT EXISTS ix_estrategia_senales_ts ON estrategia.senales (ts DESC);
CREATE INDEX IF NOT EXISTS ix_estrategia_senales_tk_ts ON estrategia.senales (ticker, ts DESC);

-- Resultado de cada señal a cada horizonte (15/30/60 min) — lo escribe el
-- resolver DURANTE la rueda. ret_pct con signo de la DIRECCIÓN de la señal
-- (positivo = la señal ganó). mfe/mae = máxima excursión a favor / en contra.
CREATE TABLE IF NOT EXISTS estrategia.resultados (
    senal_id       bigint NOT NULL REFERENCES estrategia.senales(id),
    horizonte_min  int NOT NULL,            -- 15 | 30 | 60
    ret_pct        numeric,                 -- retorno direccional al horizonte
    mfe_pct        numeric,                 -- max favorable excursion (≥0)
    mae_pct        numeric,                 -- max adverse excursion (≤0)
    toco_objetivo  boolean,                 -- tocó +objetivo antes que -stop
    gano           boolean,                 -- ret_pct > 0
    parcial        boolean DEFAULT false,   -- resuelto con menos minutos (cierre)
    resuelto_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (senal_id, horizonte_min)
);

-- Pesos del modelo, VERSIONADOS: cada señal guarda su pesos_version →
-- reproducibilidad total (se puede comparar modelo v1 vs v2). El engine usa la
-- fila con activo=true (única). v1 = pesos manuales (hipótesis); las siguientes
-- salen de calibrar contra estrategia.resultados.
CREATE TABLE IF NOT EXISTS estrategia.modelo_pesos (
    version     text PRIMARY KEY,           -- 'v1-manual', 'v2-cal-2026-09', ...
    pesos       jsonb NOT NULL,             -- {recorrido_indice, alineacion, nafta, confluencia}
    activo      boolean NOT NULL DEFAULT false,
    notas       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Última evaluación live por ticker (UPSERT del engine cada ciclo, se emita o no
-- señal al ledger) — la lee GET /api/estrategia/live para la zona LIVE de la
-- vista. Efímera por naturaleza (solo vale la fila de hoy).
CREATE TABLE IF NOT EXISTS estrategia.eval_live (
    ticker      text PRIMARY KEY,
    ts          timestamptz NOT NULL,
    data        jsonb NOT NULL              -- score, direccion, factores, inputs
);

-- ─────────────────────────────────────────────────────────────────────────────
-- VEPs (tab VEPS de Tesorería) — agenda de vencimientos, TODOS egresos.
--
-- Es un tablero de SEGUIMIENTO, igual que los cheques EMITIDOS: no filtra por
-- fecha (un VEP viejo sin pagar sigue a la vista) y la fila NO se borra al
-- pagarse, queda para el histórico.
--
-- NO IMPACTA EL SALDO de la grilla BANCOS, y es a propósito: el egreso del VEP
-- ya entra al banco por REGISTROS MANUALES (`tesoreria_registros`, tipo 'VEP').
-- Si esta tabla también sumara, el mismo VEP se contaría DOS VECES.
--
-- Se puede cargar de dos formas y las dos conviven:
--   1) A mano desde la tab VEPS.
--   2) SOLA, como espejo, cuando alguien carga un registro manual de tipo 'VEP'
--      — se crea con lo que ese registro tiene (importe, banco, moneda) y el
--      resto de los campos se completan después desde la tab. `registro_id`
--      apunta al registro que la generó y es ÚNICO: re-guardar el registro no
--      duplica la fila.
CREATE TABLE IF NOT EXISTS operaciones.tesoreria_veps (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    numero_vep      text,                    -- puede faltar si nació del registro manual
    concepto        text,
    importe         numeric NOT NULL,
    banco           text NOT NULL,           -- cuenta operativa (denominación); default AL2
    unidad          text NOT NULL DEFAULT 'ARS',
    vencimiento     date,                    -- vencida y sin pagar = fila AMARILLA
    estado          text NOT NULL DEFAULT 'pendiente',   -- 'pendiente' | 'pagado'
    -- Trazabilidad del origen: 'manual' (cargado en la tab) | 'registro' (espejo
    -- de un registro manual de tipo VEP).
    origen          text NOT NULL DEFAULT 'manual',
    registro_id     bigint,                  -- FK lógica a tesoreria_registros.id
    pagado_at       timestamptz,
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
-- Un registro manual genera UN solo VEP (idempotencia del espejo). Parcial: los
-- cargados a mano tienen registro_id NULL y no compiten entre sí.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tesoreria_veps_registro
    ON operaciones.tesoreria_veps (registro_id) WHERE registro_id IS NOT NULL;
-- El tablero ordena por vencimiento y separa pendientes de pagados.
CREATE INDEX IF NOT EXISTS ix_tesoreria_veps_estado_venc
    ON operaciones.tesoreria_veps (estado, vencimiento);

-- ─────────────────────────────────────────────────────────────────────────────
-- SALUD (Manager → SALUD). Ver api/services/salud.py para el modelo.
--
-- El estado ACTUAL no se persiste: se evalúa en vivo desde el crontab + job_runs
-- + los contratos de frescura. Lo que sí se guarda es lo que no se puede
-- recalcular después:
--
--   salud_eventos — las TRANSICIONES (verde→rojo→verde). Append-only. Es el
--     historial que responde "cuándo se rompió y cuándo volvió", con la evidencia
--     congelada del momento (después el motivo ya no está: el job volvió a correr).
--   salud_config  — el toggle de alerta por chequeo. Silenciar algo NO lo saca de
--     la pantalla: solo deja de abrir el modal. Un chequeo silenciado sigue rojo.
--   salud_vistos  — qué eventos ya vio cada admin, para que el modal no repita.
CREATE TABLE IF NOT EXISTS manager.salud_eventos (
    id         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    chequeo_id text NOT NULL,               -- 'job:aum', 'dato:portafolio.tenencia'
    familia    text,
    titulo     text,
    de         text,                        -- estado anterior (NULL = primera vez)
    a          text NOT NULL,               -- ok | warn | error
    motivo     text,
    evidencia  text,
    at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_salud_eventos_at ON manager.salud_eventos (at DESC);
CREATE INDEX IF NOT EXISTS ix_salud_eventos_chequeo
    ON manager.salud_eventos (chequeo_id, at DESC);

CREATE TABLE IF NOT EXISTS manager.salud_config (
    chequeo_id      text PRIMARY KEY,
    alertar         boolean NOT NULL DEFAULT true,
    nota            text,                   -- por qué se silenció
    actualizado_por text,
    actualizado_at  timestamptz
);

CREATE TABLE IF NOT EXISTS manager.salud_vistos (
    email     text   NOT NULL,
    evento_id bigint NOT NULL,
    visto_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (email, evento_id)
);

-- Diagnóstico con IA de un chequeo de SALUD que YA se confirmó persistente. Se
-- cachea por (chequeo_id, evento_id): el mismo incidente NO se re-diagnostica —
-- si no, cada poll de la vista gastaría tokens repitiendo lo mismo.
CREATE TABLE IF NOT EXISTS manager.salud_diagnosticos (
    chequeo_id text   NOT NULL,
    evento_id  bigint NOT NULL,             -- la transición que lo disparó
    texto      text,
    modelo     text,
    creado_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chequeo_id, evento_id)
);
