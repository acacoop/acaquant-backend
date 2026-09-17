"""`core/schema_sql.py` — **LO QUE `sql/schema.sql` DECLARA, LEÍDO UNA SOLA VEZ.**

El archivo de schema es la fuente de verdad de qué tablas y qué schemas son
NUESTROS, y ya lo leían dos lugares por su cuenta. Vive acá —en `core/`, la capa
que no importa nada del proyecto— para que `agente/` y cualquier otro puedan
preguntarle sin duplicar el parseo (REGLA #9 B: dos parsers del mismo archivo son
dos verdades, y la que se desincronice no falla: contesta distinto).

⚠️ **ES UNA DECLARACIÓN, NO LA BASE.** `sql/schema.sql` puede ir adelante de lo
que existe en Postgres (`scripts/apply_schema.py` crea lo que falte). Para
preguntar qué hay DE VERDAD está `pg_catalog`; esto contesta qué **debería**
haber, que es otra pregunta y la única que no vence.
"""
from __future__ import annotations

import logging
import pathlib
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

_RE_CREA = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_][a-z_0-9]*)\.([a-z_0-9]+)", re.I)
_RE_DROP = re.compile(
    r"DROP\s+TABLE\s+IF\s+EXISTS\s+([a-z_][a-z_0-9]*)\.([a-z_0-9]+)", re.I)
_RE_SCHEMA = re.compile(
    r"CREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+([a-z_][a-z_0-9]*)", re.I)
_RE_VISTA = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([a-z_][a-z_0-9]*)\.([a-z_0-9]+)", re.I)


@lru_cache(maxsize=1)
def texto() -> str:
    """El archivo, leído UNA vez por proceso. `""` si no se puede."""
    try:
        return (pathlib.Path(__file__).resolve().parents[1]
                / "sql" / "schema.sql").read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("core/schema_sql: no pude leer sql/schema.sql (%s)", e)
        return ""


@lru_cache(maxsize=1)
def tablas() -> frozenset[str]:
    """Las `schema.tabla` que el archivo dice que tienen que existir.

    Se le RESTAN las que el mismo archivo dropea: una tabla dada de baja a
    propósito no es un faltante.

    `frozenset()` si no se puede leer — y ahí **no se afirma nada**: quedarse sin
    schema no puede convertirse en «faltan 200 tablas».
    """
    txt = texto()
    if not txt:
        return frozenset()
    crea = {f"{a}.{b}".lower() for a, b in _RE_CREA.findall(txt)}
    drop = {f"{a}.{b}".lower() for a, b in _RE_DROP.findall(txt)}
    return frozenset(crea - drop)


@lru_cache(maxsize=1)
def vistas() -> frozenset[str]:
    """Las `schema.vista` declaradas. Aparte de `tablas()` a propósito: una vista
    se lee como una tabla pero no se escribe ni se dropea con `DROP TABLE`."""
    txt = texto()
    if not txt:
        return frozenset()
    return frozenset(f"{a}.{b}".lower() for a, b in _RE_VISTA.findall(txt))


@lru_cache(maxsize=1)
def schemas() -> frozenset[str]:
    """Los schemas DECLARADOS (`CREATE SCHEMA`), que es nuestro territorio.

    Se leen del `CREATE SCHEMA` y **no se deducen de los nombres de tabla**:
    `partner` está declarado y sus tablas no figuran en el archivo, así que
    sacarlo de las tablas lo dejaría afuera — y es nuestro.
    """
    txt = texto()
    return frozenset(m.lower() for m in _RE_SCHEMA.findall(txt)) if txt else frozenset()


@lru_cache(maxsize=1)
def _por_nombre_corto() -> dict[str, str]:
    """`tabla` → `schema.tabla`, **solo cuando el nombre corto es ÚNICO**.

    ⚠️ **LAS AMBIGUAS QUEDAN AFUERA A PROPÓSITO, Y ES LA MITAD QUE IMPORTA.**
    Medido sobre `sql/schema.sql` (208 tablas): TRES nombres cortos viven en dos
    schemas — `cuentas` (clientes · bancos), `movimientos` (operaciones · bancos)
    y `presencia` (manager · bancos). Resolver uno de esos a cualquiera de los
    dos le adjudicaría a una tabla un escritor que no la escribe, y eso no
    falla: contesta con seguridad la cosa equivocada, que es exactamente el modo
    de falla que la REGLA #9 persigue.

    Devolver `""` para las tres deja al que pregunta en «no sé», que en
    `core/escribe.py` significa **se le sigue exigiendo frescura** — el default
    seguro. Perder cobertura sobre tres tablas es barato; atribuir mal, no.
    """
    vistas: dict[str, str | None] = {}
    for nombre in tablas():
        corto = nombre.split(".", 1)[1]
        # `None` marca «ya la vi en otro schema»: ambigua, no se resuelve.
        vistas[corto] = None if corto in vistas else nombre
    return {c: n for c, n in vistas.items() if n}


def donde_vive(corto: str) -> str:
    """`schema.tabla` de un nombre sin schema, o `""` si es ambiguo o no está.

    Es lo que permite leer un `INSERT INTO negocio_movimientos` —sin schema,
    resuelto por `search_path` en runtime— y saber de qué tabla habla.
    """
    t = (corto or "").strip().lower()
    if "." in t:
        return t if t in tablas() else ""
    return _por_nombre_corto().get(t, "")


def ambiguo(corto: str) -> bool:
    """¿Ese nombre corto vive en MÁS DE UN schema?

    Separa las dos razones por las que `donde_vive` devuelve `""`, y hay que
    tratarlas distinto: si la tabla no está declarada, quedarse con el nombre
    corto no le saca nada a nadie; si es ambigua, quedarse con el corto le
    ADJUDICA el escritor de una a la otra — que es peor que no saber.
    """
    t = (corto or "").strip().lower()
    if not t or "." in t:
        return False
    return t not in _por_nombre_corto() and any(
        n.split(".", 1)[1] == t for n in tablas())
