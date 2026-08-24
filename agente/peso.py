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
