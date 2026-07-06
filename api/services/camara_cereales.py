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
    # SQL-native (decomiso 2026-06-29): lee mercado.camara_cereales (la MISMA tabla que
    # escribe set_camara_cereal). Antes leía Mongo Derivados.CamaraCereales — DROPEADA →
    # la vista quedaba en blanco. data jsonb = {cereal, precio_ars, precio_usd, updated_by, updated_at}.
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT cereal, data FROM mercado.camara_cereales")
        docs = {r["cereal"]: (r["data"] or {}) for r in cur.fetchall()}

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

    # Audit SQL-native (mercado.camara_cereales_audit, self-create). Best-effort: un fallo
    # del audit NO rompe la carga del precio (ya escrito arriba). Antes iba a Mongo
    # Derivados.CamaraCerealesAudit (no migrado) → la recreaba al dropearla.
    _audit_camara_sql(c, prev, new, email, now)
    return new


def _audit_camara_sql(cereal: str, prev: dict, new: dict, email: str, ts: datetime) -> None:
    """Append del cambio a mercado.camara_cereales_audit (SQL). Reemplaza el audit Mongo."""
    try:
        from psycopg.types.json import Jsonb

        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS mercado.camara_cereales_audit ("
                "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, cereal text, "
                "prev jsonb, new jsonb, updated_by text, updated_at timestamptz)")
            cur.execute(
                "INSERT INTO mercado.camara_cereales_audit (cereal, prev, new, updated_by, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (cereal,
                 Jsonb({"precio_ars": prev.get("precio_ars"), "precio_usd": prev.get("precio_usd")}),
                 Jsonb({"precio_ars": new["precio_ars"], "precio_usd": new["precio_usd"]}),
                 email, ts))
            conn.commit()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# TASAS DE COBERTURA (ON / Pagaré) — inputs manuales GLOBALES
# ─────────────────────────────────────────────────────────────────────────────
# Dos tasas que carga el trader en la tab DATOS. Alimentarán el cálculo de las
# columnas Pagaré / ON del "Pase con Cobertura" (fórmula a definir con la mesa —
# por ahora solo se persisten y se muestran). NO son por commodity: una sola fila
# global (id='GLOBAL'). La tabla se auto-crea para tolerar el drift de schema.
# Se guarda el número tal cual lo tipea el trader (una TNA en %, ej. 40.5).

_TASAS_TABLE = "mercado.agro_tasas_cobertura"
_TASAS_KEY = "GLOBAL"


def _ensure_tasas_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_TASAS_TABLE} ("
        "id text PRIMARY KEY, data jsonb, updated_at timestamptz)")


def get_tasas_cobertura() -> dict[str, Any]:
    """Tasas manuales ON / Pagaré (global). Campos None si no se cargaron.

    Output: {"tasa_on": float|None, "tasa_pagare": float|None,
             "updated_by": str|None, "updated_at": str|None}
    """
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    row = None
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            _ensure_tasas_table(cur)
            cur.execute(f"SELECT data FROM {_TASAS_TABLE} WHERE id = %s", (_TASAS_KEY,))
            row = cur.fetchone()
            conn.commit()
    except Exception:
        row = None
    d = (row["data"] if row else None) or {}
    return {
        "tasa_on":     d.get("tasa_on"),
        "tasa_pagare": d.get("tasa_pagare"),
        "updated_by":  d.get("updated_by"),
        "updated_at":  d.get("updated_at"),
    }


def set_tasas_cobertura(
    tasa_on: float | None,
    tasa_pagare: float | None,
    email: str,
) -> dict[str, Any]:
    """Upsert de las tasas ON / Pagaré. None = no tocar esa tasa (update parcial,
    igual que set_camara_cereal). Devuelve el doc actualizado."""
    if tasa_on is None and tasa_pagare is None:
        raise ValueError("debe venir tasa_on o tasa_pagare (o ambas)")
    if tasa_on is not None and tasa_on <= 0:
        raise ValueError("tasa_on debe ser > 0")
    if tasa_pagare is not None and tasa_pagare <= 0:
        raise ValueError("tasa_pagare debe ser > 0")

    from psycopg.types.json import Jsonb

    from core.postgres import get_pool
    now = datetime.now(UTC)
    prev = get_tasas_cobertura()
    new = {
        "tasa_on":     float(tasa_on) if tasa_on is not None else prev.get("tasa_on"),
        "tasa_pagare": float(tasa_pagare) if tasa_pagare is not None else prev.get("tasa_pagare"),
        "updated_by":  email,
        "updated_at":  now.isoformat(),
    }
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_tasas_table(cur)
        cur.execute(
            f"INSERT INTO {_TASAS_TABLE} (id, data, updated_at) VALUES (%s, %s, %s) "
            f"ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
            (_TASAS_KEY, Jsonb(new), now))
        conn.commit()
    return new
