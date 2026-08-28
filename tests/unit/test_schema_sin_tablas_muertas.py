"""El `sql/schema.sql` no declara tablas que no usa nadie.

Regla del user, 2026-08-28: *«el SCHEMA no tiene que tener tablas que no se usen,
es fácil»*. Es fácil de decir y no se cumple sola, así que vive acá.

## Por qué es un test y no una buena intención

Una declaración que sobra **no molesta hasta que sí**, y cuando molesta lo hace en
silencio. El 2026-08-28 se dropearon las 18 tablas del agente viejo sin sacar sus
`CREATE TABLE IF NOT EXISTS` de este archivo: **el `apply_schema` del deploy
siguiente las recreó las 18**, con los dos `INSERT` que siembran una fila, así que
hasta volvieron con datos. `IF NOT EXISTS` no da error, el deploy salió verde, y
las tablas estaban de nuevo. Nadie lo iba a notar.

Ese día también salieron `operaciones.accounts_descubiertas`, `manager.db_tamano`
y `manager.db_tamano_dia` (las reemplazó `agente.db_peso`) y
`operaciones.triggers_mep`. Medido: quedaron **204 declaradas y cero sin uso**.

## Qué cuenta como «usarla»

Que ALGÚN archivo de `api/ · agente/ · jobs/ · core/ · engines/ · quant/ ·
scripts/` la nombre — calificada (`manager.role_matrix`) o pelada entre comillas
(`_JOBS_TABLE = "aranceles_job_runs"`, que es como la escribe medio repo).

⚠️ **`sql/` NO cuenta**, y es la trampa de este test: `git grep manager.db_tamano`
encuentra su propio `CREATE TABLE`, con lo cual toda declaración se justificaría a
sí misma y el test pasaría siempre. Medido: contando `sql/`, las 3 muertas daban
«1 uso» cada una.

⚠️ **`scripts/` SÍ cuenta acá**, al revés que en `diag_tablas_muertas`. No es
inconsistencia: son preguntas distintas. Allá se pregunta «¿la puedo BORRAR?» y un
script puede nombrar una tabla justamente para borrarla. Acá se pregunta «¿esta
declaración se justifica?», y un backfill que la llena la justifica
(`mercado.precios_extremos_hist` ← `scripts/backfill_extremos_hist.py`).
"""
from __future__ import annotations

import pathlib
import re

RAIZ = pathlib.Path(__file__).resolve().parents[2]

# De dónde puede venir la justificación. `sql/` NO — ver el docstring.
CARPETAS = ("api", "agente", "jobs", "core", "engines", "quant", "scripts")

# Declaraciones sin uso que se aceptan igual. Vacío a propósito: agregar una es
# una decisión consciente y lleva el motivo al lado, no un olvido que pasa.
SIN_USO_JUSTIFICADO: dict[str, str] = {}

_RE_CREA = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_][a-z_0-9]*)\.([a-z_0-9]+)", re.I)


def _todo_el_codigo() -> str:
    """Todos los `.py` de las carpetas, en UN solo string.

    Leídos una vez y buscados en memoria: 204 tablas × 2 patrones serían 408
    `git grep`, y un test que tarda medio minuto se termina salteando.
    """
    partes = []
    for c in CARPETAS:
        for f in sorted((RAIZ / c).rglob("*.py")):
            partes.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(partes)


def test_el_schema_no_declara_tablas_que_no_usa_nadie():
    schema = (RAIZ / "sql" / "schema.sql").read_text(encoding="utf-8")
    declaradas = sorted({f"{a}.{b}".lower() for a, b in _RE_CREA.findall(schema)})
    assert len(declaradas) > 150, "no leí el schema entero, algo se rompió antes"

    codigo = _todo_el_codigo()
    huerfanas = []
    for t in declaradas:
        if t in SIN_USO_JUSTIFICADO:
            continue
        pelado = t.split(".", 1)[1]
        if t in codigo or f'"{pelado}"' in codigo or f"'{pelado}'" in codigo:
            continue
        huerfanas.append(t)

    assert not huerfanas, (
        "sql/schema.sql declara tablas que no usa nadie:\n  "
        + "\n  ".join(huerfanas)
        + "\n\nSacá su bloque del schema (y dropealas con "
          "`python -m scripts.diag_tablas_muertas --aplicar`), o si tienen que "
          "quedarse, agregalas a SIN_USO_JUSTIFICADO con el motivo."
    )
