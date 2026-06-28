"""Service — Cámara Arbitral de Cereales de Rosario.

Single source of truth de los **precios disponibles** de granos. El trader
los carga a mano (ARS y USD por separado, no hay fórmula entre uno y otro)
y la app los reutiliza en varias vistas (Mejoras Precio Dispo, etc.).

Persistencia: `Derivados.CamaraCereales` — 5 docs fijos (_id = cereal), con
audit en `Derivados.CamaraCerealesAudit`.

NOTA: este precio NO es el mismo input que la pizarra de agro (us_pizarra
en `Derivados.AgroPizarra`) — la pizarra es la curva de pase / fin-de-mes
del trader, mientras que la Cámara refleja el "precio disponible" que el
trader le compra al productor hoy. Pueden coincidir o no.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core.mongo import get_mongo_client, get_mongo_client_read

CEREALES = ("TRIGO", "MAIZ", "GIRASOL", "SOJA", "SORGO")


def _validate_cereal(cereal: str) -> str:
    c = cereal.upper()
    if c not in CEREALES:
        raise ValueError(f"Cereal inválido: {cereal!r}. Válidos: {CEREALES}")
    return c


def get_camara_cereales() -> dict[str, Any]:
    """Devuelve los 5 cereales (siempre los 5, aunque no estén cargados).

    Output:
        {
          "ts": datetime,
          "cereales": [
            {"cereal": "TRIGO", "precio_ars": float | None, "precio_usd": float | None,
             "updated_by": str | None, "updated_at": datetime | None},
            ...
          ]
        }
    """
    col = get_mongo_client_read()["Derivados"]["CamaraCereales"]
    docs = {d["_id"]: d for d in col.find({})}

    rows: list[dict[str, Any]] = []
    for cereal in CEREALES:
        d = docs.get(cereal) or {}
        rows.append({
            "cereal":     cereal,
            "precio_ars": d.get("precio_ars"),
            "precio_usd": d.get("precio_usd"),
            "updated_by": d.get("updated_by"),
            "updated_at": d.get("updated_at"),
        })

    return {
        "ts":       datetime.now(UTC),
        "cereales": rows,
    }


def _leer_camara_sql(cereal: str) -> dict:
    """Doc actual del cereal (jsonb `data`) desde SQL. {} si no existe.

    Fuente del `prev` para el update parcial de set_camara_cereal (cutover SQL-native).
    Si PG está caído devuelve {} → un update parcial podría nullear el otro precio; es
    el tradeoff inherente de tener SQL como única fuente."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT data FROM mercado.camara_cereales WHERE cereal = %s", (cereal,))
        row = cur.fetchone()
    return (row["data"] if row else None) or {}


def set_camara_cereal(
    cereal: str,
    precio_ars: float | None,
    precio_usd: float | None,
    email: str,
) -> dict[str, Any]:
    """Upsert de un cereal. None = no tocar ese campo (igual que set_pizarra).

    Devuelve el doc actualizado. Inserta en CamaraCerealesAudit.
    """
    c = _validate_cereal(cereal)
    if precio_ars is None and precio_usd is None:
        raise ValueError("debe venir precio_ars o precio_usd (o ambos)")
    if precio_ars is not None and precio_ars <= 0:
        raise ValueError("precio_ars debe ser > 0")
    if precio_usd is not None and precio_usd <= 0:
        raise ValueError("precio_usd debe ser > 0")

    from core import pg_mirror
    now = datetime.now(UTC)

    # SQL-native: la tabla `mercado.camara_cereales` es la fuente. `prev` (para el
    # update parcial: None = no tocar) sale de SQL, no de Mongo.
    prev = _leer_camara_sql(c)
    new = {
        "cereal":     c,
        "precio_ars": float(precio_ars) if precio_ars is not None else prev.get("precio_ars"),
        "precio_usd": float(precio_usd) if precio_usd is not None else prev.get("precio_usd"),
        "updated_by": email,
        "updated_at": now,
    }

    # Write SQL-native incondicional (carga manual de la mesa). `cereal` queda dentro
    # de `data` para que el read SQL reconstruya el mismo dict. write_native nunca levanta.
    pg_mirror.write_native(
        "mercado.camara_cereales", ["cereal"],
        [{"cereal": c, "data": pg_mirror.doc_iso(new)}],
    )

    # Audit sigue en Mongo (Derivados.CamaraCerealesAudit) — no migrado en esta fase.
    audit = get_mongo_client()["Derivados"]["CamaraCerealesAudit"]
    audit.insert_one({
        "cereal":     c,
        "prev": {
            "precio_ars": prev.get("precio_ars"),
            "precio_usd": prev.get("precio_usd"),
        },
        "new": {
            "precio_ars": new["precio_ars"],
            "precio_usd": new["precio_usd"],
        },
        "updated_by": email,
        "updated_at": now,
    })
    return new
