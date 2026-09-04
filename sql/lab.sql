-- `sql/lab.sql` — EL LABORATORIO: su esquema, y sobre todo SU ALCANCE.
--
-- ⚠️⚠️ **ESTE ARCHIVO ES LA DEFINICIÓN DE LO QUE EL INVESTIGADOR PUEDE VER.**
--
-- Antes de que existiera, los permisos de `lector_lab` sobre PRODUCCIÓN se
-- habían otorgado A MANO desde el editor de Supabase y no estaban escritos en
-- ninguna parte del repo. Tres cosas que eso rompía, y ninguna avisaba:
--
--   1. **No se podía auditar leyendo el proyecto.** La única forma de contestar
--      «¿a qué tiene acceso esto?» era entrar a la base a mirar.
--   2. **No se reproducía.** Una base nueva, o un restore, no traía los roles:
--      había que rehacerlos de memoria.
--   3. **No había techo.** `probar_lector` comprobaba que PUEDE leer seis
--      tablas y que NO puede leer `clientes`. Nunca que pueda leer SÓLO esas
--      seis: un `GRANT SELECT ON ALL TABLES IN SCHEMA mercado` otorgado un día
--      apurado pasaba todos los chequeos en verde.
--
-- Por eso el bloque de permisos de abajo **REVOCA primero y otorga después**.
-- No describe el alcance: lo IMPONE. Correr este archivo deja los roles
-- exactamente en lo declarado acá, y un permiso de más otorgado a mano se
-- deshace solo en el próximo deploy.
--
-- CÓMO SE APLICA
-- ==============
-- Lo aplica `scripts/apply_schema.py` DESPUÉS de `sql/schema.sql`, y un fallo
-- acá **avisa en vez de cortar**: el lab es opcional, la mesa no. Es la misma
-- decisión que ya rige del otro lado (el agente sobrevive a un lab roto).
--
-- ⚠️ **TODOS LOS PERMISOS DEL LAB VIVEN ACÁ. TODOS.** Las tres vistas de
-- `agente` (`v_ahora`, `v_encontro`, `v_habilidades`) incluidas, aunque
-- `schema.sql` sea quien las crea. El motivo lo enseñó un deploy real: el
-- `REVOKE ALL ON ALL TABLES` de abajo **incluye las vistas**, y este archivo
-- corre DESPUÉS de `schema.sql` — así que un grant puesto allá lo deshacía este
-- de acá, cada vez. Un permiso otorgado en dos archivos donde uno pisa al otro
-- es la REGLA #9 con la peor forma posible: los dos bloques son correctos por
-- separado y el resultado está mal.
--
-- EL INVARIANTE QUE ESTO NECESITA: `apply_schema` aplica este archivo DESPUÉS
-- de `sql/schema.sql`. Lo congela `tests/unit/test_lab.py`, porque al revés las
-- vistas todavía no existirían y el lector quedaría sin ellas.
--
-- LOS ROLES NO SE CREAN ACÁ
-- =========================
-- `CREATE ROLE` lleva contraseña, y una contraseña no va a un repo. Los dos
-- roles se crean una vez a mano (ver `docs/AGENT.md` §L) y desde entonces
-- **sus permisos los manda este archivo**. Todo lo que los nombra va adentro de
-- un `DO` con guarda: en una base donde el lab no está instalado, los roles no
-- existen y un `GRANT` suelto cortaría el deploy de toda la plataforma.


-- ══════════════════════════════════════════════════════════════════════════
-- 1. EL ESQUEMA DEL LAB
-- ══════════════════════════════════════════════════════════════════════════

CREATE SCHEMA IF NOT EXISTS lab;


-- ── EL DIARIO: las investigaciones hechas ─────────────────────────────────
--
-- Una fila = UNA investigación hecha, con fecha. No «lo que sabemos de M31G6»
-- —eso se pudre—: un hecho fechado, como `agente.acciones`. Investigar lo mismo
-- mañana crea OTRA fila, no un update.
--
-- Es **append-only por permisos**, no por disciplina: `escritor_lab` tiene
-- INSERT y no tiene UPDATE ni DELETE. Un libro que se puede reescribir no es
-- un libro.
CREATE TABLE IF NOT EXISTS lab.investigaciones (
    id                    bigserial PRIMARY KEY,
    at                    timestamptz NOT NULL DEFAULT now(),

    -- LA IDENTIDAD. Misma idea que el trío (habilidad, sujeto, regla) del AV
    -- AGENT: sin identidad, dos filas sobre lo mismo no se juntan.
    tipo                  text NOT NULL,
    caso                  text NOT NULL,

    -- EL VEREDICTO
    titulo                text NOT NULL DEFAULT '',
    de_quien_es           text NOT NULL,
    -- ⚠️ ARREGLOS, no texto: un campo que ENUMERA cosas (una cronología, tres
    -- acciones, cuatro dudas) en un `text` vuelve un párrafo de ochenta
    -- palabras que nadie lee. El tipo es la regla.
    que_paso              text[] NOT NULL,
    por_que               text[] NOT NULL,
    que_haria             text[] NOT NULL,
    lo_que_no_se          text[] NOT NULL,
    de_donde              text[] NOT NULL,

    -- LA CALIDAD DE LA CORRIDA. Hechos sobre la corrida, no opiniones del
    -- modelo: el invariante #12 (el agente no se autoevalúa) sigue intacto.
    herramientas_usadas   text[] NOT NULL DEFAULT '{}',
    piso_cubierto         boolean NOT NULL,
    corto_por_presupuesto boolean NOT NULL,
    vueltas               integer NOT NULL,

    modelo                text NOT NULL DEFAULT '',
    por                   text NOT NULL DEFAULT '',

    CONSTRAINT inv_de_quien CHECK (
        de_quien_es IN ('nuestro','dato','proveedor','no_se')),
    CONSTRAINT inv_que_paso  CHECK (cardinality(que_paso) > 0),
    CONSTRAINT inv_que_haria CHECK (cardinality(que_haria) > 0),
    CONSTRAINT inv_no_se     CHECK (cardinality(lo_que_no_se) > 0),
    -- ⚠️ `cardinality`, NO `array_length`. `array_length('{}', 1)` devuelve
    -- **NULL**, y un CHECK que da NULL PASA —sólo falla con FALSE—, así que la
    -- restricción era decorativa justo para el caso que venía a atajar: un
    -- veredicto sin una sola fuente entraba igual. `cardinality` da 0.
    CONSTRAINT inv_fuentes   CHECK (cardinality(de_donde) > 0)
);

CREATE INDEX IF NOT EXISTS investigaciones_caso
    ON lab.investigaciones (tipo, caso, at DESC);
CREATE INDEX IF NOT EXISTS investigaciones_recientes
    ON lab.investigaciones (at DESC);


-- ── LA COLA: los pedidos de investigación ─────────────────────────────────
--
-- Una fila = un PEDIDO con su estado, no un resultado. El resultado vive en
-- `lab.investigaciones` y acá queda apuntado con su id. Son dos cosas
-- distintas: el pedido se puede cancelar, fallar o repetirse; la investigación,
-- una vez hecha, es un hecho.
CREATE TABLE IF NOT EXISTS lab.pedidos (
    id               bigserial PRIMARY KEY,
    at               timestamptz NOT NULL DEFAULT now(),
    tipo             text NOT NULL,
    caso             text NOT NULL,
    por              text NOT NULL DEFAULT '',

    estado           text NOT NULL DEFAULT 'pendiente',
    arrancado_at     timestamptz,
    terminado_at     timestamptz,

    -- Los pasos EN VIVO. Se van agregando MIENTRAS corre — es lo que deja ver
    -- qué herramienta está usando ahora mismo, y sin eso, cuando contesta mal,
    -- no se puede distinguir si eligió mal la herramienta, si la herramienta
    -- trajo basura, o si razonó mal.
    pasos            jsonb NOT NULL DEFAULT '[]'::jsonb,

    investigacion_id bigint REFERENCES lab.investigaciones(id),
    -- QUÉ hallazgo del agente lo disparó, cuando lo pidió el TRIAGE y no una
    -- persona. Sin FK a propósito: `agente.hallazgos` es del OTRO esquema y el
    -- lector del lab no tiene por qué poder bloquearlo. Es un puntero para
    -- poder mostrar el veredicto AL LADO del problema — un veredicto que vive
    -- en otra tab no lo lee nadie.
    hallazgo_id      bigint,
    error            text NOT NULL DEFAULT '',

    CONSTRAINT pedidos_estado_ok
        CHECK (estado IN ('pendiente','corriendo','listo','error'))
);

ALTER TABLE lab.pedidos ADD COLUMN IF NOT EXISTS hallazgo_id bigint;

CREATE INDEX IF NOT EXISTS pedidos_cola ON lab.pedidos (estado, at);
CREATE INDEX IF NOT EXISTS pedidos_recientes ON lab.pedidos (at DESC);


-- ── MIGRACIÓN: los campos que pasaron de `text` a `text[]` ────────────────
--
-- ⚠️⚠️ **SIN USAR `ALTER ... TYPE ... USING`.** El camino corto era
-- `ALTER COLUMN x TYPE text[] USING ARRAY[x]`, y falló con **«function
-- btrim(text[]) does not exist»** sobre una columna que era `text`: adentro del
-- USING la referencia se resolvió con el tipo NUEVO. No se sabe exactamente por
-- qué, así que en vez de adivinar se evita el constructo.
--
-- Columna nueva → copiar → borrar la vieja → renombrar. Cuatro pasos que no
-- dependen de cómo se resuelve nada, y el `IF t = 'text'` los saltea enteros si
-- ya se corrió antes. El tipo se pregunta al CATÁLOGO (`atttypid::regtype`),
-- que devuelve exactamente `text` o `text[]` — `information_schema` no sirve.
DO $mig$
DECLARE c text; t text;
BEGIN
  FOREACH c IN ARRAY ARRAY['que_paso','por_que','que_haria','lo_que_no_se'] LOOP
    SELECT a.atttypid::regtype::text INTO t
      FROM pg_attribute a
     WHERE a.attrelid = 'lab.investigaciones'::regclass
       AND a.attname = c AND NOT a.attisdropped;
    IF t = 'text' THEN
      EXECUTE format('ALTER TABLE lab.investigaciones ADD COLUMN %I text[]',
                     c || '_arr');
      EXECUTE format(
        'UPDATE lab.investigaciones SET %I = CASE WHEN btrim(%I) = %L '
        'THEN ARRAY[]::text[] ELSE ARRAY[%I] END', c || '_arr', c, '', c);
      EXECUTE format('ALTER TABLE lab.investigaciones DROP COLUMN %I', c);
      EXECUTE format('ALTER TABLE lab.investigaciones RENAME COLUMN %I TO %I',
                     c || '_arr', c);
      EXECUTE format('ALTER TABLE lab.investigaciones ALTER COLUMN %I SET NOT NULL', c);
    END IF;
  END LOOP;
END
$mig$;

ALTER TABLE lab.investigaciones ADD COLUMN IF NOT EXISTS titulo text NOT NULL DEFAULT '';

-- ⚠️⚠️ **UNA FILA VIEJA VACÍA IMPIDE CREAR EL CHECK — Y EL SÍNTOMA ES QUE NO
-- HAY CHECK.** Medido contra una base de prueba con la tabla en la forma vieja:
-- el paso de arriba convierte un `''` en `ARRAY[]::text[]`, y entonces
-- `ADD CONSTRAINT ... cardinality(...) > 0` falla con «is violated by some
-- row». Como los errores del lab AVISAN sin cortar (ver `apply_schema`), el
-- deploy seguiría en verde y **la restricción simplemente no existiría**: la
-- misma falla decorativa del `array_length` que esto vino a arreglar, por otra
-- puerta.
--
-- El arreglo NO es saltear el CHECK ni borrar la fila: es **rellenar lo que
-- estaba vacío con una frase que dice por qué está vacío**. La fila se
-- conserva, la restricción se crea de verdad, y el que lee entiende qué pasó en
-- vez de encontrar un campo mudo.
UPDATE lab.investigaciones SET que_paso = ARRAY['(la versión vieja no lo guardaba)']
 WHERE cardinality(que_paso) = 0;
UPDATE lab.investigaciones SET que_haria = ARRAY['(la versión vieja no lo guardaba)']
 WHERE cardinality(que_haria) = 0;
UPDATE lab.investigaciones SET lo_que_no_se = ARRAY['(la versión vieja no lo guardaba)']
 WHERE cardinality(lo_que_no_se) = 0;
UPDATE lab.investigaciones SET de_donde = ARRAY['(la versión vieja no lo guardaba)']
 WHERE cardinality(de_donde) = 0;

-- ⚠️ **UN CHECK YA CREADO NO SE ACTUALIZA SOLO.** `CREATE TABLE IF NOT EXISTS`
-- no toca la tabla que ya existe. Si la tabla nació con el CHECK viejo (el de
-- `array_length`), esto lo reemplaza; en una tabla recién creada no hace nada.
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_que_paso;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_que_paso
    CHECK (cardinality(que_paso) > 0);
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_que_haria;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_que_haria
    CHECK (cardinality(que_haria) > 0);
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_no_se;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_no_se
    CHECK (cardinality(lo_que_no_se) > 0);
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_fuentes;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_fuentes
    CHECK (cardinality(de_donde) > 0);


-- ══════════════════════════════════════════════════════════════════════════
-- 2. EL ALCANCE — revocar primero, otorgar después
-- ══════════════════════════════════════════════════════════════════════════
--
-- ⚠️ **EL ORDEN ES LA GARANTÍA.** Si sólo otorgáramos, este archivo diría lo
-- que el lab PUEDE leer sin decir nada de lo que NO puede: un permiso de más,
-- puesto a mano cualquier martes, sobreviviría para siempre y ningún chequeo lo
-- vería. Revocando todo primero, lo declarado acá es el alcance COMPLETO.
--
-- El REVOKE recorre los esquemas del catálogo en vez de nombrarlos: un esquema
-- nuevo (que hoy no existe) quedaría fuera de una lista escrita a mano, y ese
-- es exactamente el agujero que esto viene a tapar.
DO $permisos$
DECLARE
    r text;
    s text;
BEGIN
    FOREACH r IN ARRAY ARRAY['lector_lab', 'escritor_lab'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            RAISE NOTICE 'lab: el rol % no existe — salteo sus permisos', r;
            CONTINUE;
        END IF;

        -- (a) TABULA RASA. Todo lo que tenga, en cualquier esquema que no sea
        --     del sistema, se le saca. Después se le devuelve sólo lo listado.
        FOR s IN
            SELECT nspname FROM pg_namespace
             WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema'
        LOOP
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM %I', s, r);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM %I', s, r);
            EXECUTE format('REVOKE ALL ON SCHEMA %I FROM %I', s, r);
        END LOOP;
    END LOOP;

    -- ── (b) EL LECTOR ──────────────────────────────────────────────────────
    -- Lee producción, y **la base no lo deja escribir**. Estas seis tablas son
    -- las que consultan las herramientas de `lab/langgraph/datos.py`; si algún
    -- día una herramienta nueva necesita otra, se agrega ACÁ y en ningún otro
    -- lado — si no, vuelve a haber un alcance que sólo la base conoce.
    --
    -- ⚠️⚠️ **TABLA POR TABLA, Y CADA UNA CON SU GUARDA.** El camino corto era
    -- un `GRANT USAGE ON SCHEMA mercado, agente, manager, lab` seguido de los
    -- seis GRANT sueltos, y tiene un modo de falla feo que apareció probándolo:
    -- si UNO de esos objetos no existe, el bloque entero aborta — pero el
    -- REVOKE de arriba YA CORRIÓ. O sea que el lector se queda **sin ningún
    -- permiso**, el investigador deja de funcionar, y el motivo es una línea de
    -- error en la salida del deploy que nadie lee después.
    --
    -- Así, lo que falta se canta por nombre y lo demás se otorga igual. Que el
    -- lab quede a medias es malo; que quede mudo sin decir por qué es peor.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_lab') THEN
        FOREACH s IN ARRAY ARRAY['mercado', 'agente', 'manager', 'lab'] LOOP
            IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s) THEN
                EXECUTE format('GRANT USAGE ON SCHEMA %I TO lector_lab', s);
            ELSE
                RAISE WARNING 'lab: no existe el esquema % — el lector no va a poder leerlo', s;
            END IF;
        END LOOP;

        FOREACH r IN ARRAY ARRAY[
                'mercado.curvas',           -- ficha_del_bono
                'mercado.market_snapshot',  -- precio_del_simbolo
                'agente.hallazgos',         -- hallazgos_del_sujeto
                'agente.reincidencias',     -- reincidencias
                'agente.acciones',          -- acciones_sobre
                'manager.job_runs',         -- corridas_del_job
                -- El diario, para no repetir una investigación ya hecha, y la
                -- cola, para que la pantalla vea en qué anda.
                'lab.investigaciones',
                'lab.pedidos',
                -- ⚠️⚠️ **LAS TRES VISTAS VAN ACÁ, Y ANTES NO.** Su GRANT vivía
                -- en `sql/schema.sql`, pegado al CREATE, porque cada
                -- `apply_schema` las DROPEA y las vuelve a crear y un permiso
                -- cuelga del OBJETO, no del nombre. El razonamiento era
                -- correcto y aun así quedó mal: **`REVOKE ALL ON ALL TABLES`
                -- incluye las VISTAS**, y este archivo corre DESPUÉS de
                -- `schema.sql` — o sea que el revoke de arriba deshacía, cada
                -- deploy, el grant que `schema.sql` acababa de dar.
                --
                -- Se detectó en producción, no razonándolo: `probar_lector`
                -- cantó «permission denied for view v_ahora» justo después del
                -- primer deploy. No lo agarró la prueba en sandbox porque ahí
                -- las vistas nunca se crearon.
                --
                -- Por eso ahora los permisos del lab viven en UN solo archivo:
                -- el que corre último. El invariante que lo sostiene —que
                -- `apply_schema` aplique este archivo DESPUÉS de `schema.sql`—
                -- lo congela `tests/unit/test_lab.py`.
                'agente.v_ahora',
                'agente.v_encontro',
                'agente.v_habilidades']
        LOOP
            IF to_regclass(r) IS NOT NULL THEN
                EXECUTE format('GRANT SELECT ON %s TO lector_lab', r);
            ELSE
                RAISE WARNING 'lab: no existe % — el lector queda sin esa herramienta', r;
            END IF;
        END LOOP;
    END IF;

    -- ── (c) EL ESCRITOR ────────────────────────────────────────────────────
    -- Escribe SÓLO en el esquema `lab`. Producción le sigue siendo invisible —
    -- no de solo lectura: INVISIBLE, porque el REVOKE de arriba no le devolvió
    -- ni el USAGE sobre los esquemas de producción.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'escritor_lab') THEN
        GRANT USAGE ON SCHEMA lab TO escritor_lab;

        -- El diario es un LIBRO: INSERT y SELECT, sin UPDATE ni DELETE.
        GRANT SELECT, INSERT ON lab.investigaciones TO escritor_lab;
        GRANT USAGE, SELECT ON SEQUENCE lab.investigaciones_id_seq TO escritor_lab;

        -- La cola SÍ lleva UPDATE: un pedido cambia de estado y acumula pasos
        -- mientras corre. Es lo contrario del diario, y a propósito.
        GRANT SELECT, INSERT, UPDATE ON lab.pedidos TO escritor_lab;
        GRANT USAGE, SELECT ON SEQUENCE lab.pedidos_id_seq TO escritor_lab;
    END IF;
END
$permisos$;


-- ══════════════════════════════════════════════════════════════════════════
-- 3. RLS — las políticas van al lado del CREATE, no en un paso aparte
-- ══════════════════════════════════════════════════════════════════════════
--
-- ⚠️ SUPABASE PRENDE RLS SOLO EN LAS TABLAS NUEVAS, y sin política no entra ni
-- sale nada: el INSERT muere con «new row violates row-level security policy» y
-- el SELECT devuelve **CERO FILAS SIN ERROR** — que es peor, porque «no hay
-- nada» y «no puedo mirar» se ven iguales. Pasó primero con todo `mercado` y
-- después con estas tablas. Un esquema al que hay que acordarse de agregarle
-- algo después no es un esquema.
--
-- `DROP ... IF EXISTS` antes de crear porque `CREATE POLICY` no acepta
-- `IF NOT EXISTS`: sin eso, correr este archivo dos veces falla.
DO $rls$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'escritor_lab') THEN
        DROP POLICY IF EXISTS escritor_lab_inserta ON lab.investigaciones;
        CREATE POLICY escritor_lab_inserta ON lab.investigaciones
            FOR INSERT TO escritor_lab WITH CHECK (true);
        DROP POLICY IF EXISTS escritor_lab_lee ON lab.investigaciones;
        CREATE POLICY escritor_lab_lee ON lab.investigaciones
            FOR SELECT TO escritor_lab USING (true);

        DROP POLICY IF EXISTS escritor_lab_pedidos ON lab.pedidos;
        CREATE POLICY escritor_lab_pedidos ON lab.pedidos
            FOR ALL TO escritor_lab USING (true) WITH CHECK (true);
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lector_lab') THEN
        DROP POLICY IF EXISTS lector_lab_lee ON lab.investigaciones;
        CREATE POLICY lector_lab_lee ON lab.investigaciones
            FOR SELECT TO lector_lab USING (true);
        DROP POLICY IF EXISTS lector_lab_pedidos ON lab.pedidos;
        CREATE POLICY lector_lab_pedidos ON lab.pedidos
            FOR SELECT TO lector_lab USING (true);
    END IF;
END
$rls$;
