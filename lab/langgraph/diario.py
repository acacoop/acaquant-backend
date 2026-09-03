"""`lab/langgraph/diario.py` — EL LIBRO DE INVESTIGACIONES.

QUÉ ES UNA FILA
===============

**Una investigación hecha, con fecha.** No «lo que sabemos de M31G6» —eso se
pudre—: un hecho fechado, como `agente.acciones`. Investigar lo mismo mañana
crea OTRA fila, no un update. Así se puede ver que el 3/9 se concluyó una cosa
y el 10/9 otra, que es justamente lo interesante.

Es **append-only por permisos**, no por disciplina: `escritor_lab` tiene INSERT
y no tiene UPDATE ni DELETE. Un libro que se puede reescribir no es un libro.

LA IDENTIDAD ES `(tipo, caso)`
==============================

Misma idea que el trío `(habilidad, sujeto, regla)` del AV AGENT: sin una
identidad, dos filas sobre lo mismo no se juntan y la tabla no sirve para
buscar nada.

POR QUÉ NO ES UN BLOB
=====================

Lo que se filtra va en COLUMNA. `de_quien_es` con su CHECK, y sobre todo las
tres de la CALIDAD DE LA CORRIDA —`piso_cubierto`, `corto_por_presupuesto`,
`vueltas`— que dejan preguntar «¿cuáles investigaciones fueron flojas?» sin que
el modelo opine de sí mismo: son hechos sobre la corrida, igual que
`habilidades.ultimo_resultado`. El invariante #12 sigue intacto.

Y los CHECK son los que evitan el cementerio: **un veredicto sin fuentes no
entra**, igual que un hallazgo sin `que_hacer`.
"""
from __future__ import annotations

import logging

from lab.langgraph.base import escribir, leer

logger = logging.getLogger(__name__)

# El esquema vive ACÁ, al lado de las queries que lo usan. Separarlos es cómo se
# llega a una tabla que la mitad del código cree que tiene una columna que no
# tiene. Se aplica a mano una vez (`--sql` lo imprime).
SQL_ESQUEMA = """
CREATE SCHEMA IF NOT EXISTS lab;

CREATE TABLE IF NOT EXISTS lab.investigaciones (
    id                    bigserial PRIMARY KEY,
    at                    timestamptz NOT NULL DEFAULT now(),

    -- LA IDENTIDAD
    tipo                  text NOT NULL,
    caso                  text NOT NULL,

    -- EL VEREDICTO
    titulo                text NOT NULL DEFAULT '',
    de_quien_es           text NOT NULL,
    -- ⚠️ ARREGLOS, no texto: un campo que ENUMERA cosas (una cronologia, tres
    -- acciones, cuatro dudas) en un `text` vuelve un parrafo de ochenta
    -- palabras que nadie lee. El tipo es la regla.
    que_paso              text[] NOT NULL,
    por_que               text[] NOT NULL,
    que_haria             text[] NOT NULL,
    lo_que_no_se          text[] NOT NULL,
    de_donde              text[] NOT NULL,

    -- LA CALIDAD DE LA CORRIDA. Hechos, no opiniones del modelo.
    herramientas_usadas   text[] NOT NULL DEFAULT '{}',
    piso_cubierto         boolean NOT NULL,
    corto_por_presupuesto boolean NOT NULL,
    vueltas               integer NOT NULL,

    modelo                text NOT NULL DEFAULT '',
    por                   text NOT NULL DEFAULT '',

    -- Sin esto no entra. Es lo que impide que en dos meses la mitad de las
    -- filas sean prosa vacia.
    CONSTRAINT inv_de_quien CHECK (
        de_quien_es IN ('nuestro','dato','proveedor','no_se')),
    CONSTRAINT inv_que_paso  CHECK (cardinality(que_paso) > 0),
    CONSTRAINT inv_que_haria CHECK (cardinality(que_haria) > 0),
    CONSTRAINT inv_no_se     CHECK (cardinality(lo_que_no_se) > 0),
    -- ⚠️ `cardinality`, NO `array_length`. `array_length('{}', 1)` devuelve
    -- **NULL**, y un CHECK que da NULL PASA —solo falla con FALSE—, asi que
    -- la restriccion era decorativa justo para el caso que venia a atajar:
    -- un veredicto sin una sola fuente entraba igual. `cardinality` da 0.
    CONSTRAINT inv_fuentes   CHECK (cardinality(de_donde) > 0)
);

CREATE INDEX IF NOT EXISTS investigaciones_caso
    ON lab.investigaciones (tipo, caso, at DESC);
CREATE INDEX IF NOT EXISTS investigaciones_recientes
    ON lab.investigaciones (at DESC);

-- El LECTOR puede leer el diario (para no repetir una investigación).
GRANT USAGE ON SCHEMA lab TO lector_lab;
GRANT SELECT ON lab.investigaciones TO lector_lab;

-- El ESCRITOR sólo escribe acá. Sin UPDATE ni DELETE: es un libro.
GRANT USAGE ON SCHEMA lab TO escritor_lab;
GRANT SELECT, INSERT ON lab.investigaciones TO escritor_lab;
GRANT USAGE, SELECT ON SEQUENCE lab.investigaciones_id_seq TO escritor_lab;

-- ⚠️ SUPABASE PRENDE RLS SOLO EN LAS TABLAS NUEVAS, y sin politica no entra ni
-- sale nada: el INSERT muere con «new row violates row-level security policy» y
-- el SELECT devuelve CERO FILAS SIN ERROR — que es peor, porque «no hay nada» y
-- «no puedo mirar» se ven iguales. Paso primero con todo `mercado` y despues
-- con esta tabla. Por eso las politicas viven ACA, al lado del CREATE: un
-- esquema al que hay que acordarse de agregarle algo despues no es un esquema.
--
-- `DROP ... IF EXISTS` antes de crear porque CREATE POLICY no acepta IF NOT
-- EXISTS: sin eso, correr este bloque dos veces falla.
-- ⚠️ MIGRACION DE LOS CAMPOS QUE PASARON DE `text` A `text[]`.
-- Convierte lo que ya hay en un arreglo de UN elemento: no se pierde nada, y
-- las filas viejas se ven como un solo punto largo — que es exactamente lo que
-- eran. Va adentro de un DO porque `ALTER ... TYPE` falla si el tipo ya es el
-- nuevo, y este bloque se corre mas de una vez.
DO $mig$
DECLARE c text;
BEGIN
  FOREACH c IN ARRAY ARRAY['que_paso','por_que','que_haria','lo_que_no_se'] LOOP
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'lab' AND table_name = 'investigaciones'
                  AND column_name = c AND data_type <> 'ARRAY') THEN
      EXECUTE format(
        'ALTER TABLE lab.investigaciones ALTER COLUMN %I TYPE text[] '
        'USING CASE WHEN btrim(%I) = %L THEN ARRAY[]::text[] ELSE ARRAY[%I] END',
        c, c, '', c);
    END IF;
  END LOOP;
END
$mig$;

ALTER TABLE lab.investigaciones ADD COLUMN IF NOT EXISTS titulo text NOT NULL DEFAULT '';

ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_que_paso;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_que_paso
    CHECK (cardinality(que_paso) > 0);
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_que_haria;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_que_haria
    CHECK (cardinality(que_haria) > 0);
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_no_se;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_no_se
    CHECK (cardinality(lo_que_no_se) > 0);

-- Si la tabla ya existia con el CHECK viejo (el de array_length), esto lo
-- reemplaza. Es idempotente: en una tabla recien creada no hace nada.
ALTER TABLE lab.investigaciones DROP CONSTRAINT IF EXISTS inv_fuentes;
ALTER TABLE lab.investigaciones ADD CONSTRAINT inv_fuentes
    CHECK (cardinality(de_donde) > 0);

DROP POLICY IF EXISTS escritor_lab_inserta ON lab.investigaciones;
CREATE POLICY escritor_lab_inserta ON lab.investigaciones
    FOR INSERT TO escritor_lab WITH CHECK (true);

DROP POLICY IF EXISTS escritor_lab_lee ON lab.investigaciones;
CREATE POLICY escritor_lab_lee ON lab.investigaciones
    FOR SELECT TO escritor_lab USING (true);

DROP POLICY IF EXISTS lector_lab_lee ON lab.investigaciones;
CREATE POLICY lector_lab_lee ON lab.investigaciones
    FOR SELECT TO lector_lab USING (true);
"""

# Los campos del veredicto que van a la base, EN EL ORDEN del INSERT. Se
# derivan del modelo para que agregar un campo arriba no obligue a acordarse
# de dos listas.
_CAMPOS = ("titulo", "de_quien_es", "que_paso", "por_que", "que_haria",
           "lo_que_no_se", "de_donde")


def guardar(tipo: str, caso: str, veredicto: dict, *, herramientas: list[str],
            piso_cubierto: bool, corto: bool, vueltas: int,
            modelo: str = "", por: str = "") -> dict:
    """Anota la investigación. **Nunca levanta hacia el que la llamó.**

    Que no se pueda guardar no invalida lo que se averiguó: la investigación
    igual sirve. Pero el fallo se DICE —vuelve en `error`— porque tragárselo
    dejaría creer que quedó registrada.
    """
    if veredicto is None:
        return {"ok": False, "error": "no hay veredicto que guardar"}
    try:
        from lab.langgraph.veredicto import LISTAS
        # Una lista que llega vacía se guarda vacía y el CHECK la rechaza: es
        # lo que queremos. Lo que NO puede pasar es mandar `None` a una columna
        # NOT NULL y que el error hable de otra cosa.
        valores = [list(veredicto.get(c) or []) if c in LISTAS
                   else (veredicto.get(c) or "") for c in _CAMPOS]
        f = escribir(
            "INSERT INTO lab.investigaciones "
            " (tipo, caso, titulo, de_quien_es, que_paso, por_que, que_haria, "
            "  lo_que_no_se, de_donde, herramientas_usadas, piso_cubierto, "
            "  corto_por_presupuesto, vueltas, modelo, por) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (tipo, caso, *valores, list(herramientas),
             piso_cubierto, corto, int(vueltas), modelo, por))
        return {"ok": True, "id": f[0] if f else None}
    except Exception as e:
        logger.warning("diario: no pude guardar (%s)", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def anteriores(tipo: str, caso: str, limite: int = 3) -> list[dict]:
    """Lo que YA se investigó sobre este mismo caso. Vacío si no hay o si no se
    pudo leer — y esa diferencia NO se puede tapar acá, así que el que llama
    recibe una lista y no una afirmación de que no existe nada."""
    r = leer(
        "SELECT id, to_char(at,'YYYY-MM-DD HH24:MI') AS cuando, de_quien_es, "
        "       titulo, que_haria, lo_que_no_se, piso_cubierto, "
        "       corto_por_presupuesto "
        "  FROM lab.investigaciones WHERE tipo = %s AND upper(caso) = upper(%s) "
        " ORDER BY at DESC LIMIT %s", (tipo, caso, int(limite)))
    if isinstance(r, str):
        logger.info("diario: sin antecedentes (%s)", r)
        return []
    cols, filas = r
    return [dict(zip(cols, f, strict=True)) for f in filas]


def historial(tipo: str = "", caso: str = "", limite: int = 20) -> str:
    """El libro, para mirarlo desde la terminal."""
    cond, params = [], []
    if tipo:
        cond.append("tipo = %s")
        params.append(tipo)
    if caso:
        cond.append("upper(caso) = upper(%s)")
        params.append(caso)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    r = leer("SELECT to_char(at,'YYYY-MM-DD HH24:MI') AS cuando, tipo, caso, "
             "       de_quien_es, piso_cubierto, corto_por_presupuesto, "
             "       vueltas, que_haria "
             f"  FROM lab.investigaciones{where} ORDER BY at DESC LIMIT %s",
             (*params, int(limite)))
    if isinstance(r, str):
        return r
    _cols, filas = r
    if not filas:
        return "todavía no hay investigaciones guardadas."
    out = [f"{'CUÁNDO':17} {'TIPO':14} {'CASO':12} {'DE QUIÉN':10} PISO CORTE VUELTAS"]
    for f in filas:
        out.append(f"{f[0]:17} {f[1]:14} {f[2]:12} {f[3]:10} "
                   f"{'sí' if f[4] else 'NO':4} {'sí' if f[5] else 'no':5} {f[6]:>7}")
        out.append(f"      → {' '.join(str(f[7]).split())[:100]}")
    return "\n".join(out)
