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

# Qué pata carga el trader a MANO; la otra se DERIVA con el dólar Banco Nación
# (decisión de la mesa): SOJA en ARS, el resto (TRIGO/MAIZ/GIRASOL/SORGO) en USD.
# Solo se completa una pata — la otra es 100% automática.
_MANUAL_LEG_ARS = ("SOJA",)


def manual_leg(cereal: str) -> str:
    """'ars' si el trader carga la pata en pesos, 'usd' si la carga en dólares."""
    return "ars" if cereal.upper() in _MANUAL_LEG_ARS else "usd"


def _derivar_legs(
    cereal: str,
    precio_ars_stored: float | None,
    precio_usd_stored: float | None,
    dolar_bna: float | None,
) -> tuple[float | None, float | None]:
    """(precio_ars, precio_usd) con la pata derivada calculada con el dólar BNA.

    SOJA: manual ARS → USD = ARS / BNA (dolariza).
    Resto: manual USD → ARS = USD × BNA (pesifica).
    La pata derivada queda None si falta el dólar BNA o el valor manual."""
    if manual_leg(cereal) == "ars":
        precio_ars = precio_ars_stored
        precio_usd = (precio_ars / dolar_bna) if (precio_ars is not None and dolar_bna) else None
    else:
        precio_usd = precio_usd_stored
        precio_ars = (precio_usd * dolar_bna) if (precio_usd is not None and dolar_bna) else None
    return precio_ars, precio_usd


def _validate_cereal(cereal: str) -> str:
    c = cereal.upper()
    if c not in CEREALES:
        raise ValueError(f"Cereal inválido: {cereal!r}. Válidos: {CEREALES}")
    return c


def get_camara_cereales() -> dict[str, Any]:
    """Devuelve los 5 cereales (siempre los 5, aunque no estén cargados).

    Solo se guarda la pata MANUAL (SOJA en ARS, resto en USD); la otra se DERIVA
    acá con el dólar Banco Nación (get_dolares_referencia). Si el BNA no está
    cargado, la pata derivada sale None.

    Output:
        {
          "ts": datetime, "dolar_bna": float | None,
          "cereales": [
            {"cereal", "precio_ars", "precio_usd", "manual_leg": "ars"|"usd",
             "updated_by", "updated_at"},
            ...
          ]
        }
    """
    # SQL-native (decomiso 2026-06-29): lee mercado.camara_cereales (la MISMA tabla que
    # escribe set_camara_cereal). data jsonb = {cereal, precio_ars|precio_usd (solo la
    # pata manual), updated_by, updated_at}.
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT cereal, data FROM mercado.camara_cereales")
        docs = {r["cereal"]: (r["data"] or {}) for r in cur.fetchall()}

    dolar_bna = get_dolares_referencia().get("dolar_bna")

    rows: list[dict[str, Any]] = []
    for cereal in CEREALES:
        d = docs.get(cereal) or {}
        precio_ars, precio_usd = _derivar_legs(
            cereal, d.get("precio_ars"), d.get("precio_usd"), dolar_bna)
        rows.append({
            "cereal":     cereal,
            "precio_ars": precio_ars,
            "precio_usd": precio_usd,
            "manual_leg": manual_leg(cereal),
            "updated_by": d.get("updated_by"),
            "updated_at": d.get("updated_at"),
        })

    return {
        "ts":        datetime.now(UTC),
        "dolar_bna": dolar_bna,
        "cereales":  rows,
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
    """Upsert de la PATA MANUAL de un cereal (la otra se deriva al leer con el
    dólar BNA). SOJA se carga en ARS; el resto en USD. Se ignora la pata que no
    corresponde. Devuelve el doc guardado + inserta audit.
    """
    c = _validate_cereal(cereal)
    leg = manual_leg(c)
    valor = precio_ars if leg == "ars" else precio_usd
    if valor is None:
        moneda = "ARS" if leg == "ars" else "USD"
        raise ValueError(f"{c} se carga en {moneda}: falta ese valor")
    if valor <= 0:
        raise ValueError("el precio debe ser > 0")

    from core import pg_mirror
    now = datetime.now(UTC)

    # Solo se persiste la pata manual; la derivada NO se guarda (se calcula al leer
    # con el dólar BNA vigente → si el BNA cambia, la pata derivada se actualiza sola).
    prev = _leer_camara_sql(c)
    new = {
        "cereal":     c,
        "precio_ars": float(valor) if leg == "ars" else None,
        "precio_usd": float(valor) if leg == "usd" else None,
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
# PARÁMETROS MANUALES GLOBALES DE LA MESA AGRO — tab DATOS
# ─────────────────────────────────────────────────────────────────────────────
# Escalares que carga el trader a mano y que alimentan otros cálculos (hoy el
# "Pase con Cobertura"). NO son por commodity: una fila global por familia.
# Todo vive en la misma tabla `mercado.agro_tasas_cobertura` (id = clave de la
# familia), que se auto-crea para tolerar el drift de schema:
#   - id='GLOBAL'  → tasas ON / Pagaré (TNA %, ej. 40.5)
#   - id='DOLARES' → dólares de referencia Banco Nación / Matba Rofex ($/US$)
# Cada familia tiene su propio `updated_at` (fila aparte) → editar un dólar no
# pisa el timestamp de las tasas. El helper genérico evita duplicar el CRUD.

_TASAS_TABLE = "mercado.agro_tasas_cobertura"
_TASAS_KEY = "GLOBAL"
_DOLARES_KEY = "DOLARES"


def _ensure_tasas_table(cur) -> None:
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_TASAS_TABLE} ("
        "id text PRIMARY KEY, data jsonb, updated_at timestamptz)")


def _get_param_doc(key: str, fields: tuple[str, ...]) -> dict[str, Any]:
    """Lee la fila `key` de la tabla de params y devuelve solo `fields` (+audit).
    Campos None si no se cargaron. Tolera PG caído (devuelve todo None)."""
    from psycopg.rows import dict_row

    from core.postgres import get_pool
    row = None
    try:
        with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            _ensure_tasas_table(cur)
            cur.execute(f"SELECT data FROM {_TASAS_TABLE} WHERE id = %s", (key,))
            row = cur.fetchone()
            conn.commit()
    except Exception:
        row = None
    d = (row["data"] if row else None) or {}
    out: dict[str, Any] = {f: d.get(f) for f in fields}
    out["updated_by"] = d.get("updated_by")
    out["updated_at"] = d.get("updated_at")
    return out


def _set_param_doc(
    key: str,
    fields: tuple[str, ...],
    updates: dict[str, float | None],
    email: str,
) -> dict[str, Any]:
    """Upsert parcial de la fila `key`. None = no tocar ese campo (igual que
    set_camara_cereal). Cada valor debe ser > 0. Devuelve el doc actualizado."""
    if all(updates.get(f) is None for f in fields):
        raise ValueError(f"debe venir al menos uno de: {', '.join(fields)}")
    for f in fields:
        v = updates.get(f)
        if v is not None and v <= 0:
            raise ValueError(f"{f} debe ser > 0")

    from psycopg.types.json import Jsonb

    from core.postgres import get_pool
    now = datetime.now(UTC)
    prev = _get_param_doc(key, fields)
    new: dict[str, Any] = {
        f: (float(updates[f]) if updates.get(f) is not None else prev.get(f))
        for f in fields
    }
    new["updated_by"] = email
    new["updated_at"] = now.isoformat()
    with get_pool().connection() as conn, conn.cursor() as cur:
        _ensure_tasas_table(cur)
        cur.execute(
            f"INSERT INTO {_TASAS_TABLE} (id, data, updated_at) VALUES (%s, %s, %s) "
            f"ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
            (key, Jsonb(new), now))
        conn.commit()
    return new


# ── Tasas de cobertura (ON / Pagaré / Caución 7D ARS y USD) ──────────────────
# tasa_caucion_7d (TNA %, en pesos) alimenta el "Descuento a Tasa de Caución 7D"
# que da el "Monto Pesos Cau 7D" de cada card del Pase con Cobertura.
# tasa_caucion_7d_usd es su equivalente en dólares (para las próximas columnas
# del pase). Ver agro_cobertura.
_TASAS_FIELDS = ("tasa_on", "tasa_pagare", "tasa_caucion_7d", "tasa_caucion_7d_usd")


def get_tasas_cobertura() -> dict[str, Any]:
    """Tasas manuales ON / Pagaré / Caución 7D ARS+USD (global). None si no cargadas.

    Output: {"tasa_on", "tasa_pagare", "tasa_caucion_7d", "tasa_caucion_7d_usd",
             "updated_by", "updated_at"}
    """
    return _get_param_doc(_TASAS_KEY, _TASAS_FIELDS)


def set_tasas_cobertura(
    tasa_on: float | None,
    tasa_pagare: float | None,
    email: str,
    tasa_caucion_7d: float | None = None,
    tasa_caucion_7d_usd: float | None = None,
) -> dict[str, Any]:
    """Upsert de las tasas ON / Pagaré / Caución 7D (ARS y USD). None = no tocar."""
    return _set_param_doc(
        _TASAS_KEY, _TASAS_FIELDS,
        {
            "tasa_on": tasa_on,
            "tasa_pagare": tasa_pagare,
            "tasa_caucion_7d": tasa_caucion_7d,
            "tasa_caucion_7d_usd": tasa_caucion_7d_usd,
        },
        email,
    )


# ── Dólares de referencia (Banco Nación / Matba Rofex / BNA Comprador T-1) ────
# Cotizaciones que el trader carga a mano en la tab DATOS y alimentan el "Pase
# con Cobertura". bna_comprador_t1 = BNA comprador de AYER (T−1), usado en la
# columna Pagaré. Antes se pensaron como discovery MAE automático; por decisión
# de la mesa hoy se cargan manual.
_DOLARES_FIELDS = ("dolar_bna", "dolar_matba", "bna_comprador_t1")


def _oficial_live_value() -> float | None:
    """Valor del dólar oficial live (feed MAE, `mid_oficial_live`) — EL MISMO que
    muestra la watchlist como DOLAR OFICIAL. None si no hay dato (→ cae al manual)."""
    try:
        from core.dolar_oficial import mid_oficial_live
        v = (mid_oficial_live("oficial") or {}).get("value")
        return float(v) if v else None
    except Exception:
        return None


def get_dolares_referencia() -> dict[str, Any]:
    """Dólares de referencia (global). `dolar_matba` sale AUTOMÁTICO del dólar
    oficial live (feed MAE — el mismo que la watchlist DOLAR OFICIAL), en real-time;
    fallback al valor manual si el feed está caído. `dolar_bna` / `bna_comprador_t1`
    siguen manuales.

    Output: {"dolar_bna", "dolar_matba", "bna_comprador_t1", "updated_by", "updated_at"}
    """
    doc = _get_param_doc(_DOLARES_KEY, _DOLARES_FIELDS)
    oficial = _oficial_live_value()
    if oficial is not None:
        doc["dolar_matba"] = oficial  # real-time desde el dólar oficial (watchlist)
    return doc


def set_dolares_referencia(
    dolar_bna: float | None,
    dolar_matba: float | None,
    email: str,
    bna_comprador_t1: float | None = None,
) -> dict[str, Any]:
    """Upsert de los dólares de referencia. None = no tocar ese dólar."""
    return _set_param_doc(
        _DOLARES_KEY, _DOLARES_FIELDS,
        {
            "dolar_bna": dolar_bna,
            "dolar_matba": dolar_matba,
            "bna_comprador_t1": bna_comprador_t1,
        },
        email,
    )
