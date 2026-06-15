"""api/services/import_tenencia_sql.py — import manual a SQL `portafolio.tenencia`.

Dos modos (Manager → AUNESA → IMPORTAR):
  1) PRECIOS   — Excel [unidad, precio, fecha] → actualiza SOLO `precio` por (fecha, unidad).
                 NO recalcula valuación (eso es el paso 2: precio×cantidad, /100 bonos).
  2) AUM       — Excel [Cuenta, Unidad, Cantidad, Fecha, Precio, Valuación] → pisa las
                 tenencias de cada fecha (delete+insert por (fecha, id_cuenta), idempotente).
                 Setea la columna `aum` ('si'/'no') con el mismo filtro `_aum_filters`.

El front parsea el Excel y manda `rows` JSON. `commit=False` previsualiza; `True` aplica.
Scopeado por (fecha, cuenta) — sin scans de toda la tabla (REGLA #4).
"""
from __future__ import annotations

import re

from api.services.import_tenencia import _norm_fecha, _num
from core.postgres import get_pool
from jobs._aum_filters import (
    is_excluded,
    load_contrapartes_id_cuentas,
    load_contrapartes_names,
)

_MAX_ROWS = 50_000
_RE_IDC = re.compile(r"\[(\d+)\]")


def _idc_de(row: dict) -> str:
    """id_cuenta: del campo directo o extraído de 'Cuenta' = '[805] NOMBRE'."""
    idc = str(row.get("id_cuenta") or row.get("Cuenta") or row.get("cuenta") or "").strip()
    if idc.endswith(".0"):
        idc = idc[:-2]
    if idc.isdigit():
        return idc
    m = _RE_IDC.search(idc)
    return m.group(1) if m else ""


def _campo(row: dict, *nombres):
    for n in nombres:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


# ── MODO 1: PRECIOS ───────────────────────────────────────────────────────────
def importar_precios(rows: list[dict], commit: bool = False) -> dict:
    """Excel [unidad, precio, fecha] → UPDATE precio en portafolio.tenencia por (fecha, unidad)."""
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "archivo vacío o sin filas"}
    if len(rows) > _MAX_ROWS:
        return {"ok": False, "error": f"demasiadas filas ({len(rows)} > {_MAX_ROWS})"}

    parsed: list[tuple[str, str, float]] = []   # (fecha, unidad, precio)
    errores: list[dict] = []
    for i, r in enumerate(rows, 1):
        fecha = _norm_fecha(_campo(r, "fecha", "Fecha"))
        unidad = str(_campo(r, "unidad", "Unidad", "especie") or "").strip()
        precio = _num(_campo(r, "precio", "Precio"))
        problemas = []
        if not fecha:
            problemas.append("fecha inválida")
        if not unidad:
            problemas.append("falta unidad")
        if precio is None:
            problemas.append("falta precio")
        if problemas:
            errores.append({"fila": i, "detalle": "; ".join(problemas)})
            continue
        parsed.append((fecha, unidad, precio))

    fechas = sorted({f for f, _, _ in parsed})
    resumen = {"ok": True, "modo": "precios", "commit": commit, "n_filas": len(rows),
               "n_validas": len(parsed), "n_errores": len(errores), "errores": errores[:200],
               "fechas": fechas}
    if not commit:
        # Previsualización: cuántas matchean en la tabla.
        with get_pool().connection() as conn, conn.cursor() as cur:
            match = 0
            for fecha, unidad, _ in parsed:
                cur.execute("SELECT 1 FROM portafolio.tenencia "
                            "WHERE fecha = %s AND unidad = %s LIMIT 1", (fecha, unidad))
                if cur.fetchone():
                    match += 1
        return {**resumen, "matchean": match, "sin_match": len(parsed) - match}
    if not parsed:
        return {**resumen, "aplicado": False, "error": "no hay filas válidas"}

    actualizadas = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for fecha, unidad, precio in parsed:
            cur.execute("UPDATE portafolio.tenencia SET precio = %s "
                        "WHERE fecha = %s AND unidad = %s", (precio, fecha, unidad))
            actualizadas += cur.rowcount
        conn.commit()
    return {**resumen, "aplicado": True, "filas_actualizadas": actualizadas,
            "nota": "valuación NO recalculada (paso 2: precio×cantidad, /100 bonos)"}


# ── PASO 2: RECALCULAR VALUACIÓN (precio × cantidad, ÷100 renta fija) ──────────
# El divisor lo decide la CARTERA (no el tipo de instrumento):
#   ×1   → FCI, RENTA VARIABLE, MONEDAS, DERIVADOS
#   ÷100 → HD, DL, ARS (renta fija: cotizan en paridad)
#   FINANCIAMIENTO y cualquier otra → SIN CLASIFICAR: no se toca (default-deny).
_CARTERA_DIV_1 = {"FCI", "RENTA VARIABLE", "MONEDAS", "DERIVADOS"}
_CARTERA_DIV_100 = {"HD", "DL", "ARS"}


def _divisor_cartera(cartera: str | None) -> int | None:
    c = (cartera or "").strip().upper()
    if c in _CARTERA_DIV_100:
        return 100
    if c in _CARTERA_DIV_1:
        return 1
    return None


def recalcular_valuacion(fechas: list[str], commit: bool = False) -> dict:
    """Recalcula `valuacion = cantidad × precio (÷100 si renta fija)` en
    portafolio.tenencia para las `fechas` dadas (las que importaste en el paso 1).
    El divisor sale de la CARTERA. Devuelve el antes/después por cartera; las
    carteras sin regla (FINANCIAMIENTO/otras) NO se tocan y se reportan aparte.
    `commit=False` previsualiza; `True` aplica el UPDATE (scopeado por fecha)."""
    fechas_ok = sorted({f for f in (_norm_fecha(x) for x in (fechas or [])) if f})
    if not fechas_ok:
        return {"ok": False, "error": "no hay fechas válidas"}

    # cartera real: la de la fila o, si quedó NULL (filas del modo AUM), la de assets.
    por_cartera: dict[str, dict] = {}
    sin_clasificar: dict[str, dict] = {}
    updates: list[tuple] = []   # (valuacion, fecha, id_cuenta, unidad)
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.fecha, t.id_cuenta, t.unidad, "
            "       COALESCE(NULLIF(t.cartera, ''), a.cartera) AS cartera, "
            "       t.cantidad, t.precio, t.valuacion "
            "FROM portafolio.tenencia t "
            "LEFT JOIN portafolio.assets a ON a.unidad = t.unidad "
            "WHERE t.fecha = ANY(%s)", (fechas_ok,))
        for fecha, idc, unidad, cartera, cantidad, precio, val_old in cur.fetchall():
            div = _divisor_cartera(cartera)
            antes = float(val_old or 0)
            key = (cartera or "").strip().upper() or "(sin cartera)"
            if div is None:
                g = sin_clasificar.setdefault(key, {"n": 0, "total_antes": 0.0})
                g["n"] += 1
                g["total_antes"] += antes
                continue
            val_new = round(float(cantidad or 0) * float(precio or 0) / div, 2)
            g = por_cartera.setdefault(key, {"divisor": div, "n": 0,
                                             "total_antes": 0.0, "total_despues": 0.0})
            g["n"] += 1
            g["total_antes"] += antes
            g["total_despues"] += val_new
            updates.append((val_new, fecha, idc, unidad))

        carteras = [{"cartera": k, "divisor": v["divisor"], "n": v["n"],
                     "total_antes": round(v["total_antes"], 2),
                     "total_despues": round(v["total_despues"], 2),
                     "delta": round(v["total_despues"] - v["total_antes"], 2)}
                    for k, v in sorted(por_cartera.items())]
        no_clasif = [{"cartera": k, "n": v["n"], "total_antes": round(v["total_antes"], 2)}
                     for k, v in sorted(sin_clasificar.items())]
        resumen = {
            "ok": True, "commit": commit, "fechas": fechas_ok,
            "n_filas": len(updates) + sum(v["n"] for v in sin_clasificar.values()),
            "n_recalculadas": len(updates), "carteras": carteras,
            "sin_clasificar": no_clasif,
            "total_antes": round(sum(c["total_antes"] for c in carteras), 2),
            "total_despues": round(sum(c["total_despues"] for c in carteras), 2),
        }
        if not commit:
            return resumen
        if not updates:
            return {**resumen, "aplicado": False, "error": "nada para recalcular"}
        cur.executemany(
            "UPDATE portafolio.tenencia SET valuacion = %s "
            "WHERE fecha = %s AND id_cuenta = %s AND unidad = %s", updates)
        conn.commit()
    return {**resumen, "aplicado": True, "filas_actualizadas": len(updates)}


# ── MODO 2: AUM (pisa tenencias) ──────────────────────────────────────────────
def importar_aum(rows: list[dict], actor: str, commit: bool = False) -> dict:
    """Excel [Cuenta, Unidad, Cantidad, Fecha, Precio, Valuación] → pisa portafolio.tenencia
    por (fecha, id_cuenta). Setea `aum` con _aum_filters. Resto de columnas (ticker/cartera/
    moneda) quedan NULL — la vista las resuelve por JOIN a assets."""
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "archivo vacío o sin filas"}
    if len(rows) > _MAX_ROWS:
        return {"ok": False, "error": f"demasiadas filas ({len(rows)} > {_MAX_ROWS})"}

    cont_ids = load_contrapartes_id_cuentas()
    cont_names = load_contrapartes_names()
    docs: list[dict] = []
    errores: list[dict] = []
    for i, r in enumerate(rows, 1):
        fecha = _norm_fecha(_campo(r, "fecha", "Fecha"))
        idc = _idc_de(r)
        unidad = str(_campo(r, "unidad", "Unidad", "especie") or "").strip()
        cuenta = str(_campo(r, "cuenta", "Cuenta") or (f"[{idc}]" if idc else "")).strip()
        cant = _num(_campo(r, "cantidad", "Cantidad"))
        prec = _num(_campo(r, "precio", "Precio"))
        val = _num(_campo(r, "valuacion", "Valuación", "Valuacion"))
        problemas = []
        if not fecha:
            problemas.append("fecha inválida")
        if not idc:
            problemas.append("id_cuenta/Cuenta inválido")
        if not unidad:
            problemas.append("falta unidad")
        if problemas:
            errores.append({"fila": i, "detalle": "; ".join(problemas)})
            continue
        aum = "no" if is_excluded(cuenta, unidad, id_cuenta=idc,
                                  contrapartes_ids=cont_ids, contrapartes_names=cont_names) else "si"
        docs.append({"fecha": fecha, "id_cuenta": idc, "cuenta": cuenta, "unidad": unidad,
                     "cantidad": cant, "precio": prec, "valuacion": val, "aum": aum})

    cuentas = sorted({d["id_cuenta"] for d in docs})
    fechas = sorted({d["fecha"] for d in docs})
    resumen = {"ok": True, "modo": "aum", "commit": commit, "n_filas": len(rows),
               "n_validas": len(docs), "n_errores": len(errores), "errores": errores[:200],
               "cuentas": cuentas, "n_cuentas": len(cuentas), "fechas": fechas,
               "total_valuacion": round(sum(d["valuacion"] or 0 for d in docs), 2)}
    if not commit:
        return resumen
    if not docs:
        return {**resumen, "aplicado": False, "error": "no hay filas válidas"}

    # delete+insert por (fecha, id_cuenta) — idempotente, scopeado.
    por_grupo: dict[tuple[str, str], list[dict]] = {}
    for d in docs:
        por_grupo.setdefault((d["fecha"], d["id_cuenta"]), []).append(d)

    borrados = insertados = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for (fecha, idc), grupo in por_grupo.items():
            cur.execute("DELETE FROM portafolio.tenencia WHERE fecha = %s AND id_cuenta = %s",
                        (fecha, idc))
            borrados += cur.rowcount
            cur.executemany(
                "INSERT INTO portafolio.tenencia "
                "(fecha,id_cuenta,cuenta,unidad,cantidad,precio,valuacion,aum) "
                "VALUES (%(fecha)s,%(id_cuenta)s,%(cuenta)s,%(unidad)s,"
                "%(cantidad)s,%(precio)s,%(valuacion)s,%(aum)s)", grupo)
            insertados += len(grupo)
        conn.commit()
    return {**resumen, "aplicado": True, "borrados": borrados, "insertados": insertados,
            "actor": actor}
