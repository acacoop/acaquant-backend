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
-- El AV AGENT tiene schema PROPIO (2026-08-22). Sus 15+ tablas nacieron en
-- `mercado` por inercia del primer detector — y `mercado` es DATOS DE MERCADO,
-- no la memoria del agente. La mudanza vive en un bloque DO más abajo (antes
-- del primer CREATE TABLE del agente): ALTER TABLE ... SET SCHEMA es un cambio
-- de metadata instantáneo, los datos no se copian ni se pierden.
CREATE SCHEMA IF NOT EXISTS agente;

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
-- Huella de las expresiones con las que se calculó la fila (jobs/ops_agregado.
-- formula_hash). Un cambio de FÓRMULA no mueve ningún `ingestado_en`, así que sin
-- esto los días cerrados quedaban fosilizados con el cálculo viejo (incidente
-- 2026-08-10: el fix de dolarización no llegaba a la serie histórica).
ALTER TABLE operaciones.ops_agregado_diario ADD COLUMN IF NOT EXISTS formula_hash text;
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

-- ── AJUSTES DE PnL (eventos corporativos sin boleto: splits, canjes, pre-data) ──
-- Un split (ej. CEDEAR YPF 10:1) cambia la tenencia SIN generar boleto en Aunesa →
-- el cost-basis del motor de PnL (api/services/pnl.py) queda con la cantidad
-- pre-split y el PnL no realizado se rompe. Estos ajustes viven en TABLA PROPIA
-- (jamás como filas en negocio_movimientos: la reconciliación horaria de
-- jobs/negocio_movimientos las anularía en silencio) y pnl_sql._deps_sql los
-- mergea al stream cronológico de boletos como pseudo-boletos.
--   tipo='split'    → factor multiplica la cantidad viva (10 = split 10:1,
--                     0.1 = reverse 1:10). NO toca costo ni realizado.
--   tipo='cantidad' → delta con signo. >0 suma cantidad con costo opcional
--                     (canje entrante, posición pre-data); <0 resta liberando
--                     costo PROPORCIONAL sin generar realizado (canje saliente).
--   id_cuenta NULL  → GLOBAL: aplica a TODAS las cuentas con boletos del ticker
--                     (un split se carga UNA vez). Con id_cuenta: solo esa cuenta.
--   fecha           → el ajuste se aplica ANTES de los boletos de ese día.
--   activo=false    → apagado sin borrar (para probar el efecto / deshacer).
-- Escritura: SOLO admin (endpoints /api/portfolio/pnl-ajustes*); todo cambio
-- queda en pnl_ajustes_audit con before/after.
CREATE TABLE IF NOT EXISTS operaciones.pnl_ajustes (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tipo            text NOT NULL CHECK (tipo IN ('split', 'cantidad')),
    ticker          text NOT NULL,            -- match_key del motor (= negocio_movimientos.ticker; FCI usa CAFCI)
    id_cuenta       text,                     -- NULL = todas las cuentas con boletos del ticker
    fecha           date NOT NULL,            -- fecha efectiva (se aplica antes de los boletos del día)
    factor          numeric,                  -- tipo='split': qty *= factor (> 0)
    cantidad        numeric,                  -- tipo='cantidad': delta con signo (≠ 0)
    costo           numeric,                  -- tipo='cantidad' y delta>0: costo asociado (opcional, default 0)
    moneda          text NOT NULL DEFAULT 'ARS',  -- moneda del costo (ARS/USD/USDC — pesifica con MEP de `fecha`)
    nota            text,                     -- descripción libre ("Split 10:1 CEDEAR YPF")
    activo          boolean NOT NULL DEFAULT true,
    creado_por      text,
    creado_at       timestamptz DEFAULT now(),
    actualizado_por text,
    actualizado_at  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_pnl_ajustes_ticker ON operaciones.pnl_ajustes (ticker, fecha);

CREATE TABLE IF NOT EXISTS operaciones.pnl_ajustes_audit (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     timestamptz,
    actor  text,                              -- email de quién hizo el cambio
    action text,                              -- create / update / delete
    target text,                              -- id del ajuste
    data   jsonb                              -- before/after
);
CREATE INDEX IF NOT EXISTS ix_pnl_ajustes_audit_ts ON operaciones.pnl_ajustes_audit (ts DESC);

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

-- ACCESO a la vista (2026-08-11). Antes VER Mesa de Dinero lo daba el módulo
-- `operaciones` → la veía todo NEGOCIO. Ahora es allowlist per-usuario, igual
-- que la escritura: el criterio es "estas personas", no "este puesto", y un
-- módulo no puede expresar eso sin inventar un rol por combinación.
-- REGLA: escribir IMPLICA leer (mesa_dinero_escritores entra por unión), así
-- las dos listas no se pueden contradecir. admin siempre puede.
CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero_lectores (
    email        text PRIMARY KEY,           -- usuario de la app con permiso de LECTURA
    agregado_por text,
    agregado_at  timestamptz
);

-- ACCESO PARCIAL — SOLO la tab RESULTADOS (2026-08-17). Segunda lista, no una
-- columna de la de arriba: son dos grupos distintos de gente y el panel del
-- Manager es el mismo componente para las dos. Quien está acá entra a
-- /mesa-dinero pero SOLO ve RESULTADOS (por cliente / por comercial): NO ve el
-- detalle operación por operación (activo, VN, precios, trader) ni la tab ACA
-- VALORES RETORNO. Nace del pedido de dar el tablero de resultados a operadores
-- comerciales sin abrirles la operatoria de la mesa.
-- REGLA: el acceso MÁS AMPLIO gana. Si el email también es admin, escritor o
-- lector completo, ve TODO — esta lista solo agrega gente, nunca recorta.
CREATE TABLE IF NOT EXISTS operaciones.mesa_dinero_lectores_resultados (
    email        text PRIMARY KEY,           -- usuario de la app que ve SOLO resultados
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
    -- Tilde PROPIA de la tab EXCEL MAE (2026-08-13): la marca el TRADER cuando
    -- ya cargó esa orden en el MAE. Es INDEPENDIENTE de `estado` a propósito —
    -- `estado` es el laburo del BACK OFFICE (Quantex) y mezclarlos hacía que un
    -- equipo le moviera el tablero al otro. Solo afecta al Excel MAE: la orden
    -- tildada sale del archivo (no se re-carga al re-generarlo) pero SIGUE
    -- visible en la tab, grisada, para poder destildarla.
    mae_completada     boolean NOT NULL DEFAULT false,
    mae_completada_por text,                 -- email del trader que la tildó
    mae_completada_at  timestamptz,
    -- Espejo de `editada_completada` para el MAE: se prende si se editó una
    -- orden que YA estaba tildada → el MAE quedó cargado con los datos viejos
    -- (fila amarilla en la tab + botón ⚠ EDITADA que la baja). Marca aparte
    -- de la de Quantex porque la baja OTRO equipo: el trader mira el MAE, el
    -- back office mira Quantex, y el visto de uno no puede tapar al del otro.
    mae_editada_completada boolean NOT NULL DEFAULT false,
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
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS mae_completada boolean NOT NULL DEFAULT false;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS mae_completada_por text;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS mae_completada_at timestamptz;
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS mae_editada_completada boolean NOT NULL DEFAULT false;
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

-- SEGMENTOS del MAE (columna SEGMENTO del Excel MAE). Catálogo chico y editable
-- desde la vista SENEBIS, igual que el de agentes: el MAE puede sumar segmentos
-- y no queremos un deploy por eso. El default operativo vive en el código
-- (`senebis.SEGMENTO_MAE_DEFAULT`), no acá: la tabla es solo el universo válido.
CREATE TABLE IF NOT EXISTS operaciones.senebis_segmentos (
    nombre          text PRIMARY KEY,
    creado_por      text,
    creado_at       timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
-- Semilla: el segmento que usa la mesa por defecto. ON CONFLICT → re-aplicar el
-- schema no pisa ni duplica nada.
INSERT INTO operaciones.senebis_segmentos (nombre, creado_por, creado_at)
VALUES ('Bilateral MAEClear', 'schema', now())
ON CONFLICT (nombre) DO NOTHING;

-- SEGMENTO de la orden: solo aplica a las `es_mae` (es una columna del Excel
-- MAE). Se guarda el NOMBRE, no un id: el Excel lleva el texto y así una baja
-- del catálogo no huerfana las órdenes históricas.
ALTER TABLE operaciones.senebis ADD COLUMN IF NOT EXISTS segmento text;

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
    -- 'manual' (lo cargó el back office) | 'aunesa' (espejo del e-cheq de EGRESO que
    -- informó la API). Ver el bloque de ALTERs de abajo.
    origen          text NOT NULL DEFAULT 'manual',
    mov_id          text,                    -- id del movimiento de Aunesa espejado
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

-- ESPEJO AUTOMÁTICO de los e-cheq EMITIDOS (2026-08-10). Un egreso de Aunesa con
-- RIEL '[E CHEQ] E CHEQ' ES un cheque emitido: hasta ahora el back office lo veía en
-- MOVIMIENTOS y lo VOLVÍA A CARGAR a mano en la tab CHEQUES, y el saldo lo contaba
-- dos veces. Ahora la fila se crea sola (estado 'emitido', fecha de pago = el día del
-- movimiento) y queda distinguida por `origen`.
--
-- Las filas con origen='aunesa' NO restan del saldo de BANCOS: esa plata YA entra a la
-- fila `egresos_echeq` por el movimiento de Aunesa. Solo restan las 'manual' (mismo
-- criterio que los VEPs espejo, ver tesoreria_veps).
ALTER TABLE operaciones.tesoreria_cheques ADD COLUMN IF NOT EXISTS origen text NOT NULL DEFAULT 'manual';
ALTER TABLE operaciones.tesoreria_cheques ADD COLUMN IF NOT EXISTS mov_id text;
-- Un movimiento de Aunesa genera UN solo cheque: es la idempotencia del espejo, que
-- corre en CADA poll de la tab (cada 20s). Parcial: los manuales tienen mov_id NULL y
-- no compiten entre sí.
CREATE UNIQUE INDEX IF NOT EXISTS ux_tesoreria_cheques_mov
    ON operaciones.tesoreria_cheques (mov_id) WHERE mov_id IS NOT NULL;

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
-- (fecha, unidad) — las vistas que arrancan por INSTRUMENTO y no por cuenta
-- (FINANCIAMIENTO: "qué papeles vivos hay y quién los tiene") filtran por `fecha`
-- y joinean por `unidad`. `ix_tenencia_cuenta_fecha` NO sirve para eso: su columna
-- líder es `id_cuenta`, así que sin este índice la query es un SEQ SCAN de toda la
-- tenencia histórica (todas las fechas × cuentas × unidades) para quedarse con un
-- día. Con él, el filtro por fecha es un rango contiguo y el join por unidad entra
-- dentro de ese rango.
CREATE INDEX IF NOT EXISTS ix_tenencia_fecha_unidad ON portafolio.tenencia(fecha, unidad);

-- ── TENENCIA LIVE — la posición del DÍA, refrescada durante la rueda ──────────
-- Tabla APARTE de `tenencia` a propósito, y NADIE la lee todavía. `tenencia` es la
-- foto conciliada e inmutable de ayer (writer diario, 11 UTC) y no se toca; acá
-- vive el presente, que se mueve todo el día. Mezclarlas sería poner un número
-- que muta adentro de la columna cuyo contrato es "día cerrado".
--
-- Dos filas por (cuenta, unidad), las dos con `fecha = hoy`:
--   horizonte='t0' → consultada con desde = hoy+1 hábil → posición LIQUIDADA A HOY
--                    (lo que está en custodia: se puede entregar/garantizar).
--   horizonte='t1' → consultada con desde = hoy+2 hábiles → liquidada a MAÑANA,
--                    ya con lo concertado hoy adentro (la posición "económica").
-- Regla detrás (medida 2026-08-11): `desde = X` en `posicionValuada` devuelve la
-- posición liquidada al día hábil ANTERIOR a X.
--
-- NO es acumulativa: el primer barrido del día borra lo anterior (`fecha < hoy`).
-- Writer ÚNICO: jobs/tenencia_live.py (daemon, cron 11 UTC L-V).
CREATE TABLE IF NOT EXISTS portafolio.tenencia_live (
    fecha            date NOT NULL,
    horizonte        text NOT NULL,            -- 't0' | 't1'
    id_cuenta        text NOT NULL,
    unidad           text NOT NULL,
    cuenta           text,
    ticker           text,
    cartera          text,
    cantidad         numeric,
    precio           numeric,
    valuacion        numeric,
    moneda           text,
    aum              text,
    tipo_titulo      text,
    gar_cantidad     numeric,
    -- La fecha que EFECTIVAMENTE se le mandó a Aunesa. Con esto la fila se explica
    -- sola: nadie tiene que conocer la regla ni contar días hábiles para auditarla.
    desde_consultado date,
    origen           text,                     -- 'apertura' | 'boleto'
    -- Frescura POR CUENTA. Si Aunesa falla, la fila anterior se conserva y esto
    -- envejece a la vista — una tabla "live" congelada en silencio es peor que no
    -- tenerla. Es lo que permite que la UI diga "actualizado hace 2 min".
    actualizado_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, horizonte, id_cuenta, unidad)
);
CREATE INDEX IF NOT EXISTS ix_tlive_cuenta
    ON portafolio.tenencia_live(id_cuenta, fecha, horizonte);
CREATE INDEX IF NOT EXISTS ix_tlive_frescura
    ON portafolio.tenencia_live(fecha, actualizado_at);

-- ── control_saldos — SALDO LIQUIDADO de hoy por cuenta y moneda ───────────────
-- Fuente: endpoint `cuentas/{id}/posiciones` de Aunesa (Resumen de posiciones),
-- que separa lo LIQUIDADO de lo PENDIENTE DE LIQUIDAR. `tenencia_live` no sirve
-- para esto: es una posición PROYECTADA (mete adentro lo que todavía no liquidó),
-- así que una caución que vence mañana deja la cuenta en falso negativo hoy.
--
-- `cantidad` ya viene con el SIGNO CORREGIDO: Aunesa manda las tenencias al revés
-- (dice -56.095,90 para una cuenta que tiene pesos a favor), igual que
-- `posicionValuada`. Negativo en esta tabla = DESCUBIERTO REAL.
--
-- `filas_origen` = cuántas filas del endpoint se sumaron para esa moneda. El
-- endpoint devuelve más de una fila por moneda y no sabemos qué las separa
-- (estado/lugar/subCuenta/informacion vienen idénticos), así que se suman — un
-- saldo es aditivo — y esta columna deja el rastro para auditar una cuenta rara.
--
-- NO es acumulativa: el primer barrido del día borra lo anterior (`fecha < hoy`).
-- Writer ÚNICO: jobs/control_saldos.py (daemon, cron 11 UTC L-V).
CREATE TABLE IF NOT EXISTS portafolio.control_saldos (
    fecha              date NOT NULL,
    id_cuenta          text NOT NULL,
    ticker             text NOT NULL,          -- 'ARS' | 'USD' | 'USDL' | 'USDC'
    cuenta             text,                   -- '[805] DENOMINACIÓN'
    cantidad           numeric,                -- saldo LIQUIDADO (signo ya corregido)
    cantidad_pendiente numeric,                -- lo que falta liquidar (auditoría)
    filas_origen       integer,
    origen             text,                   -- 'apertura' | 'boleto'
    -- Frescura POR CUENTA: si Aunesa falla se conserva la fila anterior y esto
    -- envejece a la vista. Un tablero live congelado en silencio es peor que no
    -- tenerlo.
    actualizado_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fecha, id_cuenta, ticker)
);
CREATE INDEX IF NOT EXISTS ix_csaldos_negativos
    ON portafolio.control_saldos(fecha, cantidad);
CREATE INDEX IF NOT EXISTS ix_csaldos_cuenta
    ON portafolio.control_saldos(id_cuenta, fecha);

-- Presencia GENÉRICA por vista: quién tiene una pantalla abierta ahora mismo.
-- El poll de la vista ES el heartbeat (no hay nada extra del lado del cliente);
-- presente = visto en los últimos 90s.
--
-- Es genérica (`vista` en la PK) porque ya existían DOS tablas idénticas
-- (operaciones.senebis_presencia y operaciones.tesoreria_presencia) y la tercera
-- copia era el momento de parar. Esas dos NO se migraron: hacerlo es gratis
-- después y no valía el riesgo de mover dos vistas que funcionan.
CREATE TABLE IF NOT EXISTS manager.presencia (
    vista    text NOT NULL,                 -- 'saldos'
    email    text NOT NULL,
    visto_at timestamptz NOT NULL,
    PRIMARY KEY (vista, email)
);

-- Cuentas que el back office decide NO VER en el control de saldos. Se manejan
-- desde la propia vista (mismo patrón que el catálogo de agentes de SENEBIS).
--
-- ⚠️ OCULTAR NO ES EXCLUIR: la fila se sigue escribiendo en control_saldos — el
-- saldo existe y el daemon lo persiste. Lo único que cambia es que la pantalla
-- no lo muestra. Por eso el filtro vive en la LECTURA (api/services/
-- titulos_negativos.py) y no en el job: es una preferencia de visualización, no
-- una regla sobre qué es un saldo válido. Distinto del umbral de ruido
-- (jobs/control_saldos.py::UMBRALES), que sí decide qué NO se persiste.
--
-- `creado_por` / `creado_at`: ocultar es esconderle información al resto del
-- equipo, así que tiene que tener nombre y fecha. Re-ocultar una cuenta ya
-- oculta refresca los dos: el último que tomó la decisión es el que figura.
CREATE TABLE IF NOT EXISTS portafolio.control_saldos_ocultas (
    id_cuenta  text PRIMARY KEY,
    cuenta     text,                      -- denominación, resuelta al ocultar
    motivo     text,
    creado_por text,
    creado_at  timestamptz NOT NULL DEFAULT now()
);

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
    instrumento    text,             -- símbolo Primary de la pata en PESOS (MERV - XMEV - AL30 - 24hs)
    instrumento_usd text,            -- símbolo Primary de la pata en DÓLARES (…AL30D…)
    calificacion   text,
    cafci          text,
    vencimiento    text,
    codigo_cnv     text,
    fee_admin      numeric,          -- fracción (0.01 = 1%), FCI
    -- VIGENCIA (2026-08-15). Un título que amortizó DEJA de existir en el mercado,
    -- pero NO se puede borrar del catálogo: la tenencia histórica lo referencia.
    -- Sin esta marca no había forma de distinguir "no existe porque venció" de
    -- "no existe porque el símbolo está mal", y todo lo vencido sería un falso
    -- positivo eterno para `jobs/validar_instrumentos`.
    vigente        boolean DEFAULT true,
    vigencia_motivo text,             -- 'vencido' = lo puso el job · resto = la mesa
    vigencia_at    timestamptz,
    actualizado_por text,
    actualizado_at  timestamptz
);
-- Columnas nuevas del 2026-08-15: sobre la tabla que ya existe el CREATE de arriba
-- es no-op, así que SOLO pueden entrar por ALTER. `instrumento_usd` la llena la
-- regla `especies` de jobs/assets_autofill desde `mercado.especies` (nunca pisa lo
-- cargado); la vigencia la mantiene `jobs/validar_instrumentos`.
ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS instrumento_usd text;
ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS vigente boolean DEFAULT true;
ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS vigencia_motivo text;
ALTER TABLE portafolio.assets ADD COLUMN IF NOT EXISTS vigencia_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_assets_vigente ON portafolio.assets(vigente);
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
-- NOMBRES (rediseño 2026-08-15): `ticker` es el TICKER del bono (AL30) — el que
-- joinea con portafolio.assets.ticker — e `instrumento` es el SÍMBOLO DE MERCADO
-- (`MERV - XMEV - AL30 - 24hs`), que es lo que se le manda a Primary para pedir
-- market data. Estaban al revés: la PK se llamaba `ticker_corto` y la columna
-- `ticker` guardaba el símbolo. Ver el bloque DO de más abajo.
CREATE TABLE IF NOT EXISTS mercado.curvas (
    ticker            text PRIMARY KEY,      -- AL30  (= portafolio.assets.ticker)
    instrumento       text,                  -- MERV - XMEV - AL30 - 24hs  (símbolo Primary)
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
    data              jsonb,
    -- EJES del rediseño 2026-08-15 (docs/RENTA_FIJA.md §0). Conviven con `curva`
    -- (que sigue mandando en la vista hasta el paso 3) y los deriva
    -- `scripts/clasificar_curvas.py` cruzando contra el catálogo de 1816.
    -- Nullable a propósito: "sin clasificar" es un estado válido y visible, no
    -- se adivina. `moneda_eje` se llama así para no chocar con `moneda_flujo`,
    -- que es otra cosa (cómo se VALÚA, no en qué se denomina).
    emisor_tipo       text,   -- soberano | provincial | corporativo | bcra
    moneda_eje        text,   -- ARS | USD | EUR
    ajuste            text,   -- fija | cer | tamar | badlar | dolar_linked | tpm | caucion
    ajuste_alt        text,   -- la SEGUNDA pata de un dual (mismo dominio). NULL = no es dual
    ley               text    -- local | ny  (Bonar vs Global)
);
-- Por qué DOS columnas de ajuste y no un `ajuste='dual'`. Un dual no es una
-- familia aparte: es un bono con DOS patas de rendimiento, y el trader lo mira en
-- la tabla de CER *y* en la de TAMAR — no lo archiva en un solo lado. Guardar
-- `dual` lo sacaba de las dos y además DESTRUÍA el dato: no decía contra qué
-- ajusta, así que se perdía que uno es CER+TAMAR y otro CER+devaluación.
-- Con `ajuste` + `ajuste_alt` todo cae solo y sin reglas especiales:
--     tabla CER   = ajuste='cer'   OR ajuste_alt='cer'
--     tabla TAMAR = ajuste='tamar' OR ajuste_alt='tamar'
--     "es dual"   = ajuste_alt IS NOT NULL      ← deja de cargarse, se deduce
-- Dos columnas y no un array porque "dual" significa exactamente DOS: con un
-- `text[]` habría que cambiar el tipo, el índice (a GIN) y enseñarle a cinco
-- services a leer arrays, para una cardinalidad que está acotada. Si algún día
-- aparece un triple, ahí sí conviene la lista.
-- SIN índice sobre `ajuste_alt` a propósito: son 221 filas y el Seq Scan es
-- óptimo (misma razón por la que no se indexan las tablas chicas de Tesorería).
-- El eje bono/letra (`tipo_instrumento`) fue ELIMINADO el 2026-08-15: nació con el
-- rediseño y nunca se pobló (221 de 221 bonos sin dato, medido con
-- `scripts/diag_curvas_columnas`). Una columna vacía no agrupa nada y sí obliga a
-- todo el que lee la tabla a preguntarse qué significa.

-- RENOMBRE de columnas (2026-08-15). El CREATE TABLE de arriba es no-op sobre una
-- tabla que ya existe, así que en la DB real esto SOLO entra por ALTER. Postgres no
-- tiene `RENAME COLUMN IF EXISTS` → el guard va a mano contra information_schema,
-- que es lo que lo hace idempotente (apply_schema corre en cada deploy).
--
-- El orden importa y no es intercambiable: primero hay que LIBERAR el nombre
-- `instrumento` (lo ocupaba el eje bono/letra, que nació vacío el 2026-08-15 y
-- nunca se pobló), recién después se le puede dar ese nombre al símbolo de mercado.
--
-- ⚠️ El paso 1 ORIGINAL renombraba ese eje a `tipo_instrumento`. Al eliminarse la
-- columna, ese rename se volvió UNA TRAMPA: su condición ("existe `instrumento` y
-- no existe `tipo_instrumento`") es exactamente el estado de la base YA migrada,
-- así que el próximo apply_schema le habría puesto `tipo_instrumento` al SÍMBOLO
-- DE MERCADO y la vista se quedaba sin precios. Por eso el paso 1 ahora BORRA en
-- vez de renombrar, y distingue los dos mundos por `ticker_corto`: si esa columna
-- todavía existe, la base nunca corrió el renombre y `instrumento` ES el eje viejo.
DO $$
BEGIN
    -- 1) el eje bono/letra se ELIMINA y con eso libera el nombre `instrumento`
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'mercado'
               AND table_name = 'curvas' AND column_name = 'ticker_corto')
    THEN ALTER TABLE mercado.curvas DROP COLUMN IF EXISTS instrumento;
    END IF;
    ALTER TABLE mercado.curvas DROP COLUMN IF EXISTS tipo_instrumento;

    -- 2) el símbolo de mercado pasa a llamarse como lo que es
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'mercado'
                   AND table_name = 'curvas' AND column_name = 'instrumento')
       AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'mercado'
                   AND table_name = 'curvas' AND column_name = 'ticker')
       AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'mercado'
                   AND table_name = 'curvas' AND column_name = 'ticker_corto')
    THEN ALTER TABLE mercado.curvas RENAME COLUMN ticker TO instrumento;
    END IF;

    -- 3) y la PK queda con el nombre correcto: `ticker`
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'mercado'
               AND table_name = 'curvas' AND column_name = 'ticker_corto')
    THEN ALTER TABLE mercado.curvas RENAME COLUMN ticker_corto TO ticker;
    END IF;
END $$;

-- Los EJES en tablas que YA existen (mismo motivo: el CREATE TABLE es no-op).
ALTER TABLE mercado.curvas ADD COLUMN IF NOT EXISTS emisor_tipo      text;
ALTER TABLE mercado.curvas ADD COLUMN IF NOT EXISTS moneda_eje       text;
ALTER TABLE mercado.curvas ADD COLUMN IF NOT EXISTS ajuste           text;
ALTER TABLE mercado.curvas ADD COLUMN IF NOT EXISTS ajuste_alt       text;
ALTER TABLE mercado.curvas ADD COLUMN IF NOT EXISTS ley              text;
ALTER TABLE mercado.curvas ADD COLUMN IF NOT EXISTS instrumento      text;
CREATE INDEX IF NOT EXISTS ix_curvas_curva ON mercado.curvas(curva);
CREATE INDEX IF NOT EXISTS ix_curvas_ejes  ON mercado.curvas(moneda_eje, ajuste);
CREATE INDEX IF NOT EXISTS ix_curvas_vto   ON mercado.curvas(fecha_vencimiento);

-- mercado.tamar_1816 — LA TASA Y EL MARGEN DE LOS TAMAR, TRAÍDOS DE 1816 (2026-08-16).
--
-- **El problema.** Los bonos TAMAR no tenían NINGÚN cálculo: caen en el `else` de
-- `engines/curvas.py` y solo se les computaba duration. No estaban mal valuados
-- —estaban SIN valuar— y eso no se veía porque una celda vacía no rompe nada.
-- Y lo que la mesa mira de un TAMAR no es tanto la TEA sino el **MARGEN sobre la
-- TAMAR**: cuánto paga por encima de la tasa de referencia del BCRA.
--
-- **Por qué no lo calculamos nosotros.** Un TAMAR es una nota de tasa PROMEDIO:
-- el cupón es el promedio de la TAMAR de bancos privados entre T−10 hábiles de
-- emisión y T−10 del vencimiento, más un margen fijado en licitación. La parte ya
-- observada está congelada y la futura hay que proyectarla. Medido contra la
-- planilla de la mesa (2026-08-16): 1816 da TXMD9 TEA 38,62% / margen 9,73% y la
-- planilla dice 38,55% / 9,71% — o sea que **la mesa ya valida contra 1816**.
-- Reimplementar la metodología nos pondría a competir con el número que ellos ya
-- miran, y una diferencia de 3 puntos básicos alcanzaría para que nadie use el
-- nuestro aunque tuviera razón.
--
-- **UNA FILA POR PATA, y ahí está el fix de los duales.** La PK es (ticker, pata)
-- y no el ticker solo. Un dual CER+TAMAR rinde DISTINTO según por qué pata se lo
-- mire, y `mercado.market_snapshot` no puede representarlo: tiene una fila por
-- símbolo de mercado y por lo tanto UNA sola TEA. Medido el 2026-08-16, la
-- diferencia entre patas de TXMD9 es de ~2.900 bps (TEA 6,82% por CER contra
-- 38,62% por TAMAR) — no son dos formas de decir lo mismo, son dos números.
-- Hasta hoy la vista mostraba el MISMO valor en las dos tablas.
--
-- ⚠️ **ESCALA: fracciones, no porcentajes.** 1816 devuelve 0.0973 para "9,73%",
-- igual que nuestro `market_snapshot.tea`. Se guarda TAL CUAL — convertir acá
-- crearía dos escalas conviviendo en la misma app, que es la clase de bug que
-- suma bien en cada lado y da distinto al comparar.
--
-- `fecha_operacion` es la que ECHA 1816, no la que se pidió: si un feriado hace
-- retroceder la búsqueda, la vista tiene que poder decir de qué día es el número.
CREATE TABLE IF NOT EXISTS mercado.tamar_1816 (
    ticker          text NOT NULL,   -- NUESTRO ticker (TXMD9) → mercado.curvas.ticker
    pata            text NOT NULL,   -- el AJUSTE de esta pata: tamar | cer | fija | dolar_linked
    ticker_1816     text NOT NULL,   -- la grafía exacta que se pidió ('TXMD9 @TAMAR')
    tea             double precision,
    tna             double precision,
    spread          double precision,   -- EL MARGEN sobre la TAMAR (0.0973 = 9,73%)
    precio_clean    double precision,
    duration        double precision,
    paridad         double precision,
    fecha_operacion date,               -- la rueda a la que corresponden los valores
    actualizado_en  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, pata)
);
-- Sin índice extra: son ~23 filas y el Seq Scan es óptimo (misma razón que las
-- tablas chicas de Tesorería). La PK ya cubre el join por ticker.

-- mercado.especies — LAS PATAS de cada bono (rediseño 2026-08-15, paso 8).
--
-- Un bono es UNO (AL30: un emisor, un cuadro de flujos) pero COTIZA en varias
-- especies: `AL30` en pesos, `AL30D` en dólar MEP, `AL30C` en cable, cada una en
-- plazo 24hs y CI. `mercado.curvas` tiene UN solo casillero (`instrumento`), así
-- que la especie quedaba congelada en el alta: no se podía ofrecer "ver el mismo
-- bono en pesos o en MEP", y si el alta eligió la pata equivocada el precio que
-- se muestra es de otra escala (medido: le pasa a CO32).
--
-- Medido en Primary (`manager.pyrofex_instruments`, 9.719 símbolos): 17 bonos del
-- master tienen las 3 especies y 183 tienen una sola. O sea que hoy es 1:1 para
-- casi todos — pero el modelo 1:N es el correcto y es el que deja de perder datos.
--
-- `ticker_especie` (AL30D) NO es basura a limpiar: es la identidad de lo que el
-- cliente TIENE y COBRA, y es la clave con la que ya viven `portafolio.tenencia`
-- y `operaciones.acreencias` (9.726 filas medidas). Por eso la tabla guarda las
-- dos: `ticker` (AL30) une con la curva, `ticker_especie` (AL30D) une con la
-- posición.
-- mercado.emisores — EL EMISOR Y SU INDUSTRIA (2026-08-16).
--
-- La industria es un atributo del EMISOR, no del bono. Guardarla por bono —que es
-- lo que hacía `mercado.curvas.sector`— significa escribir el mismo dato N veces
-- y esperar que nadie lo escriba distinto. Medido: **8 de 51 emisores tienen HOY
-- sectores que se contradicen entre sus propios bonos**; Pampa Energía tiene tres
-- valores ('', 'energia' y 'otros') repartidos en sus 4 bonos. Ninguno está "mal":
-- cada fila suma bien por separado, y por eso agrupar por industria da distinto
-- según de dónde se lea. Con el dato una sola vez por emisor eso es imposible.
--
-- El `emisor` es la PK de TEXTO y no un id, a propósito: es la clave con la que ya
-- joinean `mercado.curvas.emisor` y `portafolio.assets.EMISOR`, y meter un id
-- obligaría a resolverlo en cada uno de los cuatro escritores que hoy tocan ese
-- campo. El precio de esa decisión es que `'YPF '` y `'YPF'` serían dos emisores,
-- así que el índice único va sobre la forma NORMALIZADA y el service normaliza al
-- escribir. Lo que hace viable la clave de texto es que 1816 ya estandarizó el
-- nombre (`jobs/ficha_1816.py`, 140/140 corporativos cubiertos).
--
-- `industria` NULL = SIN CLASIFICAR, y es un estado válido y visible. No se
-- reparte a dedo ni se colapsa por mayoría: el bucket tiene que verse.
CREATE TABLE IF NOT EXISTS mercado.emisores (
    emisor      text PRIMARY KEY,
    industria   text,                  -- NULL = sin clasificar (visible, no se adivina)
    activo      boolean DEFAULT true,
    obs         text,
    creado_at   timestamptz DEFAULT now(),
    editado_por text,
    editado_at  timestamptz
);
-- La red contra `'YPF '` vs `'YPF'`: dos grafías del mismo emisor no pueden
-- convivir. Un índice funcional y no un CHECK porque el normalizado tiene que ser
-- consultable, no solo rechazable.
CREATE UNIQUE INDEX IF NOT EXISTS ux_emisores_norm
    ON mercado.emisores (upper(btrim(emisor)));
CREATE INDEX IF NOT EXISTS ix_emisores_industria ON mercado.emisores (industria);

-- Catálogo controlado de industrias (mismo patrón que `mercado.rubros` para los
-- CEDEARs): la industria NO se escribe libre, se elige de acá o se crea explícito.
-- Es lo que evita que 'Energia', 'energía' y 'ENERGIA' terminen siendo tres.
CREATE TABLE IF NOT EXISTS mercado.industrias (
    industria text PRIMARY KEY,
    activo    boolean DEFAULT true
);

CREATE TABLE IF NOT EXISTS mercado.especies (
    simbolo        text PRIMARY KEY,       -- MERV - XMEV - AL30D - 24hs (clave de Primary)
    ticker         text NOT NULL,          -- AL30   → mercado.curvas.ticker
    ticker_especie text NOT NULL,          -- AL30D  → portafolio.tenencia / acreencias
    especie        text,                   -- pesos | mep | cable
    moneda         text,                   -- ARS | USD
    plazo          text,                   -- 24hs | CI
    es_default     boolean DEFAULT false,  -- la que dibuja la curva hoy
    activa         boolean DEFAULT true,
    validado       boolean,                -- existe en Primary (jobs/validar_instrumentos)
    validado_at    timestamptz,
    actualizado_at timestamptz
);
-- ¿El símbolo existe en el catálogo real de Primary? La marca la refresca
-- `jobs/validar_instrumentos` con la MISMA fuente que usa el filtro del WS
-- (`core/instrumentos_validos`), para que no puedan contradecirse. `validado_at`
-- importa tanto como el booleano: un `true` sin fecha no se distingue de uno viejo.
ALTER TABLE mercado.especies ADD COLUMN IF NOT EXISTS validado boolean;
ALTER TABLE mercado.especies ADD COLUMN IF NOT EXISTS validado_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_especies_ticker  ON mercado.especies(ticker);
CREATE INDEX IF NOT EXISTS ix_especies_te      ON mercado.especies(ticker_especie);
CREATE INDEX IF NOT EXISTS ix_especies_default ON mercado.especies(ticker) WHERE es_default;

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
-- manager.tokens_externos — EL TOKEN COMPARTIDO de un proveedor externo
-- (2026-08-17, a raíz del «auth HTTP 429» de 1816).
--
-- El plan de 1816 tiene DOS límites que no son créditos, y el que nos estaba
-- rompiendo es el segundo:
--
--     Créditos diarios      3.863 / 100.000     ← sobra
--     Máx. peticiones/seg   1
--     **Máx. tokens por día   50**              ← ESTE
--
-- El token dura **24 h** (`expiresIn` 86400), así que UNO alcanzaría para todo el
-- día. Pero el cliente lo guardaba en un dict de módulo, o sea **en memoria de
-- cada proceso**: `jobs/tamar_1816` corre 15 veces por día, más el resto de los
-- jobs, más `api.service` (que pide uno nuevo en CADA restart, o sea en cada
-- deploy), más cada corrida manual. Cada uno quemaba un token de los 50.
--
-- Y encima el backoff los multiplicaba: un `_auth` que falla reintenta 5 veces, y
-- **reintentar contra una CUOTA consume justo el recurso que se acabó**. Ahí está
-- la trampa conceptual: el backoff es la respuesta correcta a un rate limit
-- (transitorio) y la peor posible a una cuota diaria (no lo es).
--
-- Con esta tabla hay UN token para todos los procesos: se pide una vez, se
-- persiste, y los demás lo adoptan. De ~16-30 logins/día a 1-2.
--
-- `llamadas_at` implementa el OTRO límite —1 petición por segundo— que el throttle
-- en memoria no podía garantizar: coordinaba dentro de un proceso y la API y los
-- jobs son procesos distintos que no se ven entre sí.
--
-- `logins_dia`/`dia` existen para que el presupuesto sea VISIBLE. Un límite que no
-- se puede mirar se descubre siempre de la misma forma: cuando ya se agotó.
CREATE TABLE IF NOT EXISTS manager.tokens_externos (
    proveedor    text PRIMARY KEY,          -- '1816'
    token        text,
    expira_at    timestamptz,
    obtenido_at  timestamptz,
    obtenido_por text,                      -- qué proceso lo pidió (para auditar)
    dia          date,                      -- día del contador (ART)
    logins_dia   int NOT NULL DEFAULT 0,    -- cuántos van de los 50
    llamadas_at  timestamptz,               -- última petición, para el 1 req/s global
    -- El valor ANTERIOR de `llamadas_at`, y no es redundante: en un
    -- `ON CONFLICT DO UPDATE ... RETURNING`, Postgres devuelve la fila **nueva**,
    -- así que restarle `now()` a `llamadas_at` daría siempre 0. Guardar el previo
    -- en el mismo UPDATE es lo que deja medir el intervalo en UNA sola operación
    -- atómica — que es justo lo que hace falta para que dos procesos no manden dos
    -- peticiones en el mismo segundo.
    llamadas_prev_at timestamptz
);

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

-- ─────────────────────────────────────────────────────────────────────────────
-- FINANCIAMIENTO → CALCULADORA DE DESCUENTO (panel 4 de la tab FINANCIAMIENTO).
-- Ver api/services/financiamiento_calc.py para las fórmulas.
--
-- La calculadora NO persiste NADA: es un simulador que corre cualquiera que
-- entre a la vista y se lleva puesto al salir. Lo único que vive acá son los
-- PARÁMETROS que la planilla Excel tenía hardcodeados a un costado y que la
-- mesa carga desde la tab DATOS.
--
-- ⚠️ UNIDADES: todos los porcentajes se guardan como PORCENTAJE (5.0 = 5 %,
-- 0.06 = 0,06 %), NO como fracción — es lo que el usuario tipea, así que mirar
-- la tabla en SQL da el mismo número que la pantalla. La división por 100 se
-- hace UNA sola vez, en el service.
-- ─────────────────────────────────────────────────────────────────────────────

-- Catálogo de SGRs. El NOMBRE es la clave porque es lo que el usuario elige
-- textualmente en el selector de la calculadora. Dos costos por fila: el aval
-- de un CHEQUE y el de un PAGARÉ no valen lo mismo.
CREATE TABLE IF NOT EXISTS operaciones.financiamiento_avales (
    nombre          text PRIMARY KEY,
    costo_cheque    numeric,                -- % anual (5.00 = 5 %); null = no ofrece
    costo_pagare    numeric,                -- % anual
    nota            text,                   -- 'más 0,4 directo' — INFORMATIVA, no entra al cálculo
    orden           int NOT NULL DEFAULT 0, -- orden de la tabla en pantalla
    actualizado_por text,
    actualizado_at  timestamptz
);

-- Arancel de ACA Valores y derecho de mercado. Fila ÚNICA (id = 1): no son por
-- cliente ni por operación, son la tarifa vigente. El CHECK es lo que impide que
-- alguien inserte una segunda fila y deje la calculadora eligiendo al azar.
CREATE TABLE IF NOT EXISTS operaciones.financiamiento_aranceles (
    id              int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    arancel_aca     numeric,                -- % anual sobre el NOMINAL, prorrateado por días
    derecho_mercado numeric,                -- % sobre el monto DESCONTADO, sin prorratear
    actualizado_por text,
    actualizado_at  timestamptz
);

CREATE TABLE IF NOT EXISTS operaciones.financiamiento_datos_audit (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     timestamptz,
    actor  text,
    action text,                            -- set_aval / del_aval / set_aranceles
    target text,                            -- nombre de la SGR, o '1' para los aranceles
    data   jsonb
);
CREATE INDEX IF NOT EXISTS ix_financiamiento_datos_audit_ts
    ON operaciones.financiamiento_datos_audit (ts DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- ACA — RESUMEN EJECUTIVO DE INVERSIONES (vista /aca, docs/ACA.md)
-- ─────────────────────────────────────────────────────────────────────────────
-- La cartera propia de ACA contada para los gerentes. NO es una vista live: es
-- una FOTO MENSUAL que la mesa carga a mano, como la planilla que reemplaza. El
-- criterio de diseño es "Excel con las fórmulas ya puestas": lo que se puede
-- derivar se deriva SIEMPRE server-side (montos, ponderaciones, share, métricas,
-- acumulados) y lo único que se tipea son los INPUTS que ninguna fuente tiene
-- (precio de corte del mes, VN, MEP/A3500 del informe, rendimientos externos).
--
-- Lo que NO se duplica: la ficha del título (emisor, calificación, clase de
-- activo, vencimiento, ticker) NO se copia acá. Vive en `portafolio.assets` —
-- el mismo catálogo que edita Manager → Títulos — y se resuelve por `unidad` en
-- cada lectura. Copiarla habría creado una segunda verdad que se desincroniza
-- sola: el rebautizo de especies de Aunesa (ver la regla `herencia` de
-- jobs/assets_autofill) ya demostró lo caro que sale eso.
--
-- Permisos: LECTURA = módulo `aca` (rol `empleado_aca`) ∪ admin ∪ escritores.
--           ESCRITURA = allowlist de Mesa de Dinero (operaciones.mesa_dinero_escritores)
--           + admin — decisión del user: la mesa maneja la cuenta, no se crea
--           una segunda lista que mantener sincronizada a mano.
CREATE SCHEMA IF NOT EXISTS aca;

-- Un PERÍODO = una foto mensual. `periodo` es 'YYYY-MM' (ordena lexicográfico).
CREATE TABLE IF NOT EXISTS aca.periodos (
    periodo         text PRIMARY KEY,       -- 'YYYY-MM'
    fecha_informe   date NOT NULL,          -- "Informe al 31/07/2026"
    mep             numeric,                -- Valor MEP del informe (manual)
    a3500           numeric,                -- Valor A3500 del informe (manual)
    nota            text,
    creado_por      text,
    creado_at       timestamptz DEFAULT now(),
    actualizado_por text,
    actualizado_at  timestamptz
);

-- DETALLE DE ACTIVOS del período. Solo los INPUTS: la ficha se joinea con
-- portafolio.assets por `unidad` (PK de ese catálogo).
--   monto = vn × px / divisor(cartera)  — derivado, salvo que `monto` traiga
--   un override manual (columna `monto`, NULL = derivar).
CREATE TABLE IF NOT EXISTS aca.activos (
    periodo         text NOT NULL,
    unidad          text NOT NULL,          -- FK lógica → portafolio.assets.unidad
    vn              numeric,                -- valor nominal (manual)
    px              numeric,                -- precio de corte del mes (manual — el dato sensible)
    monto           numeric,                -- override manual; NULL = derivado de vn × px
    tasa            text,                   -- columna "Tasa" (texto libre / NO APLICA)
    obs             text,                   -- columna suelta de la derecha ("Amortizo")
    orden           integer DEFAULT 0,
    actualizado_por text,
    actualizado_at  timestamptz,
    PRIMARY KEY (periodo, unidad)
);
CREATE INDEX IF NOT EXISTS ix_aca_activos_periodo ON aca.activos (periodo);

-- REGLA DE MONEDA — de qué lado suma cada cosa en TOTAL DOLARIZADO / TOTAL PESOS.
-- Dos scopes, y el de `clase` GANA sobre el de `cartera`: las carteras HD/DL/ARS
-- se resuelven por cartera, y el FCI —que tiene fondos en las dos monedas— se
-- abre por `clase_activo` (MM USD y HD T1 son dólares; MM ARS, ARS T1 y RENTA
-- VARIABLE son pesos). Editable en Manager → ACA: el user pidió explícitamente
-- que la clasificación fuera dinámica, no hardcodeada.
-- Lo que no resuelve ninguna regla NO se reparte a dedo: cae en `sin_clasificar`
-- y la vista lo muestra, para que un activo nuevo no se cuele silenciosamente
-- en el lado equivocado.
CREATE TABLE IF NOT EXISTS aca.moneda_regla (
    scope           text NOT NULL,          -- 'cartera' | 'clase'
    clave           text NOT NULL,          -- 'HD' | 'DL' | 'ARS' … / 'MM USD' | 'HD T1' …
    moneda          text NOT NULL,          -- 'usd' | 'ars'
    actualizado_por text,
    actualizado_at  timestamptz,
    PRIMARY KEY (scope, clave)
);

-- Catálogo de EMISORES que las MÉTRICAS GENERALES muestran SIEMPRE, aunque el
-- mes cierre en cero (así se lee "no tenemos nada de YPF", que es información,
-- en vez de que la fila desaparezca). Un emisor que aparece en el período y NO
-- está acá igual se muestra, marcado `fuera_catalogo` — mismo criterio que la
-- grilla BANCOS de Tesorería: el catálogo agrega filas, nunca esconde plata.
CREATE TABLE IF NOT EXISTS aca.emisor_destacado (
    bloque text NOT NULL,                   -- 'hd' | 'dl' | 'privados'
    emisor text NOT NULL,
    orden  integer DEFAULT 0,
    PRIMARY KEY (bloque, emisor)
);

-- Ídem para las CLASES DE ACTIVO por cartera (bloques CARTERA FCI y CARTERA ARS
-- de las métricas).
CREATE TABLE IF NOT EXISTS aca.clase_destacada (
    cartera text NOT NULL,                  -- 'FCI' | 'ARS' | …
    clase   text NOT NULL,                  -- 'MM USD' | 'TAMAR' | …
    orden   integer DEFAULT 0,
    PRIMARY KEY (cartera, clase)
);

-- HISTÓRICO — catálogo de SERIES (las columnas de la planilla histórica y las
-- líneas de los gráficos "vs benchmarks").
-- ⚠️ TODO el rendimiento mensual se TIPEA (decisión del user 2026-08-19: "nada
-- de ACA tiene que ser automático"). `fuente` y `escala` son columnas
-- VESTIGIALES de la automatización que se dio de baja — el código ya no las lee
-- ni las escribe. NO se dropean (borrar código se revierte, borrar datos no)
-- pero se normalizan a 'manual' abajo para que la fila no siga afirmando algo
-- que el sistema ya no hace. Ver docs/ACA.md §5.
CREATE TABLE IF NOT EXISTS aca.series (
    codigo   text PRIMARY KEY,
    nombre   text NOT NULL,
    grupo    text,                          -- 'cartera' | 'benchmark' | 'externo'
    fuente   text NOT NULL DEFAULT 'manual',  -- VESTIGIAL: siempre 'manual'
    escala   numeric NOT NULL DEFAULT 100,  -- VESTIGIAL (ver comentario de arriba)
    graficos text[] NOT NULL DEFAULT '{}',  -- 'total_ars' | 'total_usd' | 'pesos'
    color    text,
    orden    integer DEFAULT 0,
    activo   boolean NOT NULL DEFAULT true
);

-- HISTÓRICO — valores mensuales. `acumulado` NO se persiste: se deriva en la
-- lectura encadenando (1 + acum_anterior) × (1 + mensual) − 1, que es la fórmula
-- que el user viene arrastrando en la planilla. Persistirlo sería guardar un
-- número que puede contradecir a sus propios insumos.
-- El período de esta tabla es INDEPENDIENTE de aca.periodos: la serie histórica
-- arranca mucho antes que el primer informe cargado.
CREATE TABLE IF NOT EXISTS aca.historico (
    periodo         text NOT NULL,          -- 'YYYY-MM'
    serie           text NOT NULL,          -- → aca.series.codigo
    monto           numeric,                -- monto de cierre (ARS o USD, según la serie)
    ingreso_retiro  numeric,                -- aportes/retiros del mes
    mensual         numeric,                -- rendimiento del mes, FRACCIÓN (0,0245 = 2,45%)
    actualizado_por text,
    actualizado_at  timestamptz,
    PRIMARY KEY (periodo, serie)
);
CREATE INDEX IF NOT EXISTS ix_aca_historico_serie ON aca.historico (serie, periodo);

-- Trazabilidad de TODA escritura (before/after), igual que mesa_dinero_audit.
CREATE TABLE IF NOT EXISTS aca.audit (
    id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts     timestamptz,
    actor  text,
    action text,
    target text,
    data   jsonb
);
CREATE INDEX IF NOT EXISTS ix_aca_audit_ts ON aca.audit (ts DESC);

-- ── Semillas de los catálogos ───────────────────────────────────────────────
-- SEMILLA, no estado forzado. Cada bloque corre SOLO si su tabla está vacía.
--
-- `ON CONFLICT DO NOTHING` alcanza para no duplicar, pero NO para no resucitar:
-- estos catálogos se editan desde Manager → ACA, y si alguien borra la regla de
-- 'MM USD' o desactiva una serie, un `apply_schema` posterior se la volvía a
-- meter. El schema deshaciendo en silencio una decisión del admin es peor que
-- la fila faltante — y en este caso movería plata de lado en el informe.
INSERT INTO aca.moneda_regla (scope, clave, moneda)
SELECT * FROM (VALUES
    ('cartera', 'HD',  'usd'),
    ('cartera', 'DL',  'usd'),
    ('cartera', 'ARS', 'ars'),
    ('clase',   'MM USD',         'usd'),
    ('clase',   'HD T1',          'usd'),
    ('clase',   'MM ARS',         'ars'),
    ('clase',   'ARS T1',         'ars'),
    ('clase',   'RENTA VARIABLE', 'ars')
) AS t(scope, clave, moneda)
WHERE NOT EXISTS (SELECT 1 FROM aca.moneda_regla)
ON CONFLICT (scope, clave) DO NOTHING;

-- Emisores que las MÉTRICAS muestran siempre. Salen del informe que hoy se arma
-- a mano; se editan en Manager → ACA. `privados` es una selección CURADA (ese
-- bloque muestra SOLO su catálogo), por eso repite emisores que ya están en HD/DL.
-- Si algún nombre no coincide exactamente con el `emisor` de portafolio.assets,
-- la fila del catálogo queda en cero y el emisor real aparece marcado
-- `fuera_catalogo` — visible y corregible desde el panel, nunca perdido.
INSERT INTO aca.emisor_destacado (bloque, emisor, orden)
SELECT * FROM (VALUES
    ('hd', 'TESORO', 1), ('hd', 'BCRA', 2), ('hd', 'BCO. MACRO', 3),
    ('hd', 'BCO. COMAFI', 4), ('hd', 'CREDICUOTAS', 5), ('hd', 'YPF', 6),
    ('hd', 'YPF LUZ', 7), ('hd', 'PROV. SANTA FE', 8), ('hd', 'ARCOR', 9),
    ('hd', 'IRSA', 10), ('hd', 'FRIGORIFICO Gral PICO S.A.', 11),
    ('dl', 'YPF', 1), ('dl', 'TECO', 2), ('dl', 'PAE', 3), ('dl', 'TESORO', 4),
    ('dl', 'CGC', 5), ('dl', 'GENNEIA', 6), ('dl', 'PETROLERA ACONC', 7),
    ('dl', 'VISTA', 8),
    ('privados', 'YPF', 1), ('privados', 'TECO', 2), ('privados', 'BCO. MACRO', 3),
    ('privados', 'PAE', 4), ('privados', 'BCO. COMAFI', 5), ('privados', 'CREDICUOTAS', 6)
) AS t(bloque, emisor, orden)
WHERE NOT EXISTS (SELECT 1 FROM aca.emisor_destacado)
ON CONFLICT (bloque, emisor) DO NOTHING;

INSERT INTO aca.clase_destacada (cartera, clase, orden)
SELECT * FROM (VALUES
    ('FCI', 'ARS T1', 1), ('FCI', 'MM ARS', 2), ('FCI', 'MM USD', 3),
    ('FCI', 'HD T1', 4),  ('FCI', 'RENTA VARIABLE', 5),
    ('ARS', 'CER', 1), ('ARS', 'DUAL', 2), ('ARS', 'FIJA', 3), ('ARS', 'TAMAR', 4)
) AS t(cartera, clase, orden)
WHERE NOT EXISTS (SELECT 1 FROM aca.clase_destacada)
ON CONFLICT (cartera, clase) DO NOTHING;

INSERT INTO aca.series (codigo, nombre, grupo, fuente, graficos, orden)
SELECT * FROM (VALUES
    ('total_ars',  'Cartera Total ACA en ARS', 'cartera',   'manual', '{total_ars}'::text[],       1),
    ('total_usd',  'Cartera Total ACA en USD', 'cartera',   'manual', '{total_usd}'::text[],       2),
    ('ars_aca',    'Cartera ARS ACA',          'cartera',   'manual', '{pesos}'::text[],           3),
    ('usd_aca',    'Cartera USD ACA',          'cartera',   'manual', '{}'::text[],                4),
    ('badlar',     'Badlar',                   'benchmark', 'manual', '{total_ars,pesos}'::text[], 5),
    ('inflacion',  'Inflacion',                'benchmark', 'manual', '{total_ars,pesos}'::text[], 6),
    ('a3500',      'A3500',                    'benchmark', 'manual', '{total_ars}'::text[],       7),
    ('dl_caspi',   'Cartera DL Caspi',         'externo',   'manual', '{}'::text[],                8),
    ('ars_caspi',  'Cartera ARS Caspi',        'externo',   'manual', '{}'::text[],                9)
) AS t(codigo, nombre, grupo, fuente, graficos, orden)
WHERE NOT EXISTS (SELECT 1 FROM aca.series)
ON CONFLICT (codigo) DO NOTHING;

-- El seed de arriba solo corre con la tabla VACÍA, así que en prod la fila del
-- A3500 sigue declarando su vieja fuente automática. El código ya no la lee (el
-- valor calculado desapareció de la planilla), pero dejar ese string ahí es una
-- afirmación falsa sobre lo que hace el sistema. Idempotente: la segunda vez no
-- toca nada.
UPDATE aca.series SET fuente = 'manual' WHERE fuente <> 'manual';

-- ── RBAC de la vista ACA ────────────────────────────────────────────────────
-- El módulo `aca` y el rol `empleado_aca` nacen en core/roles.py, pero
-- DEFAULT_MATRIX solo aplica cuando `manager.role_matrix` está VACÍA — y en prod
-- está poblada, así que el default no se propaga solo. Sin estas filas la vista
-- quedaría invisible hasta para el admin, y el rol nuevo ni siquiera aparecería
-- en el panel ROLES Y PERMISOS para poder asignarlo.
--
-- Las DOS semillas están guardadas contra la resurrección (mismo criterio que
-- los catálogos de arriba): la matriz se edita desde ROLES Y PERMISOS y un
-- apply_schema no puede devolverle a un rol un módulo que el admin le sacó.
--
-- ORDEN IMPORTANTE: primero los módulos base, después la fila `aca`. Al revés,
-- la fila `aca` haría existir a `empleado_aca` en la matriz y el bloque de
-- abajo se saltearía para siempre — el rol quedaría con la vista ACA y NADA más.

-- 1) `empleado_aca` = los mismos módulos que `sales` (es el mismo puesto). Se
--    COPIA de sales en vez de listarlos para que la semilla no quede contando
--    una historia vieja si mañana alguien ajusta sales. Solo si el rol todavía
--    no existe en la matriz.
--    `sales` NO recibe `aca` a propósito: sigue siendo el DEFAULT_ROLE (todo
--    email nuevo que pasa Cloudflare cae ahí), y darle la cartera propia de la
--    casa a un alta automática es exactamente lo que no queremos.
INSERT INTO manager.role_matrix (role, module)
SELECT 'empleado_aca', module FROM manager.role_matrix
WHERE role = 'sales'
  AND NOT EXISTS (SELECT 1 FROM manager.role_matrix WHERE role = 'empleado_aca')
ON CONFLICT (role, module) DO NOTHING;

-- 2) El módulo `aca` para quienes lo tienen que ver de entrada. Se siembra UNA
--    sola vez: si ya existe CUALQUIER fila del módulo, su distribución ya se
--    decidió desde el panel y el schema no vuelve a opinar.
INSERT INTO manager.role_matrix (role, module)
SELECT r, 'aca' FROM (VALUES ('empleado_aca'), ('admin')) AS t(r)
WHERE NOT EXISTS (SELECT 1 FROM manager.role_matrix WHERE module = 'aca')
ON CONFLICT (role, module) DO NOTHING;

-- =====================================================================
-- BANCOS — espejo de las APIs de Interbanking (ver docs/INTERBANKING.md)
-- =====================================================================
-- Datos de los BANCOS de ACA, leídos de Interbanking por jobs/interbanking_sync.
-- Todo entra por ese job: la vista NUNCA le pega a Interbanking en vivo, porque
-- el límite de 100 llamadas/minuto es del ABONADO y no del proceso — tres
-- usuarios refrescando la pantalla podrían agotar la cuota y romper el propio
-- job (y cualquier otro sistema de ACA que use esa cuota).
--
-- V1: el foco es CONCILIAR, así que la fuente única es la API de Extractos, que
-- trae el día (apertura/cierre/totales) Y su detalle de movimientos en la misma
-- respuesta. Saldos y Transferencias quedan para después, a propósito: el
-- extracto ya da el saldo diario y sumar la API de Saldos sería una segunda
-- verdad para el mismo número.
CREATE SCHEMA IF NOT EXISTS bancos;

-- Maestro de cuentas. Se descubre solo en cada corrida del job.
--
-- OJO — `account-type` y `currency` FILTRAN del lado de Interbanking, y la API
-- defaultea `currency` a ARS: pedir /accounts sin especificar moneda devuelve
-- SOLO las cuentas en pesos, sin avisar. El universo completo son 4 llamadas
-- (CC/CA × ARS/USD). Eso ya costó una lectura incompleta el 2026-08-14.
--
-- `id` propio (surrogate) en vez de la clave natural de 4 campos: si mañana
-- Interbanking renombra o repite algo, se toca una fila y no N tablas hijas.
CREATE TABLE IF NOT EXISTS bancos.cuentas (
    id              bigserial PRIMARY KEY,
    bank_number     text NOT NULL,          -- código BCRA de 3 dígitos
    bank_name       text,
    account_number  text NOT NULL,          -- el que va en el path de la API
    account_type    text NOT NULL,          -- CC | CA
    currency        text NOT NULL,          -- ARS | USD
    account_cbu     text,                   -- NUNCA se serializa al front
    account_cuit    text,                   -- NUNCA se serializa al front
    account_label   text,
    activa          boolean NOT NULL DEFAULT true,
    primera_vez     date,
    ultima_vez      date,
    raw             jsonb,                  -- respuesta cruda, ver nota abajo
    actualizado_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bank_number, account_number, account_type, currency)
);

-- El código de la cuenta en el MAYOR contable (Aunesa
-- `contabilidad/registrosContables` → `codigoCuenta`, ej. '101010100002').
--
-- Va ACÁ y no en una tabla de mapeo aparte porque es otra IDENTIDAD de la misma
-- cuenta bancaria, no una relación entre dos cosas: una fila = una cuenta real,
-- con el nombre que le pone Interbanking y el que le pone Contabilidad.
--
-- ⚠️ **Se carga a mano y no se puede deducir del nombre.** El mayor la llama
-- distinto que Interbanking, y de las ~20 cuentas bancarias solo un par traen el
-- número de cuenta en el nombre (`Banco Patagonia CC 304-100753595-000`); el
-- resto son `Banco de Valores ARS`, `Banco Patagonia ACDI`, `CVU AL2 ALICUOTA
-- GENERAL`. Adivinar por nombre mezclaría cuentas del mismo banco —Patagonia
-- tiene cuatro— y el error recién se vería en el cierre.
--
-- ⚠️ El UPSERT de `jobs/interbanking_sync` NO la toca (enumera columnas, igual
-- que con `origen`): el descubrimiento automático de cuentas no puede borrar un
-- dato que puso una persona.
ALTER TABLE bancos.cuentas ADD COLUMN IF NOT EXISTS codigo_contable text;
-- Parcial: muchas cuentas no tienen mayor asociado y NULL no puede colisionar,
-- pero dos cuentas con el MISMO código contable sí sería un error de carga.
CREATE UNIQUE INDEX IF NOT EXISTS ux_bancos_cuentas_codigo_contable
    ON bancos.cuentas (codigo_contable) WHERE codigo_contable IS NOT NULL;

-- El día del extracto: lo que el BANCO dice que pasó. Es la fila contra la que
-- se concilia.
--
-- `cierra` / `diferencia` se materializan en la ingesta (apertura + créditos −
-- débitos vs cierre). Es la primera pregunta de cualquier conciliación y no
-- puede depender de que alguien la calcule bien en la vista.
CREATE TABLE IF NOT EXISTS bancos.extracto_dia (
    cuenta_id          bigint NOT NULL REFERENCES bancos.cuentas(id),
    fecha              date NOT NULL,
    saldo_apertura     numeric,
    saldo_cierre       numeric,
    total_creditos     numeric,
    total_debitos      numeric,
    total_movimientos  integer,             -- lo que DICE el banco
    numero_extracto    text,
    cierra             boolean,             -- apertura + cr − de == cierre
    diferencia         numeric,
    sincronizado_at    timestamptz NOT NULL DEFAULT now(),
    raw                jsonb,
    PRIMARY KEY (cuenta_id, fecha)
);

-- El detalle. Un movimiento del banco.
--
-- ⚠️ NO HAY ID NATURAL. El YAML de Movimientos v1 declara un campo `id`, pero
-- medido contra producción (2026-08-14) NO viene — ni en v1 ni en v2. Así que la
-- PK es un hash determinístico de los campos identificatorios del movimiento
-- (ver `jobs/interbanking_sync._hash_mov`).
--
-- El hash incluye importe, tipo y código de operación a propósito, y no solo
-- (extracto, correlativo): si el banco corrige un movimiento, preferimos que
-- aparezca una fila NUEVA a que se pise la vieja en silencio. Duplicar es
-- visible; perder no. Y la duplicación se detecta sola: la ingesta compara la
-- cantidad de movimientos que guardó contra el `total_movimientos` que declara
-- el extracto de ese día, y lo reporta en `sync_log`.
--
-- `cuit_contraparte` / `denominacion_contraparte` vienen en ~3% de los casos
-- (medido): con esta API NO se puede saber de quién vino la plata en la mayoría
-- de los movimientos. Son datos personales de terceros — no se serializan al
-- front sin enmascarar y nunca van a un log.
CREATE TABLE IF NOT EXISTS bancos.movimientos (
    mov_hash                 text PRIMARY KEY,
    cuenta_id                bigint NOT NULL REFERENCES bancos.cuentas(id),
    fecha                    date NOT NULL,
    fecha_movimiento         timestamptz,
    fecha_valor              timestamptz,
    fecha_proceso            timestamptz,
    importe                  numeric NOT NULL,
    tipo                     text,          -- C = crédito | D = débito
    descripcion_banco        text,
    descripcion_ib           text,
    codigo_operacion_ib      text,
    codigo_operacion_banco   text,
    numero_extracto          text,
    correlativo              bigint,
    comprobante              bigint,
    sucursal                 text,
    cuit_contraparte         text,
    denominacion_contraparte text,
    raw                      jsonb,
    sincronizado_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_bancos_mov_cuenta_fecha
    ON bancos.movimientos (cuenta_id, fecha);

-- El OTRO lado de la conciliación: lo que el MAYOR contable dice que pasó.
-- Origen: Aunesa `GET contabilidad/registrosContables` (ver docs/ y
-- `scripts/diag_registros_contables.py`). Reemplaza al .xlsx que hoy se exporta
-- a mano de HYGIRUS y se sube en INTERBANKING → CONCILIAR; el archivo sigue
-- funcionando como origen alternativo del mismo formato interno.
--
-- Vive en el schema `bancos` y no en uno de contabilidad porque **solo guarda
-- cuentas BANCARIAS**: de los ~23.600 movimientos diarios del mayor (101 cuentas
-- contables) las bancarias son ~20 cuentas y un puñado de movimientos. El resto
-- —comitentes, IVA, aranceles, regularizadoras— no se guarda.
--
-- ⚠️ **`fecha_conciliacion`, NO `fecha_alta`.** Medido el 2026-08-20: el rango
-- `fechaDesde`/`fechaHasta` de la API filtra por la fecha de CONCILIACIÓN, y
-- pidiendo el 19/08 vuelven asientos con `fecha_alta` del 20/08 (se cargan al día
-- siguiente). Usar `fecha_alta` correría todo un día — y un día corrido acá no se
-- lee como un bug, se lee como una diferencia de conciliación.
--
-- ⚠️ **El día NO está cerrado: se REEMPLAZA entero en cada corrida.** Medido: el
-- 19/08 pasó de 1.840 a 1.847 asientos (23.405 → 23.600 movimientos) entre dos
-- consultas del mismo día siguiente. Siguen cargando asientos, y también anulan.
-- Por eso el refresco es `DELETE WHERE fecha_conciliacion = $1` + insert, y NO un
-- UPSERT: con upsert, un asiento anulado durante la rueda quedaría de fantasma y
-- el saldo del mayor saldría inflado sin que nada lo delate.
--
-- No hay tabla de saldos del mayor a propósito: el saldo de apertura sale del
-- cierre del día anterior que ya persiste `bancos.extracto_dia`, y el cierre del
-- mayor se calcula al vuelo (apertura + suma de estos movimientos). Guardarlo
-- sería una segunda verdad para el mismo número.
CREATE TABLE IF NOT EXISTS bancos.mayor_movimientos (
    movimiento_id       text PRIMARY KEY,      -- `movimientoID` de Aunesa
    asiento_id          text,
    asiento_numero      text,                  -- columna «Asiento» del mayor (negativa)
    fecha_conciliacion  date NOT NULL,
    fecha_alta          date,
    codigo_cuenta       text NOT NULL,         -- `codigoCuenta`, ej. '101010100002'
    cuenta_id           bigint NOT NULL REFERENCES bancos.cuentas(id),
    moneda              text,                  -- `codigoUnidad`, ej. 'ARS'
    -- `cantidad` FIRMADA (Debe − Haber) y EN LA MONEDA DE LA CUENTA.
    -- NO es `valuacion`: esa es `cantidad × factor` (el TC), o sea pesos, y en
    -- las cuentas en dólares no se puede comparar contra el extracto.
    importe             numeric NOT NULL,
    concepto            text,                  -- referencia del ASIENTO: la que agrupa `_grupo_mayor`
    comprobante         text,
    numero_operacion    text,
    referencia_mov      text,
    actualizado_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_bancos_mayor_fecha_cuenta
    ON bancos.mayor_movimientos (fecha_conciliacion, cuenta_id);

-- Cada corrida del refresco del mayor. Mismo motivo que `bancos.sync_log`: sin
-- esto no se puede contestar «¿se actualizó?», y una vista que muestra el mayor
-- de anteayer como si fuera el de hoy es peor que una vista vacía.
CREATE TABLE IF NOT EXISTS bancos.mayor_sync_log (
    id                 bigserial PRIMARY KEY,
    corrida_at         timestamptz NOT NULL DEFAULT now(),
    fecha_conciliacion date NOT NULL,
    asientos_api       integer,   -- lo que devolvió la API (todas las cuentas)
    movimientos_api    integer,
    movimientos_banco  integer,   -- los que quedaron guardados (solo mapeadas)
    cuentas_sin_mapear integer,   -- códigos distintos que vinieron sin `codigo_contable`
    segundos           numeric,
    ok                 boolean NOT NULL DEFAULT true,
    error              text
);

-- Cada corrida del job, cuenta por cuenta. Sin esto no hay forma de saber que
-- un día NO se sincronizó — que es exactamente el agujero del incidente del
-- backfill de tenencias del 2026-08-07: no faltaban datos, faltaba la pregunta
-- "¿corrió cuando debía?".
CREATE TABLE IF NOT EXISTS bancos.sync_log (
    id            bigserial PRIMARY KEY,
    corrida_at    timestamptz NOT NULL DEFAULT now(),
    cuenta_id     bigint REFERENCES bancos.cuentas(id),
    fecha_desde   date,
    fecha_hasta   date,
    paginas       integer,
    dias          integer,
    movimientos   integer,
    incoherentes  integer,   -- días donde lo guardado != total_movimientos del banco
    control_code  text,      -- el que devuelve Interbanking, para reclamarles
    ok            boolean NOT NULL,
    error         text
);

CREATE INDEX IF NOT EXISTS ix_bancos_sync_corrida
    ON bancos.sync_log (corrida_at DESC);

-- Auditoría de LECTURA. En cualquier otra vista sería opcional; acá son los
-- saldos bancarios de la casa. Es lo que permite responder "quién miró el banco
-- X y cuándo" sin depender de la memoria de nadie.
CREATE TABLE IF NOT EXISTS bancos.audit_lecturas (
    id           bigserial PRIMARY KEY,
    ts           timestamptz NOT NULL DEFAULT now(),
    email        text NOT NULL,
    cuenta_id    bigint,
    fecha_desde  date,
    fecha_hasta  date,
    filas        integer
);

CREATE INDEX IF NOT EXISTS ix_bancos_audit_ts
    ON bancos.audit_lecturas (ts DESC);

-- SALDOS que informa el banco. Tabla APARTE de `extracto_dia` a propósito: son
-- dos preguntas distintas y responderlas con la misma fila las confunde.
--
-- `extracto_dia` contesta "qué PASÓ ese día" y **solo existe si hubo
-- movimientos** — el extracto no devuelve los días quietos. `saldos` contesta
-- "cuánto HAY", y viene haya habido movimientos o no. Esa es toda la razón de
-- sumar esta API: con la ventana corta que usa el back office (último día hábil
-- + hoy), cualquier cuenta que no se movió en esos dos días no tenía NINGUNA
-- fila y el consolidado la mostraba con «—». Cuanto más corta la ventana, más
-- grande el agujero.
--
-- ⚠️ NO es una segunda verdad para el mismo número. El saldo de cierre del
-- extracto y el saldo del día son dos cosas que el banco informa por separado;
-- si difieren, eso es un HALLAZGO de conciliación y por eso se guardan aparte
-- en vez de pisarse. El consolidado usa el extracto cuando lo tiene y cae a esta
-- tabla cuando no, y dice de dónde salió cada número (`fuente`).
--
-- ⚠️ NADA DE ESTO SE MEZCLA CON TESORERÍA (`operaciones.tesoreria_*`). Son
-- objetos distintos y sin clave en común: Tesorería trabaja sobre la CUENTA
-- OPERATIVA de Aunesa (una denominación de texto, tipo 'BANCO MARIVA TERCEROS',
-- que es una imputación interna del agente) y esto es la CUENTA BANCARIA real
-- (banco BCRA + número + CBU). No se joinean ni se suman.
CREATE TABLE IF NOT EXISTS bancos.saldos (
    cuenta_id           bigint NOT NULL REFERENCES bancos.cuentas(id),
    fecha               date NOT NULL,
    -- Del bloque `historical_balances[]`: una fila por día.
    saldo_dia           numeric,
    creditos_dia        numeric,
    debitos_dia         numeric,
    -- Del bloque `balances`: es la foto de HOY, no una serie. Solo se completa
    -- en la fila del `row_date` que declara la respuesta; en los días anteriores
    -- queda NULL, que es lo correcto — el banco no informa el proyectado de ayer.
    saldo_contable      numeric,
    saldo_operativo     numeric,
    saldo_operativo_ini numeric,
    proyectado_24hs     numeric,
    proyectado_48hs     numeric,
    es_foto             boolean NOT NULL DEFAULT false,  -- true = fila del row_date
    raw                 jsonb,
    sincronizado_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cuenta_id, fecha)
);

CREATE INDEX IF NOT EXISTS ix_bancos_saldos_fecha
    ON bancos.saldos (fecha DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- GASTOS BANCARIOS — qué movimiento es un gasto que cobró el banco
--
-- El back office necesita SEPARAR, dentro de los movimientos del día, lo que el
-- banco se cobró (comisiones, mantenimiento, sellados). Ya está implícito en el
-- saldo: esto no cambia ningún número, lo DISTINGUE.
--
-- ⚠️ Son DOS capas y la diferencia entre ellas es la idea central del modelo:
--
--   `gastos_reglas`     = el CONOCIMIENTO. "Todo lo que tenga el código 830 es
--                         gasto". Vale para siempre y para todos los bancos.
--                         Es lo que hace que mañana no haya que marcar nada.
--
--   `gastos_overrides`  = el PARCHE. "ESTE movimiento, hoy, es (o no es) gasto".
--                         Sirve para lo raro, lo que pasa una vez. **Gana
--                         SIEMPRE sobre la regla** — mismo criterio que
--                         `tesoreria_exclusiones` y que la marca manual de
--                         `assets.vigencia_motivo`: si una persona lo decidió, la
--                         máquina no se lo da vuelta.
--
-- Si el equipo se encuentra marcando lo MISMO todos los días, eso no es un
-- override: es una regla que falta. La vista lo hace visible mostrando de dónde
-- salió cada marca (`origen`: regla / manual).
--
-- La clasificación NO se materializa en `bancos.movimientos`: se resuelve en la
-- LECTURA. Así, cambiar una regla se refleja al instante en todos los días que
-- haya en la base, sin recomputar nada — y el número que muestra la vista no
-- puede contradecir a la regla que dice el catálogo. Es barato: la base retiene
-- 3 fechas (~500 movimientos), no un histórico.
CREATE TABLE IF NOT EXISTS bancos.gastos_reglas (
    id          bigserial PRIMARY KEY,
    -- Sobre QUÉ campo del movimiento se aplica. Los códigos son la forma
    -- precisa; el texto de la descripción es con la que se arranca cuando
    -- todavía no se sabe qué códigos usa cada banco.
    campo       text NOT NULL,   -- codigo_ib | codigo_banco | descripcion_banco | descripcion_ib
    operador    text NOT NULL,   -- igual | contiene
    valor       text NOT NULL,
    nota        text,            -- para qué es, en castellano
    activa      boolean NOT NULL DEFAULT true,
    creado_por  text,
    creado_at   timestamptz NOT NULL DEFAULT now(),
    -- Dos reglas idénticas no aportan nada y ensucian el catálogo.
    UNIQUE (campo, operador, valor)
);

-- La marca por movimiento. `ON DELETE CASCADE` a propósito: cuando la retención
-- de 3 fechas borra el movimiento, su override se va con él. Un override sin
-- movimiento no significa nada y acumularlos sería basura creciendo sola.
CREATE TABLE IF NOT EXISTS bancos.gastos_overrides (
    mov_hash  text PRIMARY KEY REFERENCES bancos.movimientos(mov_hash) ON DELETE CASCADE,
    es_gasto  boolean NOT NULL,
    por       text NOT NULL,
    at        timestamptz NOT NULL DEFAULT now()
);

-- Trazabilidad de TODA escritura (reglas y marcas). Mismo criterio que
-- `tesoreria_audit` / `senebis_audit`: son decisiones sobre plata de la casa y
-- tiene que poder responderse quién y cuándo sin depender de la memoria de nadie.
CREATE TABLE IF NOT EXISTS bancos.gastos_audit (
    id        bigserial PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    email     text NOT NULL,
    accion    text NOT NULL,   -- regla_alta | regla_baja | marca
    detalle   jsonb
);

CREATE INDEX IF NOT EXISTS ix_bancos_gastos_audit_ts
    ON bancos.gastos_audit (ts DESC);

-- Quién tiene la vista abierta. El poll de 60s ES el heartbeat (no hay un
-- endpoint aparte que golpear), igual que en Tesorería.
-- IGNORAR un movimiento. Mismo modelo que `operaciones.tesoreria_exclusiones`:
-- **solo overrides** — si no hay fila acá, el movimiento cuenta.
--
-- Ignorar NO borra nada y NO puede cambiar un saldo: los saldos los informa el
-- banco en `extracto_dia` y no se suman desde el detalle. Lo que saca es al
-- movimiento de los GASTOS BANCARIOS y de su balde del desglose. La fila sigue
-- viéndose en la lista, tachada y con el motivo — esconderla haría que el
-- detalle no explique al total.
--
-- Para qué: el banco manda el mismo movimiento dos veces, o una fila que es un
-- error suyo. Es el mismo problema que en Tesorería obliga a que los movimientos
-- de Aunesa sin hora arranquen destildados.
--
-- `ON DELETE CASCADE`: cuando la retención de 3 fechas borra el movimiento, su
-- exclusión se va con él. Una exclusión sin movimiento no significa nada.
-- CUENTAS Y BANCOS MANUALES
--
-- Interbanking no tiene todos los bancos de la casa, y el que falta igual mueve
-- plata. `origen` separa las dos poblaciones EN LA MISMA TABLA en vez de crear
-- una segunda: son cuentas bancarias, se muestran juntas y se leen igual — una
-- tabla aparte obligaría a unir dos fuentes en cada lectura y a duplicar cada
-- cambio de acá en adelante.
--
-- ⚠️ **El job NO puede pisarlas**, y no hace falta ninguna defensa nueva: el job
-- recorre lo que le devuelve Interbanking y una cuenta manual, por definición, no
-- está en esa lista. Lo único que se agregó es que el upsert **no toca `origen`**
-- (ver `sincronizar_cuentas`): si algún día Interbanking empieza a informar una
-- cuenta que se había cargado a mano, se completa con datos reales pero sigue
-- marcada como manual, que es la información que hace falta para decidir qué
-- hacer con sus movimientos manuales. Nunca al revés.
ALTER TABLE bancos.cuentas ADD COLUMN IF NOT EXISTS origen text NOT NULL DEFAULT 'interbanking';
ALTER TABLE bancos.cuentas ADD COLUMN IF NOT EXISTS creado_por text;

-- ─────────────────────────────────────────────────────────────────────────────
-- MOVIMIENTOS MANUALES — lo que el banco no informa
--
-- Mismo modelo que los REGISTROS MANUALES de Tesorería: una fuente de plata que
-- no viene de ninguna API. **Siempre impactan el saldo al cierre** del día que
-- se les cargue, se sumen a un extracto real o sean lo único que tiene esa
-- cuenta (que es el caso de un banco manual, donde el saldo ES la suma de estos
-- movimientos).
--
-- `tipo` C/D en vez de un importe con signo, igual que `bancos.movimientos`: así
-- un movimiento manual se dibuja en la misma tabla que los del banco, con las
-- mismas columnas, y no hay dos convenciones de signo conviviendo.
--
-- ⚠️ **NO los purga la retención de 3 fechas.** Los movimientos del banco se
-- vuelven a pedir cuando hagan falta; esto lo tipeó una persona y no se puede
-- reconstruir. Es exactamente la razón por la que la purga solo toca las tablas
-- que el job escribe.
CREATE TABLE IF NOT EXISTS bancos.movimientos_manuales (
    id           bigserial PRIMARY KEY,
    cuenta_id    bigint NOT NULL REFERENCES bancos.cuentas(id) ON DELETE CASCADE,
    fecha        date NOT NULL,
    descripcion  text NOT NULL,
    importe      numeric NOT NULL,
    tipo         text NOT NULL,          -- C (suma) | D (resta)
    creado_por   text,
    creado_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_bancos_manuales_fecha
    ON bancos.movimientos_manuales (fecha, cuenta_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- DESGLOSE de los gastos — en qué columna cae cada gasto
--
-- El total de gastos no alcanza: el back office necesita ver cuánto es IVA,
-- cuánto percepción, cuánto comisión y cuánto impuesto. Eso NO cambia ningún
-- número — parte el que ya está.
--
-- ⚠️ Esto era una CONSTANTE en Python hasta el 2026-08-18, con el argumento de
-- que "qué columnas tiene una tabla no se cambia todos los días". Duró un día:
-- apareció un impuesto que ningún balde agarraba y la única forma de sumarlo era
-- tocar código y deployar. El que sabe que un banco escribe `LEY25413DB` donde
-- otro dice `IMP.DB/CR BANCARIOS P/DEB` es el EQUIPO, no el que programa, así
-- que el catálogo baja a la base y se edita desde la vista.
--
--   `gastos_baldes`          = la columna (etiqueta + dónde se muestra + orden).
--   `gastos_balde_matchers`  = las GRAFÍAS con que llega ese concepto. Son
--                              varias porque cada banco lo escribe distinto.
--
-- ⚠️ El CAMPO va en el MATCHER y no en el balde: `COM.TRANSF` llega como
-- CONCEPTO en unos bancos y escrito en la DESCRIPCIÓN en otros, y un balde tiene
-- que poder mirar los dos lados.
--
-- ⚠️ `orden` NO es cosmético: es lo único que decide los EMPATES. Gana el primer
-- balde que matchea, así que un movimiento con concepto IVA y descripción que
-- menciona SELLOS cuenta una sola vez y siempre del mismo lado. Si sumara en los
-- dos, el desglose daría más que el total.
--
-- La semilla la carga `bancos.py::_sembrar_desglose` la primera vez que la tabla
-- está vacía — y SOLO si está vacía, para que un balde borrado a propósito no
-- vuelva solo.
CREATE TABLE IF NOT EXISTS bancos.gastos_baldes (
    clave       text PRIMARY KEY,      -- derivada de la etiqueta; es la que viaja en el JSON
    etiqueta    text NOT NULL,         -- lo que se lee en la columna
    grupo       text NOT NULL DEFAULT 'otros',  -- concepto (columna propia) | otros (van a OTROS IMP)
    orden       int  NOT NULL DEFAULT 100,
    activo      boolean NOT NULL DEFAULT true,
    creado_por  text,
    creado_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bancos.gastos_balde_matchers (
    id          bigserial PRIMARY KEY,
    balde       text NOT NULL REFERENCES bancos.gastos_baldes(clave) ON DELETE CASCADE,
    campo       text NOT NULL,   -- codigo_ib | codigo_banco | descripcion_banco | descripcion_ib
    operador    text NOT NULL,   -- igual | contiene
    valor       text NOT NULL,
    creado_por  text,
    creado_at   timestamptz NOT NULL DEFAULT now(),
    -- Dos matchers idénticos en el mismo balde no aportan nada.
    UNIQUE (balde, campo, operador, valor)
);

CREATE TABLE IF NOT EXISTS bancos.movimientos_ignorados (
    mov_hash  text PRIMARY KEY REFERENCES bancos.movimientos(mov_hash) ON DELETE CASCADE,
    motivo    text,
    por       text NOT NULL,
    at        timestamptz NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- MOVIMIENTOS A CONCILIAR — lo que el back office confirmó que hay que arreglar
--
-- CONCILIAR encuentra los movimientos que explican la diferencia entre nuestro
-- saldo y el del mayor. Encontrarlos no alcanza: **el arreglo se hace en OTRO
-- sistema (HYGIRUS) y en otro momento**, así que si no queda anotado, la próxima
-- conciliación vuelve a encontrar lo mismo y nadie sabe si ya se corrigió.
--
-- `accion` es lo ÚNICO que hay que leer para saber qué hacer:
--   · `falta_en_el_mayor` — el banco lo tiene y el mayor no: hay que CARGARLO.
--   · `sobra_en_el_mayor` — el mayor lo tiene y el banco no: hay que SACARLO.
-- Se guarda la acción y no el diagnóstico ("hay una diferencia de X") porque el
-- que lo abre mañana necesita saber qué toca hacer, no qué se detectó.
--
-- La DESCRIPCIÓN se guarda **tal como viene del lado que corresponde**: si el
-- movimiento está de más en el mayor, se guarda como lo escribe HYGIRUS
-- (`[Op. 1131723] Extracción…`); si falta, como lo escribe el banco
-- (`CREDITO POR DATANET`). Es lo que lo hace encontrable en el sistema donde hay
-- que ir a arreglarlo — traducirlo sería obligarlo a buscar a ciegas.
--
-- No se purga con la retención de 3 fechas: un pendiente puede tardar días en
-- resolverse y lo escribió una persona.
CREATE TABLE IF NOT EXISTS bancos.conciliacion_pendientes (
    id           bigserial PRIMARY KEY,
    cuenta_id    bigint NOT NULL REFERENCES bancos.cuentas(id) ON DELETE CASCADE,
    fecha        date NOT NULL,           -- el día que se estaba conciliando
    accion       text NOT NULL,           -- falta_en_el_mayor | sobra_en_el_mayor
    descripcion  text NOT NULL,           -- tal como viene de SU lado
    importe      numeric NOT NULL,        -- firmado
    diferencia   numeric,                 -- la diferencia total de esa conciliación
    nota         text,
    confirmado_por text,
    confirmado_at  timestamptz NOT NULL DEFAULT now(),
    -- Se marca resuelto cuando el arreglo ya se hizo en el otro sistema. La fila
    -- NO se borra: es la traza de qué se corrigió y quién lo corrigió.
    resuelto     boolean NOT NULL DEFAULT false,
    resuelto_por text,
    resuelto_at  timestamptz
);

-- ⚠️ IDENTIDAD DEL MOVIMIENTO, y la razón por la que la clave única cambió.
--
-- La versión original era `UNIQUE (cuenta_id, fecha, accion, descripcion,
-- importe)`, o sea: la unicidad definida sobre el TEXTO. Dos movimientos
-- DISTINTOS que se escriben igual eran, para esta tabla, el mismo — y el caso
-- se dio (2026-08-19, Banco Valores): dos `N/C - CRED REVERSO PASE E` de
-- $50.000,00 el mismo día. El segundo cayó en el `ON CONFLICT DO UPDATE` y pisó
-- al primero, así que quedó UN pendiente de 50.000 para una diferencia de
-- 100.000,55. Peor que perder una fila: la anotación miente por la mitad y la
-- conciliación de mañana no cierra igual.
--
-- `mov_ref` es la identidad REAL: `mov_hash` del lado del banco, `mayor:<fila>`
-- del lado del mayor (el Excel no trae ningún ID, la fila es lo mejor que hay).
-- Las filas viejas quedan en NULL y no colisionan entre sí (en Postgres los NULL
-- son distintos), que es exactamente lo que se quiere: no se inventa una
-- identidad para algo que se anotó sin ella.
ALTER TABLE bancos.conciliacion_pendientes ADD COLUMN IF NOT EXISTS mov_ref text;

DO $$
DECLARE r record;
BEGIN
    -- Se busca por COLUMNAS y no por nombre: el nombre que genera Postgres para
    -- esa constraint pasa los 63 caracteres y queda truncado, así que escribirlo
    -- a mano es una bomba de tiempo.
    FOR r IN
        SELECT conname FROM pg_constraint
         WHERE conrelid = 'bancos.conciliacion_pendientes'::regclass
           AND contype = 'u'
           AND conkey @> ARRAY[(SELECT attnum FROM pg_attribute
                                 WHERE attrelid = 'bancos.conciliacion_pendientes'::regclass
                                   AND attname = 'descripcion')]
    LOOP
        EXECUTE format('ALTER TABLE bancos.conciliacion_pendientes DROP CONSTRAINT %I',
                       r.conname);
    END LOOP;
END $$;

-- El mismo MOVIMIENTO confirmado dos veces es el mismo pendiente. Dos
-- movimientos distintos son dos pendientes, aunque digan lo mismo.
CREATE UNIQUE INDEX IF NOT EXISTS ux_bancos_pendientes_mov
    ON bancos.conciliacion_pendientes (cuenta_id, fecha, accion, mov_ref);

CREATE INDEX IF NOT EXISTS ix_bancos_pendientes_abiertos
    ON bancos.conciliacion_pendientes (resuelto, fecha DESC);

CREATE TABLE IF NOT EXISTS bancos.presencia (
    email     text PRIMARY KEY,
    visto_at  timestamptz NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────────────────
-- AV AGENT — hallazgos de integridad de renta fija (E1)
-- Doc madre: docs/AV_AGENT.md
--
-- La tabla PROPIA del agente: acá escribe con autonomía total porque nada de lo
-- que ponga entra a una valuación. `mercado.curvas` no se toca en esta etapa.
--
-- Append-only por CORRIDA (`corrida_at`), no un upsert por ticker: lo que
-- importa no es solo qué está mal HOY sino desde cuándo y si apareció o
-- desapareció solo. Con un upsert, un hallazgo que se arregla borra la evidencia
-- de que existió — el mismo error que `manager.salud_eventos` ya evita
-- guardando las TRANSICIONES en vez del estado.
--
-- El agente se llamó `curador` durante su primer día (2026-08-16) y se renombró
-- a AV AGENT antes de que persistiera una sola fila (la única corrida fue
-- --dry-run). Por eso la tabla vieja se dropea en vez de migrarse: está vacía por
-- construcción y dejarla sería un fantasma que confunde al próximo que lea el
-- schema. Si por lo que fuera tuviera filas, el DROP las pierde y no importa:
-- son hallazgos de una corrida, se regeneran corriendo el job de nuevo.
DROP TABLE IF EXISTS mercado.curador_hallazgos;

-- ── LA MUDANZA: mercado.av_agent_* → agente.* (2026-08-22) ──────────────────
-- Corre ANTES de los CREATE TABLE de abajo a propósito: si la tabla existe en
-- `mercado` se MUDA (con sus datos, índices y PKs — SET SCHEMA es metadata),
-- y el CREATE IF NOT EXISTS de abajo la encuentra ya en `agente` y no hace
-- nada. Si el orden fuera el inverso, el CREATE crearía una tabla VACÍA en
-- `agente` y la historia quedaría varada en `mercado` — dos verdades.
-- Idempotente: en la segunda pasada no queda nada en `mercado` que mudar.
DO $mudanza_agente$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables
            WHERE schemaname = 'mercado' AND tablename LIKE 'av\_agent%'
  LOOP
    IF EXISTS (SELECT 1 FROM pg_tables
                WHERE schemaname = 'agente' AND tablename = t) THEN
      -- Las dos existen (un deploy a medias creó la nueva vacía): no se pisa
      -- nada solo — se canta para resolver a mano.
      RAISE WARNING 'av_agent: % existe en mercado Y en agente — resolver a mano', t;
    ELSE
      EXECUTE format('ALTER TABLE mercado.%I SET SCHEMA agente', t);
    END IF;
  END LOOP;
END $mudanza_agente$;

CREATE TABLE IF NOT EXISTS agente.av_agent_hallazgos (
    id          bigserial PRIMARY KEY,
    corrida_at  timestamptz NOT NULL DEFAULT now(),
    alcance     text NOT NULL,        -- soberanos | no_corporativos | todo
    tipo        text NOT NULL,        -- falta_en_base | sin_flujo | tasa_sospechosa
    ticker      text NOT NULL,        -- ticker CORTO (AL30), normalizado
    regla       text NOT NULL,        -- qué regla disparó (para agrupar y calibrar)
    severidad   text NOT NULL,        -- alta | media | baja
    motivo      text NOT NULL,        -- la frase en castellano que lee un humano
    evidencia   jsonb                 -- los números que sostienen la afirmación,
                                      -- CONGELADOS: para cuando alguien lo mire,
                                      -- el motivo puede ya no existir.
);

-- El acceso natural es "la última corrida" y "la historia de este ticker".
CREATE INDEX IF NOT EXISTS ix_av_agent_corrida
    ON agente.av_agent_hallazgos (corrida_at DESC);
CREATE INDEX IF NOT EXISTS ix_av_agent_ticker
    ON agente.av_agent_hallazgos (ticker, corrida_at DESC);

-- AV AGENT — lo que el user decidió NO mirar (2026-08-16, calibración de E1).
--
-- El "no me interesa" del agente. Sin esto la lista nunca converge a cero: un
-- ticker que el user ya descartó vuelve a salir todas las noches, y una lista que
-- repite lo descartado se deja de leer.
--
-- Es SOLO para lo caso por caso. Lo que se puede expresar como REGLA va como
-- regla (los Globales en EUR salen por `MONEDAS_SEGUIDAS`, no anotando seis
-- tickers): una regla sigue valiendo cuando aparece el séptimo, una lista no.
--
-- `motivo` es obligatorio a propósito — dentro de seis meses, "por qué ignoramos
-- este bono" es la única pregunta que importa, y es la que E7 va a usar como
-- ejemplo para dejar de proponerlo.
-- agente.av_agent_trazas — LO QUE EL AGENTE DIJO, guardado (2026-08-17).
--
-- Cada diagnóstico se calculaba entero y se tiraba al cerrar el modal. Es la
-- materia prima de todo el aprendizaje: **sin el registro de lo que el agente dijo
-- ayer no hay forma de saber si hoy dice algo mejor.**
--
-- Guarda las OBSERVACIONES completas (las 8 lentes), no solo la causa: la causa
-- dice qué concluyó, las observaciones dicen POR QUÉ — y cuando se equivoca, el
-- error casi siempre está en el razonamiento, no en la etiqueta final.
--
-- `incoherencias` es el agente auditándose: dos lentes que afirman cosas
-- incompatibles sobre el mismo hecho. Una contradicción es un bug DEL AGENTE, no
-- del bono, y por eso viaja en su propia columna en vez de mezclarse.
-- ⚠️ **LA IDENTIDAD DEL HALLAZGO, CALCULADA UNA SOLA VEZ** (§0.bd).
-- La escribe el detector (`core.ciclo.clave_de`) y todos JOINean por acá. Sin
-- esta columna, el que lee tendría que recalcularla —en Python o en SQL— y ahí
-- vuelve REGLA #9: dos implementaciones de la misma identidad que se separan
-- solas, y la memoria queda existiendo pero inalcanzable.
ALTER TABLE agente.av_agent_hallazgos ADD COLUMN IF NOT EXISTS clave text;
CREATE INDEX IF NOT EXISTS ix_av_hallazgos_clave
    ON agente.av_agent_hallazgos (clave);

CREATE TABLE IF NOT EXISTS agente.av_agent_trazas (
    id            bigserial PRIMARY KEY,
    caso          text NOT NULL,          -- ticker del bono o id del chequeo
    dominio       text NOT NULL DEFAULT 'bono',
    causa         text NOT NULL,
    veredicto     text,
    observaciones jsonb,                  -- las lentes, completas
    contexto      jsonb,                  -- los números con los que razonó
    incoherencias jsonb,                  -- lo que el agente se detectó a sí mismo
    por           text,
    creado_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_av_trazas_caso  ON agente.av_agent_trazas (caso, creado_at DESC);
CREATE INDEX IF NOT EXISTS ix_av_trazas_causa ON agente.av_agent_trazas (causa, creado_at DESC);

-- agente.av_agent_lecciones — EL PATRÓN DURABLE (2026-08-17).
--
-- Pedido del user: *«no puede quedar nada desperdiciado: tiene que quedar todo,
-- cómo se va construyendo la solución, los errores que detecto, cómo se fue
-- modificando… porque ahora es bonos, pero después está SALUD y van a venir más
-- cosas.»*
--
-- Una lección es un ciclo de aprendizaje cerrado y contado en cuatro partes:
--
--     SÍNTOMA      lo que se vio en pantalla
--     CAUSA RAÍZ   qué pasaba de verdad
--     CAMBIO       qué se modificó técnicamente
--     COMMIT       dónde quedó
--
-- **Se muestran DENTRO del diagnóstico** cuando aplican a esa causa, no en un doc
-- aparte: una lección sirve en el momento en que alguien está por decidir, no
-- cuando se le ocurra ir a buscarla. Eso es lo que la convierte en memoria del
-- sistema en vez de en documentación.
--
-- `slug` es la identidad: re-anotar la misma lección la ACTUALIZA en vez de
-- duplicarla, porque una lección se refina con el tiempo.
CREATE TABLE IF NOT EXISTS agente.av_agent_lecciones (
    id            bigserial PRIMARY KEY,
    slug          text NOT NULL UNIQUE,
    dominio       text NOT NULL DEFAULT '',   -- '' = aplica a todos
    causa         text NOT NULL DEFAULT '',   -- '' = aplica a cualquier causa
    titulo        text NOT NULL,
    sintoma       text,
    causa_raiz    text,
    cambio        text NOT NULL,
    commit_sha    text,
    detectado_por text NOT NULL DEFAULT 'user',  -- user | agente | test
    activa        boolean NOT NULL DEFAULT true,
    creado_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_av_lecciones_causa ON agente.av_agent_lecciones (causa) WHERE activa;

-- agente.av_agent_evals — EL EVAL SET del agente (2026-08-17).
--
-- **La medición es la piedra angular, y no existía.** El agente diagnostica y
-- arregla, pero nadie podía decir con un número cuánto ACIERTA — y sin eso cada
-- paso hacia la autonomía es un acto de fe. Es lo que el propio doc del agente
-- dice desde el primer día: «un agente que diagnostica sin poder medir si acertó
-- no es un agente, es un generador de opiniones».
--
-- El dataset ya existía y se estaba tirando: cada vez que alguien abre un
-- diagnóstico y decide (aplicar / ignorar / corregir a mano) emite un juicio
-- sobre si la causa era la correcta. Acá ese juicio se guarda.
--
-- Un voto **no cambia nada del sistema**: es una anotación sobre el AGENTE, no
-- sobre el bono. Mezclarlas haría que corregir el diagnóstico parezca arreglar el
-- problema.
--
-- El mismo caso se puede votar N veces y todas quedan (append-only): si el agente
-- cambia de opinión sobre un bono dentro de un mes, la historia de los dos juicios
-- es justamente lo que dice si mejoró.
-- LA MEMORIA DEL TRADUCTOR DE ERRORES (docs/AV_AGENT.md §0.ba).
-- Una explicación por (unidad, patrón) — NO por fila: el mismo error aparece 90
-- veces en 6 horas y pagarle una llamada al modelo a cada aparición sería
-- absurdo. Se explica una vez y la segunda sale de acá.
-- ════════════════════════════════════════════════════════════════════════
-- LA COSA QUE EL AGENTE ENCONTRÓ O DIJO — UNA sola forma (docs/AV_AGENT.md §0.bd)
-- ════════════════════════════════════════════════════════════════════════
--
-- Pedido del user (2026-08-21): *«que todo lo del AV Agent esté como objeto; va
-- a ser siempre el mismo estilo, solo que va a cambiar el TIPO de problema»*.
--
-- Un bono mal cargado, un job que falló, una línea de ERROR de un motor y un
-- aviso a una persona son la MISMA cosa para el ciclo de vida: aparecen, se
-- ven, se actúan, se resuelven y a veces vuelven. Lo que cambia es de qué
-- hablan (`tipo`) y qué se hace con ellos — dos datos, no dos tablas.
--
-- ⚠️ **`clave` ES LA MEMORIA.** Es la PK y no lleva fecha: el mismo problema,
-- visto mañana, es el MISMO objeto. Eso es lo que hace que `abierto_at` mida
-- antigüedad de verdad, que `veces` cuente, y que un problema que reaparece
-- pase a `volvio` en vez de nacer «nuevo» otra vez — que es exactamente lo que
-- venía pasando y se leía como que el agente no se acuerda de nada.
CREATE TABLE IF NOT EXISTS agente.av_agent_items (
    -- ⚠️ **sujeto|causa — SIN fecha, SIN tipo y SIN origen** (§0.bj). Tipo y
    -- origen son QUIÉN LO VIO: meterlos en la identidad hacía que el mismo
    -- bono roto fuera DOS objetos (el del detector y el del control), y
    -- arreglarlo movía uno y dejaba el otro colgado. La arma una sola
    -- función: `av_agent_items.clave_de_problema`.
    clave       text PRIMARY KEY,
    tipo        text NOT NULL,      -- hallazgo · chequeo · log · aviso · pregunta
    origen      text NOT NULL DEFAULT '',   -- qué detector/control lo produjo
    sujeto      text NOT NULL DEFAULT '',   -- el bono, el job, la cuenta
    regla       text NOT NULL DEFAULT '',   -- la causa (lo que mide el eval set)
    -- El ciclo ÚNICO de core/ciclo.py. No hay `resuelto boolean` ni `hecho`
    -- ni `visto_at` como fuente: las marcas de tiempo acompañan, el estado manda.
    estado      text NOT NULL DEFAULT 'nuevo',
    severidad   text NOT NULL DEFAULT 'media',
    veces       integer NOT NULL DEFAULT 1,
    abierto_at  timestamptz NOT NULL DEFAULT now(),   -- NUNCA se pisa
    ultimo_at   timestamptz NOT NULL DEFAULT now(),
    visto_at    timestamptz,
    resuelto_at timestamptz,
    vuelto_at   timestamptz,        -- la última vez que volvió después de resuelto
    -- Lo que varía por TIPO. `titulo` y `afecta` ya traducidos (§0.ba): la
    -- pantalla no tiene que interpretar nada.
    titulo      text NOT NULL DEFAULT '',
    afecta      text NOT NULL DEFAULT '',
    datos       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_av_items_abiertos
    ON agente.av_agent_items (tipo, severidad, ultimo_at DESC)
    WHERE estado <> 'resuelto' AND estado <> 'ignorado';
CREATE INDEX IF NOT EXISTS ix_av_items_sujeto
    ON agente.av_agent_items (sujeto, regla);
-- Para el SEGUIMIENTO escalonado: los resueltos que todavía se están mirando.
CREATE INDEX IF NOT EXISTS ix_av_items_resueltos
    ON agente.av_agent_items (resuelto_at) WHERE estado = 'resuelto';

-- ⚠️⚠️ **CÓMO SE CERRÓ — la columna que desbloquea la medición** (2026-08-24).
--
-- §0.de apagó el voto del tiempo con una condición escrita: *«vuelve a votar
-- cuando el objeto registre CÓMO se cerró»*. Éste es ese campo, y el motivo es
-- que los dos cierres **no significan lo mismo ni por asomo**:
--
--   'accion'   alguien apretó ARREGLAR y el detector después dejó de verlo.
--              Si eso aguanta 30 días, el diagnóstico era correcto. ES un voto.
--   'ausencia' el detector no lo vio en esta corrida. Un bono que no operó esa
--              noche se auto-resuelve. NO prueba que nadie arregló nada, y
--              votarlo es fabricar señal — exactamente lo que se borró en §0.de.
--
-- Sin esta distinción los dos entraban al mismo saco y el ✖ acusaba a arreglos
-- que nadie había hecho (medido: 11 votos ✖, 9 de ellos sobre un mismo caso ya
-- documentado como VOLVIÓ espurio).
ALTER TABLE agente.av_agent_items
    ADD COLUMN IF NOT EXISTS resuelto_como text;

-- Cuántas veces se dio por arreglado y volvió. `vuelto_at` dice CUÁNDO fue la
-- última; esto dice CUÁNTAS. «Esto ya lo arreglamos tres veces y vuelve» es un
-- problema distinto de «pasa hace tres días», y sin el contador se ven igual.
-- La tabla del centinela ya lo tenía y el objeto no: al unificarlos se habría
-- perdido el dato.
ALTER TABLE agente.av_agent_items
    ADD COLUMN IF NOT EXISTS reaperturas integer NOT NULL DEFAULT 0;

-- Quién lo marcó visto. `visto_at` sin `visto_por` no sirve para preguntar.
ALTER TABLE agente.av_agent_items
    ADD COLUMN IF NOT EXISTS visto_por text;

-- ⚠️ **CUÁNTO HABÍA AGUANTADO ANTES DE VOLVER** — la otra condición de §0.de
-- (*«y deje de borrar `resuelto_at`»*). Al pasar a `volvio`, `resuelto_at` se
-- pone en NULL para que el reloj no le siga contando hitos a un arreglo que ya
-- falló; el problema es que con eso se pierde **cuánto duró**, que es justo lo
-- que distingue «falló al día siguiente» de «falló al día 20». Ahora se guarda
-- acá antes de limpiarlo.
ALTER TABLE agente.av_agent_items
    ADD COLUMN IF NOT EXISTS aguanto_hasta timestamptz;

CREATE TABLE IF NOT EXISTS agente.av_agent_errores (
    clave       text PRIMARY KEY,          -- sha256(unidad|patron), 32 chars
    unidad      text NOT NULL,
    patron      text NOT NULL,
    explicacion text NOT NULL,
    creado_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agente.av_agent_evals (
    id              bigserial PRIMARY KEY,
    caso            text NOT NULL,        -- el SUJETO: ticker del bono o id del chequeo
    dominio         text NOT NULL DEFAULT 'bono',   -- bono | salud | sistema
    causa           text NOT NULL,        -- lo que dijo el agente
    acierta         boolean NOT NULL,
    causa_correcta  text,                 -- si no acierta: cuál era (para aprender)
    nota            text,                 -- el POR QUÉ, en castellano
    por             text,
    -- ⚠️ **DE DÓNDE SALE EL VOTO** (2026-08-19). `humano` = alguien apretó ✔/✖ en
    -- la pantalla. `derivado` = se dedujo de una acción que un humano APROBÓ y
    -- que salió bien: aprobar una propuesta ES decir que la causa era la
    -- correcta, pero es una señal más débil que un voto explícito.
    --
    -- Se separan porque si se mezclan, «10/10» podría ser el agente
    -- aplaudiéndose a sí mismo. **`candidata_a_auto` cuenta SOLO los humanos**:
    -- los derivados dan contexto, no abren la compuerta.
    origen          text NOT NULL DEFAULT 'humano',  -- humano | derivado | verificado
    -- De qué se dedujo, para no sembrar dos veces lo mismo (ej. 'accion:1234').
    ref             text,
    creado_at       timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE agente.av_agent_evals ADD COLUMN IF NOT EXISTS origen text NOT NULL DEFAULT 'humano';
ALTER TABLE agente.av_agent_evals ADD COLUMN IF NOT EXISTS ref text;
-- Un voto derivado por origen: re-sembrar es idempotente y no infla el número.
CREATE UNIQUE INDEX IF NOT EXISTS ux_av_agent_evals_ref
    ON agente.av_agent_evals (ref) WHERE ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_av_evals_causa ON agente.av_agent_evals (dominio, causa);
CREATE INDEX IF NOT EXISTS ix_av_evals_caso  ON agente.av_agent_evals (caso, creado_at DESC);

CREATE TABLE IF NOT EXISTS agente.av_agent_ignorados (
    ticker      text PRIMARY KEY,     -- ticker CORTO normalizado (sin especie D/C)
    motivo      text NOT NULL,
    por         text,                 -- email de quien lo ignoró
    creado_at   timestamptz NOT NULL DEFAULT now()
);

-- AV AGENT — PREGUNTAS del agente al humano (E1.c, 2026-08-16).
--
-- El agente no se BLOQUEA esperando una decisión: la convierte en una pregunta,
-- la deja acá, y sigue con lo que sí puede hacer. El user contesta cuando puede.
-- Sin esto, cada decisión abierta frenaba el proyecto entero.
--
-- `clave` es ÚNICA y estable (`falta:TZXD8`) → **el agente no repregunta**. Una
-- herramienta que vuelve a preguntar lo mismo todas las noches se deja de leer,
-- exactamente igual que una lista que repite lo descartado.
--
-- Responder no es anotar una opinión: **dispara un EFECTO** (`ignorar` escribe en
-- `av_agent_ignorados`, `alta` marca el bono para que E2 lo simule). Por eso
-- `aplicada_at` es distinto de `respondida_at`: una respuesta cuyo efecto falló
-- no puede quedar como si hubiera surtido.
--
-- Las de `tipo='decision'` son las decisiones de diseño del propio agente (qué
-- alcance mirar, quién gana ante un conflicto). Su efecto no es automático: lo
-- aplica el desarrollo siguiente. Se guardan igual para que la decisión no se
-- pierda en un chat.
CREATE TABLE IF NOT EXISTS agente.av_agent_preguntas (
    id             bigserial PRIMARY KEY,
    clave          text NOT NULL UNIQUE,   -- idempotencia: 'falta:TZXD8', 'decision:alcance'
    tipo           text NOT NULL,          -- hallazgo | decision
    pregunta       text NOT NULL,          -- en castellano, para leer sin contexto
    opciones       jsonb NOT NULL,         -- ["alta","ignorar","despues"]
    contexto       jsonb,                  -- la evidencia, congelada
    estado         text NOT NULL DEFAULT 'abierta',   -- abierta | respondida
    respuesta      text,
    nota           text,                   -- el POR QUÉ que escribe el user
    respondida_por text,
    respondida_at  timestamptz,
    aplicada_at    timestamptz,            -- cuándo surtió efecto (NULL = no surtió)
    creada_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_av_agent_preg_abiertas
    ON agente.av_agent_preguntas (estado, creada_at);

-- ⚠️ **LA IDENTIDAD, EN COLUMNAS Y NO ADENTRO DE LA `clave`** (2026-08-21, §0.bk).
-- La `clave` es `falta:TZXD8`: causa y sujeto pegados con dos puntos. Alcanza
-- para la idempotencia, pero para espejar la pregunta como objeto hay que
-- separarlos, y hacerlo con un `split(':')` ataría la identidad a una convención
-- de texto — el día que un sujeto traiga `:` adentro partiría mal y en silencio
-- (REGLA #9). Se guardan aparte, escritos por quien arma la pregunta.
ALTER TABLE agente.av_agent_preguntas ADD COLUMN IF NOT EXISTS sujeto text;
ALTER TABLE agente.av_agent_preguntas ADD COLUMN IF NOT EXISTS causa text;

-- AV AGENT — CATÁLOGO DE CURVAS (2026-08-17). La curva deja de ser código.
--
-- `curvas_ejes._pill_de_ajuste` era una función con ocho `if` que devolvían un
-- string: una TABLA disfrazada. Mientras lo fue, agregar una curva era un deploy
-- —y por eso `badlar`, `tpm` y `caucion` nunca la tuvieron, dejando sus bonos
-- cargados e INVISIBLES en toda la app.
--
-- Acá vive lo que hace falta para que una curva exista, que es todo DATO:
--   · `pill` / `display` / `lado` / `orden` → dónde y cómo se muestra
--   · `fuente_valuacion` → de dónde sale su TASA, y es la decisión que importa:
--       'motor' = la calcula engines/curvas.py (hace falta escribir la rama)
--       '1816'  = se trae, igual que los TAMAR (`jobs/tamar_1816`) — sin código
--
-- Las 5 curvas VIEJAS siguen en código a propósito: si esta tabla no responde,
-- la vista de renta fija tiene que seguir andando exactamente igual. El catálogo
-- SUMA curvas, nunca las pisa (ver core/curvas_catalogo.py).
CREATE TABLE IF NOT EXISTS mercado.curvas_catalogo (
    ajuste           text PRIMARY KEY,   -- badlar | tpm | caucion | …  (core.curvas_ejes.AJUSTES)
    pill             text NOT NULL,      -- código de la pill (normalmente = ajuste)
    display          text NOT NULL,      -- lo que se lee en la tab: 'BADLAR'
    lado             text NOT NULL,      -- ARS | USD (la columna de la vista)
    orden            int  NOT NULL DEFAULT 99,
    fuente_valuacion text NOT NULL DEFAULT 'motor',   -- motor | 1816
    nota             text,
    creada_por       text,
    creada_at        timestamptz NOT NULL DEFAULT now()
);

-- AV AGENT — LIBRO DE ACCIONES (2026-08-17). Todo lo que el agente ESCRIBIÓ.
--
-- Append-only. Es la respuesta a "¿qué tocó, cuándo, en qué tabla y por qué?", y
-- deja de ser un lujo en cuanto el agente escriba en `mercado.curvas`: una
-- escritura automática sin libro es una escritura que nadie puede auditar ni
-- revertir.
--
-- Lo que hoy existe está DESPARRAMADO —`av_agent_preguntas.aplicada_at`,
-- `av_agent_ignorados.creado_at`, `curvas_catalogo.creada_at`— y ninguna de esas
-- tablas contesta la pregunta completa. Esta sí, y en un solo lugar.
--
-- `antes` guarda el estado previo: sin eso, "revertir" es una promesa y no una
-- función. `ok`/`error` registran también los INTENTOS FALLIDOS — un libro que
-- solo anota los éxitos hace parecer que el agente nunca se equivoca.
CREATE TABLE IF NOT EXISTS agente.av_agent_acciones (
    id          bigserial PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    accion      text NOT NULL,     -- ignorar_ticker | designorar | crear_curva | alta_bono …
    destino     text NOT NULL,     -- LA TABLA que se tocó: 'mercado.curvas_catalogo'
    objetivo    text NOT NULL,     -- sobre qué: ticker, ajuste, …
    detalle     jsonb,             -- lo que quedó escrito (el "después")
    antes       jsonb,             -- el estado previo, para poder revertir
    origen      text NOT NULL DEFAULT 'respuesta',  -- respuesta | job | api
    -- ⚠️ **QUÉ DIAGNOSTICÓ EL AGENTE**, no solo qué hizo (2026-08-19). El libro
    -- registraba la ACCIÓN (`alta_bono`) y no la CAUSA que la motivó, así que
    -- desde el historial no se puede saber qué regla se estaba juzgando cuando
    -- el humano aprobó. Sin esto, cada acción aprobada —que ES un juicio sobre
    -- el diagnóstico— se tiraba, y el eval set tenía que llenarse a mano.
    regla       text,              -- la regla del hallazgo que originó la acción
    pregunta_id bigint,            -- qué pregunta la disparó, si vino de una
    por         text,              -- email del humano que la pidió
    ok          boolean NOT NULL DEFAULT true,
    error       text
);

ALTER TABLE agente.av_agent_acciones ADD COLUMN IF NOT EXISTS regla text;
CREATE INDEX IF NOT EXISTS ix_av_agent_acciones_ts
    ON agente.av_agent_acciones (ts DESC);
CREATE INDEX IF NOT EXISTS ix_av_agent_acciones_obj
    ON agente.av_agent_acciones (objetivo, ts DESC);


-- AVISOS del AV AGENT: trabajo MANUAL pendiente que dejó un alta.
--
-- El agente hace el 95% (flujos, ejes, ficha, especies) y anota el 5% que
-- ninguna fuente publica — el caso testigo es el `cer_emision`. Nace al APLICAR
-- y **lo cierra una persona**: si se derivara del estado del master, cargar el
-- dato lo haría desaparecer sin dejar rastro de que existió, y el user no
-- tendría dónde ver qué le queda por hacer ni qué ya hizo.
--
-- La lectura igual CRUZA contra el master (`ya_cargado`), así que un aviso
-- marcado como hecho sobre un dato que sigue faltando se canta en la pantalla en
-- vez de mentir en silencio. Ese cruce es lo que hace seguro el cierre manual.
CREATE TABLE IF NOT EXISTS agente.av_agent_avisos (
    id           bigserial PRIMARY KEY,
    ticker       text NOT NULL,
    clave        text NOT NULL,     -- cer_emision | emisor | …
    que_hacer    text NOT NULL,
    por_que      text,
    donde        text,
    creado_at    timestamptz NOT NULL DEFAULT now(),
    creado_por   text,
    resuelto     boolean NOT NULL DEFAULT false,
    resuelto_por text,
    resuelto_at  timestamptz
);

-- ⚠️ **EL DESTINATARIO ENTRA A LA CLAVE** (bug 2026-08-19). El índice ANTERIOR
-- era `(ticker, clave)` e ignoraba `para`, así que el PRIMER ping sobre un
-- control lo bloqueaba para
-- todos los demás: avisarle a otra persona hacía `ON CONFLICT DO NOTHING`,
-- devolvía 0, y como nadie miraba ese 0 la acción decía «listo» y al
-- destinatario no le llegaba nada.
--
-- Es el modo de falla de siempre: **no falla nada**. Se reportó bien, se
-- verificó mal y el mensaje no existió. El user lo encontró probando el aviso de
-- `comitentes_sin_nivel1`.
--
-- Un aviso ABIERTO es único por (ticker, clave, DESTINATARIO). `coalesce` porque
-- los avisos de un alta nacen sin dueño y ésos siguen siendo uno solo.
-- MIGRACIÓN de una sola vez: el índice viejo ya no se crea nunca más (su
-- `CREATE` se borró de este archivo, no se dejó arriba). Dejarlo habría hecho
-- que **cada deploy intentara recrearlo** — y desde que dos operadores reciben
-- el mismo tema, esa recreación FALLA por duplicado y **frena el deploy entero**.
-- Pasó el 2026-08-19, y el error es exactamente la prueba de que el índice viejo
-- no puede representar lo que ahora guardamos.
DROP INDEX IF EXISTS mercado.ux_av_agent_avisos_abierto;
-- ⚠️ **UN MENSAJE PUEDE TRAER UNA TABLA Y EXIGIR QUE LA COMPLETES** (2026-08-19).
--
-- El user, sobre el aviso de saldos: *«tiene que ser como el modal de briefing:
-- aparece en la pantalla, llama la atención y te hace hacer algo para continuar.
-- No que aparezca en el cuerpo del agente como si nada.»*
--
-- Hasta acá un aviso era un texto que esperaba en la campanita. Para lo que el
-- user necesita hacen falta tres cosas más:
--   · `datos`      — las filas (cuenta, moneda, saldo), no un párrafo
--   · `interrumpe` — si abre solo o espera a que lo vayan a buscar
--   · `vence_at`   — un aviso de saldos vale HOY; mañana el mercado abre y ya no
--                    dice nada. Sin vencimiento sería una foto vieja pidiendo
--                    acción, que es peor que no avisar.
ALTER TABLE agente.av_agent_avisos ADD COLUMN IF NOT EXISTS datos jsonb;
ALTER TABLE agente.av_agent_avisos ADD COLUMN IF NOT EXISTS interrumpe boolean NOT NULL DEFAULT false;
ALTER TABLE agente.av_agent_avisos ADD COLUMN IF NOT EXISTS vence_at timestamptz;

-- Las FILAS de un aviso, cada una con su tilde y su hora. El user pidió que el
-- operador marque completado uno por uno y que eso persista con timestamp.
--
-- Van en su propia tabla y no adentro del jsonb porque **cada tilde es un hecho
-- auditable**: quién y cuándo. Un jsonb pisado en cada click deja el último
-- estado y borra el camino.
CREATE TABLE IF NOT EXISTS agente.av_agent_aviso_items (
    id        bigserial PRIMARY KEY,
    aviso_id  bigint NOT NULL REFERENCES agente.av_agent_avisos(id) ON DELETE CASCADE,
    orden     integer NOT NULL DEFAULT 0,
    clave     text NOT NULL,          -- identidad de la fila (ej. '805:ARS')
    etiqueta  text NOT NULL,          -- lo que se lee
    datos     jsonb,                  -- los números, para la tabla
    hecho     boolean NOT NULL DEFAULT false,
    hecho_at  timestamptz,
    hecho_por text,
    UNIQUE (aviso_id, clave)
);
CREATE INDEX IF NOT EXISTS ix_av_aviso_items
    ON agente.av_agent_aviso_items (aviso_id, orden);

CREATE UNIQUE INDEX IF NOT EXISTS ux_av_agent_avisos_abierto_para
    ON agente.av_agent_avisos (ticker, clave, coalesce(lower(para), ''))
    WHERE NOT resuelto;
-- A QUIÉN le toca (2026-08-19). NULL = de todos, que es como funcionó hasta
-- ahora y sigue siendo el default: la lista de pendientes del alta de bonos no
-- tiene dueño. Se completa cuando el agente le avisa a una persona puntual
-- (`av_agent_hacer.AccionAvisar`), y ahí la fila aparece en SU lista.
--
-- No se hizo una tabla de notificaciones nueva a propósito: esta ya tiene alta,
-- cierre por una persona y pantalla. Un segundo buzón daría dos lugares donde
-- mirar lo que hay para hacer — el problema exacto que SALUD vino a resolver.
ALTER TABLE agente.av_agent_avisos ADD COLUMN IF NOT EXISTS para text;
CREATE INDEX IF NOT EXISTS ix_av_agent_avisos_para
    ON agente.av_agent_avisos (lower(para)) WHERE NOT resuelto;


-- ─────────────────────────────────────────────────────────────────────────────
-- agente.av_agent_control — LA PARADA DE EMERGENCIA del AV Agent (2026-08-18).
--
-- Pedido del user: *«quiero control total del agente desde el modal por las
-- dudas»*. Hasta acá el agente tenía 15 endpoints y los 15 actuaban sobre UN
-- hallazgo — no existía ninguno que actuara sobre EL AGENTE. Si empezaba a
-- escribir algo mal, la única forma de frenarlo era no apretar el botón, o
-- apagar la API entera.
--
-- UNA SOLA FILA, garantizado por el motor: `id boolean PRIMARY KEY CHECK (id)`.
-- Solo el valor `true` pasa el CHECK y la PK impide que exista dos veces → la
-- tabla no puede tener más de una fila ni por error de código ni a mano. Es más
-- barato que un trigger y no se puede saltear (el patrón vale para cualquier
-- tabla de configuración global).
--
-- No es un permiso: los permisos ya los da `require_admin` + el humano que
-- aprueba cada escritura. Esto es un INTERRUPTOR — corta las escrituras del
-- agente sin tocar la lectura, así se puede seguir diagnosticando con la mano
-- frenada, que es justo lo que uno quiere mientras investiga.
CREATE TABLE IF NOT EXISTS agente.av_agent_control (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),
    parada      boolean NOT NULL DEFAULT false,
    motivo      text NOT NULL DEFAULT '',
    por         text NOT NULL DEFAULT '',
    cambiado_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO agente.av_agent_control (id) VALUES (true) ON CONFLICT DO NOTHING;


-- ─────────────────────────────────────────────────────────────────────────────
-- agente.av_agent_runs — EL DIAGNÓSTICO MASIVO (2026-08-18).
--
-- Pedido del user: *«un botón que haga un estado de situación con los que dieron
-- error, los que dieron bien, etc., bien completo, que quede para copiar y pegar
-- así te paso las respuestas»*.
--
-- **Por qué es una TABLA y no un request que devuelve un JSON.** El límite del
-- plan de 1816 es **1 petición por segundo**, y el throttle es global entre
-- procesos. 68 bonos con `cashflow` son ~82 segundos de piso y 2-3 minutos
-- reales: ningún request HTTP sobrevive a eso (el propio agente aborta a los 45s
-- por `_PRESUPUESTO_S`). Así que la corrida vive en background y el modal
-- pollea — y de yapa se puede frenar a la mitad y el informe queda para
-- reabrirlo y compararlo con el de la semana que viene.
--
-- `informe` guarda el resultado de CADA caso, no un resumen: el agregado se
-- deriva en la lectura. Un resumen persistido se contradice con su propio
-- detalle en cuanto cambia la forma de agrupar.
CREATE TABLE IF NOT EXISTS agente.av_agent_runs (
    id          bigserial PRIMARY KEY,
    creado_at   timestamptz NOT NULL DEFAULT now(),
    fin_at      timestamptz,
    -- corriendo | terminado | frenado | error | interrumpido
    -- `interrumpido` NO lo escribe la corrida (si el proceso murió, no puede
    -- escribir nada): lo deduce el lector cuando ve un `corriendo` sin latido.
    estado      text NOT NULL DEFAULT 'corriendo',
    por         text,
    filtro      jsonb,              -- qué se pidió: tipo, regla, búsqueda, tickers
    total       integer NOT NULL DEFAULT 0,
    hechos      integer NOT NULL DEFAULT 0,
    latido_at   timestamptz,        -- para distinguir "corriendo" de "murió el proceso"
    sin_red     boolean NOT NULL DEFAULT false,
    tope_creditos integer,
    creditos    integer,            -- consumidos, medidos contra /balance
    error       text,
    informe     jsonb NOT NULL DEFAULT '[]'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_av_runs_creado ON agente.av_agent_runs (creado_at DESC);

-- CERRAR el informe (2026-08-22): el run quedaba pegado en la pantalla para
-- siempre — un «interrumpido» de ayer sin forma de sacarlo. `visto_at` lo
-- cierra SIN borrarlo (la corrida es historia); el front lo muestra plegado.
ALTER TABLE agente.av_agent_runs ADD COLUMN IF NOT EXISTS visto_at timestamptz;

-- El ANÁLISIS con IA del informe masivo (2026-08-18, ver av_agent_analista.py).
-- Pegado a SU corrida: sin esto habría que volver a pagarlo cada vez que se
-- reabre el informe — y peor, dos lecturas del mismo informe podrían decir
-- cosas distintas.
ALTER TABLE agente.av_agent_runs ADD COLUMN IF NOT EXISTS analisis    text;
ALTER TABLE agente.av_agent_runs ADD COLUMN IF NOT EXISTS analisis_at timestamptz;


-- ─────────────────────────────────────────────────────────────────────────────
-- EL CENTINELA (2026-08-18) — el agente PRENDIDO durante toda la rueda.
--
-- Pedido del user: *«me encantaría ver a esto con un círculo verde de que está
-- prendido y vaya monitoreando en real time todo lo que vaya pasando… que no
-- haga nada de solucionar pero que sí me dé las cosas. El job actual es muy
-- poco: tiene que estar vigilando constantemente SIN PISAR LO QUE YA REPORTÓ y
-- todavía no hice nada»*.
--
-- **Esa última frase es la que define el modelo de datos.** El monitor anterior
-- hacía DELETE + INSERT en cada pasada: cada 5 minutos borraba todo y volvía a
-- escribir. Con eso es imposible saber si algo es nuevo, desde cuándo pasa, o si
-- ya lo miraste — y lo que estabas por atender desaparece y vuelve a aparecer
-- como si fuera otra cosa.
--
-- Acá cada hallazgo tiene **IDENTIDAD ESTABLE** (`clave` = tipo:sujeto:regla) y
-- un CICLO DE VIDA:
--
--     abierto_at  cuándo apareció por primera vez     (nunca se pisa)
--     ultimo_at   cuándo se lo vio por última vez     (se refresca)
--     veces       cuántos ciclos lleva                (mide persistencia)
--     visto_at    cuándo el humano lo miró            (lo pone la UI)
--     resuelto_at cuándo dejó de aparecer             (lo pone el centinela)
--
-- Un hallazgo que vuelve REABRE el mismo (resuelto_at → NULL): no nace otro.
-- Uno que deja de verse se marca `resuelto_como='solo'` — **no se borra**: «se
-- arregló solo» es información, y borrarlo dejaría la misma amnesia de antes.
CREATE TABLE IF NOT EXISTS agente.av_agent_centinela (
    id           bigserial PRIMARY KEY,
    -- La IDENTIDAD. Estable entre ciclos: mismo problema = misma fila.
    clave        text NOT NULL UNIQUE,
    tipo         text NOT NULL,
    sujeto       text NOT NULL,
    regla        text NOT NULL,
    severidad    text NOT NULL,
    motivo       text NOT NULL,
    evidencia    jsonb,
    abierto_at   timestamptz NOT NULL DEFAULT now(),
    ultimo_at    timestamptz NOT NULL DEFAULT now(),
    veces        integer     NOT NULL DEFAULT 1,
    visto_at     timestamptz,
    visto_por    text,
    resuelto_at  timestamptz,
    resuelto_como text                  -- solo | manual
);

CREATE INDEX IF NOT EXISTS ix_av_centinela_abiertos
    ON agente.av_agent_centinela (severidad, abierto_at DESC) WHERE resuelto_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_av_centinela_ultimo
    ON agente.av_agent_centinela (ultimo_at DESC);

-- EL LATIDO — una sola fila. Es lo que hace que el círculo verde no mienta:
-- sin un latido reciente, «prendido» sería una afirmación sobre el pasado.
-- agente.av_agent_seguimiento — ¿LO QUE SE ARREGLÓ, SIGUIÓ ARREGLADO? (2026-08-19)
--
-- Pedido del user: *«necesito que este agente entienda cuándo hizo algo bien, no
-- solamente porque yo le puse "acertó", sino porque queda registrado y al otro
-- día o durante unos días puede detectar que los cambios que encontró y se
-- marcaron como hechos realmente tuvieron consistencia. Es como que yo diga que
-- modelé bien un bono: mañana cuando abre el mercado lo veo en la tabla y digo
-- sí, la verdad lo hice bien, porque si no vería un error.»*
--
-- **Es la diferencia entre "lo apliqué" y "funcionó".** Hasta hoy el agente
-- verificaba releyendo la base en el mismo segundo — o sea, que la escritura
-- entró. Eso no dice nada sobre si el arreglo era el correcto: un símbolo mal
-- puesto se escribe igual de bien que uno bien puesto.
--
-- La única prueba de verdad es el tiempo: si el problema NO vuelve en los días
-- siguientes, el arreglo era el bueno. Y si vuelve, el diagnóstico estaba mal —
-- que es información igual de valiosa y hoy se perdía entera.
--
-- Alimenta el eval set con la evidencia MÁS FUERTE que existe, porque no es la
-- opinión de nadie: el problema volvió o no volvió.
CREATE TABLE IF NOT EXISTS agente.av_agent_seguimiento (
    clave         text PRIMARY KEY,   -- la MISMA identidad estable del hallazgo
    tipo          text,
    sujeto        text NOT NULL,      -- el bono, el job, la cuenta…
    regla         text NOT NULL,      -- la causa que se dio por resuelta
    dominio       text NOT NULL DEFAULT 'bono',
    arreglado_at  timestamptz NOT NULL DEFAULT now(),
    arreglado_por text,               -- quién lo dio por hecho
    que_se_hizo   text,
    -- Hasta cuándo se sigue mirando. Pasada esa fecha sin que vuelva, se da por
    -- bueno. Antes de eso no se afirma nada: **"todavía no volvió" no es
    -- "aguantó"**, y confundirlos sería premiar un arreglo de hace una hora.
    mirar_hasta   timestamptz NOT NULL,
    revisiones    integer NOT NULL DEFAULT 0,
    ultima_revision_at timestamptz,
    volvio_at     timestamptz,        -- si el problema reapareció
    veredicto     text NOT NULL DEFAULT 'mirando',  -- mirando | aguanto | volvio
    -- Para no votar dos veces lo mismo en el eval set.
    votado        boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_av_seguimiento_mirando
    ON agente.av_agent_seguimiento (mirar_hasta) WHERE veredicto = 'mirando';

-- ¿CUÁNTAS VECES VOLVIÓ? El centinela ya contaba `veces` (cuántos ciclos se vio)
-- pero no cuántas veces pasó de RESUELTO a abierto de nuevo — y ésa es la que
-- dice «esto ya lo arreglamos tres veces y vuelve». Un problema que reaparece no
-- es el mismo problema de siempre: es uno que no estamos entendiendo.
ALTER TABLE agente.av_agent_centinela
    ADD COLUMN IF NOT EXISTS reaperturas integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS agente.av_agent_latido (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    at         timestamptz NOT NULL DEFAULT now(),
    ciclo      bigint      NOT NULL DEFAULT 0,
    en_rueda   boolean     NOT NULL DEFAULT false,
    abiertos   integer     NOT NULL DEFAULT 0,
    nuevos     integer     NOT NULL DEFAULT 0,
    duracion_ms integer,
    error      text
);

INSERT INTO agente.av_agent_latido (id) VALUES (true) ON CONFLICT DO NOTHING;

-- Cada cuánto PROMETE latir el centinela (2026-08-18). Sin esto el umbral de
-- «vivo» estaba clavado en el ritmo de rueda (30s) y fuera de rueda —donde late
-- cada 5 minutos— el círculo salía GRIS con el proceso perfectamente vivo.
-- El latido declara su propia cadencia: así el umbral la sigue sola y no puede
-- volver a desincronizarse cuando se cambie un intervalo.
ALTER TABLE agente.av_agent_latido ADD COLUMN IF NOT EXISTS proximo_en_s integer;


-- ─────────────────────────────────────────────────────────────────────────────
-- manager.tabla_perfil — QUÉ ES CADA TABLA Y CÓMO SE COMPORTA (2026-08-19).
--
-- Pedido del user: *«que el agente sepa exactamente cada tabla que hay y cómo
-- funciona esa tabla en cuanto a los datos… que no dependa de un git pull, que no
-- dependa de cosas estáticas, que siempre sepa qué hay en las bases de schema y
-- eso o de tablas posta»*.
--
-- **NADA DE ESTO SE DECLARA A MANO.** Escribir el contrato de 200 tablas es algo
-- que nadie hace, y la lista quedaría vieja el primer mes — que es peor que no
-- tenerla, porque afirma cosas falsas. Todo se DERIVA:
--
--   · QUÉ TABLAS HAY        → pg_catalog. Una tabla nueva aparece sola.
--   · CUÁL ES SU FECHA      → information_schema (la primera columna temporal).
--   · CADA CUÁNTO SE ESCRIBE→ **se MIDE mirando la distribución de esa columna**.
--     Si tiene un dato cada 5 segundos es tiempo real; si tiene uno por día
--     hábil, es diaria. Nadie lo declara: la tabla lo dice.
--
-- Esta tabla es solo la MEMORIA de esa medición, para no re-medir 200 tablas en
-- cada consulta. Una fila por tabla, upsert, **no crece**: si la tabla deja de
-- existir su fila queda y el próximo barrido la marca ausente.
CREATE TABLE IF NOT EXISTS manager.tabla_perfil (
    schema      text NOT NULL,
    tabla       text NOT NULL,
    col_fecha   text,                    -- la columna temporal que se encontró
    cadencia    text,                    -- tiempo_real | intradiaria | diaria_habil
                                         -- | diaria | eventual | estatica | vacia
    -- La evidencia de la medición: cada cuántos segundos, medido como la MEDIANA
    -- de las diferencias entre escrituras. Se guarda para poder discutir el
    -- veredicto en vez de tener que creerlo.
    intervalo_p50_s bigint,
    filas       bigint,
    ultimo_dato timestamptz,
    medido_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (schema, tabla)
);

-- manager.db_tamano — LA FOTO DIARIA DE LA BASE (2026-08-19).
--
-- Pedido del user: *«que entienda qué tablas hay, cuánto pesa cada una (al menos
-- una vez por día) y sepa distinguir al día siguiente si se agregó algo nuevo, y
-- cuáles aumentaron su tamaño y por cuánto… esto tendrá que persistir para tener
-- contexto, pero a su vez no crecer todo el tiempo: con que tenga registro de hoy
-- y ayer constantemente alcanza»*.
--
-- **DOS FECHAS Y NADA MÁS.** La purga va en el MISMO INSERT del job (mismo patrón
-- que `tesoreria_snapshots`), así no depende de que alguien se acuerde de correr
-- una limpieza: son ~200 filas × 2 días. Una tabla que vigila el tamaño de la
-- base y crece sin techo es un chiste que se cuenta solo.
--
-- Por qué se persiste en vez de calcularlo en vivo: `pg_total_relation_size` dice
-- cuánto pesa HOY. **El delta es la información** —qué apareció, qué creció y por
-- cuánto— y para eso hace falta el de ayer. Sin foto no hay comparación.
CREATE TABLE IF NOT EXISTS manager.db_tamano (
    fecha  date   NOT NULL,
    schema text   NOT NULL,
    tabla  text   NOT NULL,
    bytes  bigint NOT NULL,
    filas  bigint,
    PRIMARY KEY (fecha, schema, tabla)
);

-- El total de la BASE, que NO es la suma de las tablas: incluye índices del
-- catálogo, TOAST y espacio libre. Se guarda aparte para que las dos cifras no
-- se puedan confundir — que una suma de tablas no cierre contra el total del
-- plan es normal, y descubrirlo mirando un número que decía ser el total es
-- exactamente cómo se pierde una tarde.
-- manager.proveedor_estado — CÓMO VIENE CONTESTANDO CADA PROVEEDOR EXTERNO
-- (2026-08-20). Doc: AV_AGENT.md §0.ad.
--
-- Nace de una caída de Aunesa (HTTP 500 en su login) que el user vio «de
-- milagro» al abrir Tesorería. La vista YA la detecta y la muestra bien — pero
-- solo mientras alguien tiene la pantalla abierta. Si nadie entra, nadie sabe, y
-- el back office puede pasar la mañana creyendo que el saldo del día está
-- completo cuando le falta la mitad.
--
-- UNA FILA POR PROVEEDOR, sin histórico: lo que importa es cómo está AHORA. El
-- histórico de caídas, si algún día hace falta, es otra tabla — mezclarlos haría
-- crecer sin techo justo a la que se consulta cada 5 minutos.
--
-- Se escribe SOLO cuando algo falla (y como mucho una vez por minuto y por
-- proceso): un proveedor sano no cuesta ni una escritura. Por eso mismo el
-- detector exige que el último fallo sea RECIENTE — si el proveedor se recupera
-- los fallos dejan de anotarse y nadie apaga el registro, así que un 500 de la
-- semana pasada seguiría en pantalla para siempre (la lección de §0.u).
CREATE TABLE IF NOT EXISTS manager.proveedor_estado (
    proveedor       text PRIMARY KEY,
    ok              boolean NOT NULL DEFAULT true,
    ultimo_error    text,
    ultimo_error_at timestamptz,
    ultimo_ok_at    timestamptz,
    fallos_seguidos integer NOT NULL DEFAULT 0,
    donde           text,
    actualizado_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS manager.db_tamano_dia (
    fecha        date   PRIMARY KEY,
    bytes_total  bigint NOT NULL,
    n_tablas     integer NOT NULL DEFAULT 0
);

-- manager.superficie_dia — LA MEMORIA DE LA SUPERFICIE HTTP (2026-08-19).
--
-- Pedido del user: *«esto no tiene que ser estático… pasa el tiempo, avanza la
-- app, se agregan cosas nuevas y se vuelve a quedar desactualizado todo. El
-- agente debe PERSISTIR: ya tiene que tener mapeado todo»*.
--
-- Sin esta foto, el chequeo de permisos solo sabe decir CUÁNTOS endpoints están
-- abiertos hoy. Con ella dice lo que importa: cuál APARECIÓ sin gate y —lo que
-- ninguna foto puede ver— cuál PERDIÓ el gate que tenía ayer. Esa regresión es
-- invisible para un conteo: se cierra uno, se abre otro y el total no se mueve.
--
-- Mismo contrato que `manager.db_tamano`: SOLO hoy y ayer, purgadas en el mismo
-- INSERT (`av_agent_seguridad.sacar_foto`). Una tabla que vigila a la app y
-- crece sin techo es un chiste que se cuenta solo.
CREATE TABLE IF NOT EXISTS manager.superficie_dia (
    fecha     date    NOT NULL,
    path      text    NOT NULL,
    metodos   text    NOT NULL,
    gates     text    NOT NULL DEFAULT '',
    sin_gate  boolean NOT NULL DEFAULT false,
    escribe   boolean NOT NULL DEFAULT false,
    PRIMARY KEY (fecha, path, metodos)
);

-- agente.av_agent_propuestas — LO QUE EL AGENTE SABE HACER (2026-08-19).
--
-- Pedido del user: *«que el agente aprenda a sugerir, y que si le das OK
-- actualice en el momento y luego controle que lo hizo bien en el mismo
-- proceso… que ya tenga la feature armada pero que se haga con mi permiso»*.
--
-- **El ciclo es PROPONER → APROBAR → APLICAR → VERIFICAR**, y es el mismo que
-- ya usa el alta de un bono (simular → aplicar → verificar). Que sea uno solo
-- importa: el que aprueba un cambio de cartera no tiene que aprender otro
-- modelo mental que el que aprueba un alta.
--
-- **Por qué se PERSISTE la propuesta y no se aplica de una:**
--
--   · el OK del humano es el único gate real, y sin fila no hay a qué darle OK;
--   · `antes` guarda el valor previo → «revertir» es una función y no una
--     promesa (misma decisión que el libro de acciones);
--   · `verificado` separa «lo escribí» de «quedó bien». Son cosas distintas y
--     confundirlas es como se acumulan arreglos que no arreglaron nada;
--   · cada propuesta con su resultado ES el eval set de las acciones: sin esto
--     no hay forma de saber si el agente sugiere bien, y sin eso no se le puede
--     dar más autonomía sin fe.
CREATE TABLE IF NOT EXISTS agente.av_agent_propuestas (
    id          bigserial PRIMARY KEY,
    creado_at   timestamptz NOT NULL DEFAULT now(),
    accion      text NOT NULL,            -- 'assets.cartera'
    -- Identidad del caso. UNIQUE con la acción: re-proponer sobre lo mismo
    -- ACTUALIZA la propuesta en vez de acumular diez para el mismo asset.
    sujeto      text NOT NULL,
    campo       text NOT NULL,
    antes       text,
    propuesto   text NOT NULL,
    -- De dónde salió: 'regla' (determinista) o 'ia'. **Se guarda porque cambia
    -- cuánto hay que mirarla**: una regla se audita leyendo el código, una
    -- sugerencia del modelo hay que mirarla caso por caso.
    fuente      text NOT NULL DEFAULT 'regla',
    confianza   numeric,                  -- 0..1
    porque      text NOT NULL DEFAULT '',
    -- propuesta | aplicada | rechazada | fallida
    estado      text NOT NULL DEFAULT 'propuesta',
    por         text,
    aplicado_at timestamptz,
    -- NULL = todavía no se verificó. TRUE/FALSE = se miró después de escribir.
    verificado  boolean,
    verificado_detalle text,
    error       text,
    -- Lo que la acción necesita para aplicar y que no entra en `propuesto`:
    -- la denominación y el segmento de una contraparte, el texto del aviso.
    -- Va acá y no en columnas porque cada acción necesita cosas distintas y
    -- una columna por acción convertiría la tabla en un formulario.
    extra       jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (accion, sujeto, campo)
);

CREATE INDEX IF NOT EXISTS ix_av_prop_pendientes
    ON agente.av_agent_propuestas (accion, creado_at DESC) WHERE estado = 'propuesta';
