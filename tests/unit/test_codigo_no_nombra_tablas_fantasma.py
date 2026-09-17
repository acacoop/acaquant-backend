"""Toda tabla que el código nombra está declarada en `sql/schema.sql`.

Es la contracara de `test_schema_sin_tablas_muertas.py`: aquel vigila que el
schema no declare lo que nadie usa; este, que el código no use lo que el schema
no declara. Las dos direcciones hacen falta para que `schema.sql` sea la
fuente de verdad que dice ser.

## Por qué es un test

Una query contra una tabla que no está en el schema **no falla hasta que sí**:
funciona en el Droplet porque alguien la creó a mano o porque un service la crea
al vuelo con `CREATE TABLE IF NOT EXISTS`, y el día que se levanta la base desde
el archivo (`scripts/apply_schema.py`, una réplica, un entorno nuevo) esa tabla
no existe, la query tira y nadie sabe por qué: el archivo decía que estaba todo.
Medido al escribir esto: 178 tablas calificadas nombradas por el código y UNA
que no estaba (`mercado.agro_pizarra_audit`, que el service creaba inline). Se
declaró y el inline se sacó — la regla es que la declaración vive en un solo
lugar (REGLA #9 B).

## Qué se mira

`schema.tabla` calificada detrás de `FROM` / `JOIN` / `INTO` / `UPDATE` /
`DELETE FROM` escritos en MAYÚSCULA (así un `from x import y` de Python no
entra), y solo si el schema es uno de los declarados (`core.schema_sql.schemas()`):
un alias como `a.sujeto` no es una tabla. Las vistas cuentan como declaradas
(`core.schema_sql.vistas()`).

Lo que NO ve: una tabla armada por f-string (`FROM {_SRC}`) o un nombre pelado
que confía en el `search_path`. Es una red de mínima, no una prueba de
completitud — y con esa mínima ya cazó una.
"""
from __future__ import annotations

import pathlib
import re

from core import schema_sql

RAIZ = pathlib.Path(__file__).resolve().parents[2]

# Dónde puede haber SQL. `scripts/` NO: un script puede nombrar una tabla para
# dropearla o para migrar de una vieja, y eso es legítimo.
CARPETAS = ("api", "core", "jobs", "agente", "engines", "quant", "asistente")

# Sensible a mayúsculas A PROPÓSITO: la palabra clave SQL va en mayúscula en todo
# el repo y el `from` de Python en minúscula. Sin esto, cada import se lee como
# una tabla `paquete.modulo`.
_RE_TABLA = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]\w*\.[a-z_]\w*)\b")

# Tablas que el código nombra y el schema NO declara, aceptadas con motivo.
# Vacío a propósito: agregar una es una decisión consciente, no un olvido.
FANTASMAS_JUSTIFICADOS: dict[str, str] = {}


def _referencias() -> dict[str, set[str]]:
    """`schema.tabla` → archivos que la nombran (solo schemas nuestros)."""
    nuestros = schema_sql.schemas()
    out: dict[str, set[str]] = {}
    for c in CARPETAS:
        for f in sorted((RAIZ / c).rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            for m in _RE_TABLA.findall(f.read_text(encoding="utf-8", errors="ignore")):
                if m.split(".")[0].lower() in nuestros:
                    out.setdefault(m.lower(), set()).add(f.relative_to(RAIZ).as_posix())
    return out


def test_el_codigo_no_nombra_tablas_que_el_schema_no_declara():
    declaradas = schema_sql.tablas() | schema_sql.vistas()
    assert declaradas, "no pude leer sql/schema.sql: sin schema no se afirma nada"
    refs = _referencias()
    assert len(refs) > 100, f"el barrido vio {len(refs)} tablas: cambió el patrón o las carpetas"
    fantasmas = {t: sorted(fs) for t, fs in refs.items()
                 if t not in declaradas and t not in FANTASMAS_JUSTIFICADOS}
    assert not fantasmas, (
        "el código nombra tablas que sql/schema.sql no declara:\n  "
        + "\n  ".join(f"{t} ← {', '.join(fs)}" for t, fs in sorted(fantasmas.items()))
        + "\n\nSumá el CREATE TABLE IF NOT EXISTS al schema (apply_schema la crea en el "
          "deploy). Si la tabla la crea otro sistema a propósito, va en "
          "FANTASMAS_JUSTIFICADOS con el motivo.")


def test_la_lista_de_justificados_no_tiene_fantasmas():
    """Una excepción que nombra algo que ya está declarado (o que ya nadie usa)
    tapa el próximo caso real. Mismo criterio que el trinquete de scripts."""
    declaradas = schema_sql.tablas() | schema_sql.vistas()
    refs = _referencias()
    sobran = [t for t in FANTASMAS_JUSTIFICADOS if t in declaradas or t not in refs]
    assert not sobran, f"sacá de FANTASMAS_JUSTIFICADOS: {sobran}"
