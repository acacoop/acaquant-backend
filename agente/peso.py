"""`agente/peso.py` — cuánto pesa cada tabla, EN VIVO.

Pedido del user (2026-08-24): *«esto tiene que ser más realtime y mostrar el
peso que va dando de cada tabla, no con la foto de ayer»*.

El tamaño de una tabla es **una query barata contra el catálogo de Postgres**:
no hace falta un job nocturno. Lo que cambia es la pregunta — si es en vivo,
¿contra qué compara? Contra la serie de las últimas 24 h, que se guarda sola y
se purga sola.

**El peso de cada tabla NO es un hallazgo**: es información. Llenar la pantalla
con 200 tamaños es el ruido que hace que nadie mire. El hallazgo es lo que
CRECIÓ fuera de lo suyo.

⚠️ La escritura de la serie vive acá y no en el detector: **un detector mira y
devuelve**. Es la misma separación que `agente/tablas.barrer` o
`agente/seguridad.sacar_foto`.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
from functools import lru_cache

from core.postgres import get_pool

logger = logging.getLogger(__name__)

DIAS = 3


def medir() -> dict[str, int]:
    """El tamaño de cada tabla, ahora."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT n.nspname || '.' || c.relname, pg_total_relation_size(c.oid)
              FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relkind = 'r'
               AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        """)
        return {t: int(b or 0) for t, b in cur.fetchall()}


def guardar(hoy: dict[str, int]) -> None:
    """Anota la foto y purga lo viejo **en la misma transacción**, para que no
    haga falta otro cron que alguien se pueda olvidar."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO agente.db_peso (at, tablas) "
                        "VALUES (now(), %s)", (json.dumps(hoy),))
            cur.execute("DELETE FROM agente.db_peso "
                        "WHERE at < now() - make_interval(days => %s)", (DIAS,))
    except Exception as e:
        logger.warning("agente/peso: no pude guardar la serie (%s)", e)


def de_hace(horas: int = 24) -> dict[str, int]:
    """La foto más cercana a hace N horas. `{}` = todavía no hay referencia, y
    entonces **no se afirma nada**: la primera medición no puede decir que algo
    creció."""
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT tablas FROM agente.db_peso "
                        " WHERE at <= now() - make_interval(hours => %s) "
                        " ORDER BY at DESC LIMIT 1", (horas,))
            f = cur.fetchone()
        return dict(f[0]) if f and f[0] else {}
    except Exception as e:
        logger.warning("agente/peso: sin serie previa (%s)", e)
        return {}


def mb(b) -> str:
    b = float(b or 0)
    for u, s in ((1 << 30, "GB"), (1 << 20, "MB"), (1 << 10, "KB")):
        if b >= u:
            return f"{b / u:,.1f} {s}"
    return f"{int(b)} B"


# ═══ LAS QUE TIENEN QUE EXISTIR ════════════════════════════════════════════
#
# ⚠️⚠️ **COMPARAR CONTRA AYER SOLO SIRVE UN DÍA.**
#
# `de_hace(24)` mira la foto de hace 24 h, y esa referencia **se mueve**: una
# tabla borrada el martes 19:00 se ve el miércoles al mediodía (la foto del
# martes al mediodía la tenía) y deja de verse el miércoles a la noche, porque
# a esa altura «hace 24 h» ya es un mundo sin la tabla. Después, silencio para
# siempre — y la tabla sigue sin estar.
#
# Es como comparar tu foto de hoy con la de ayer para notar que te cortaste el
# pelo: funciona el primer día, y al segundo en la foto de ayer ya estás pelado.
#
# La otra pregunta —«¿está la tabla que el sistema dice que tiene que estar?»—
# se puede contestar SIEMPRE, y la respuesta ya está escrita en `sql/schema.sql`.
_RE_CREA = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_]+)\.([a-z_0-9]+)", re.I)
_RE_DROP = re.compile(
    r"DROP\s+TABLE\s+IF\s+EXISTS\s+([a-z_]+)\.([a-z_0-9]+)", re.I)


@lru_cache(maxsize=1)
def declaradas() -> frozenset[str]:
    """Las `schema.tabla` que `sql/schema.sql` dice que tienen que existir.

    Se le RESTAN las que el mismo archivo dropea: una tabla dada de baja a
    propósito no es un faltante, y sin esta resta el agente pediría para siempre
    las cuatro que ya decidimos borrar.

    `frozenset()` si no se puede leer el archivo — y ahí **no se afirma nada**:
    quedarse sin schema no puede convertirse en «faltan 234 tablas».
    """
    try:
        txt = (pathlib.Path(__file__).resolve().parents[1]
               / "sql" / "schema.sql").read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("agente/peso: no pude leer sql/schema.sql (%s)", e)
        return frozenset()
    crea = {f"{a}.{b}".lower() for a, b in _RE_CREA.findall(txt)}
    drop = {f"{a}.{b}".lower() for a, b in _RE_DROP.findall(txt)}
    return frozenset(crea - drop)


# ═══ EL PESO TOTAL, DOS VECES POR DÍA ══════════════════════════════════════
#
# Pedido del user (2026-08-28): *«que sea fijo dos veces por día, a las 11 y a
# las 16, que avise el peso total de la base»*. En hora de la MESA.
#
# El dato ya se venía midiendo y guardando cada hora — lo que faltaba era
# dónde verlo. El propio detector lo decía: «el peso de cada tabla no es un
# hallazgo, es información y viaja aparte», y ese «aparte» nunca se construyó.
FRANJAS_ART = (11, 16)


def franja_de_hoy(ahora=None) -> str:
    """La última franja que YA pasó hoy, o `""` si todavía no pasó ninguna.

    Devuelve la franja y no un booleano a propósito: es el SUJETO del hallazgo,
    así el de las 11 y el de las 16 son dos avisos distintos y el segundo no
    pisa al primero.

    ⚠️ Y se sigue emitiendo la franja vigente en cada pasada, no solo en la hora
    exacta: `registro` cierra por ausencia lo que un detector deja de ver, así
    que emitirlo una sola vez a las 11:00 lo haría desaparecer a las 12:00.
    """
    from agente.reloj import ahora_utc
    from core.tz import AR_TZ

    h = ahora_utc(ahora).astimezone(AR_TZ).hour
    pasadas = [f for f in FRANJAS_ART if h >= f]
    return f"{max(pasadas):02d}:00" if pasadas else ""
