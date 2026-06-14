"""scripts/reconciliar_tenencia.py — RECONCILIAR tenencia de UNA cuenta a UNA fecha.

Pone lado a lado, por especie, las TRES fuentes para que las compares contra tu
sistema contable (la verdad de referencia) y aísles dónde está el error:

  1) MONGO AuM       — lo que hay hoy en Valuaciones.AuM (fecha_snapshot = fecha).
  2) PORTAFOLIO SQL  — el backfill nuevo (portafolio.tenencia, regla desde=D+1 hábil).
  3) AUNESA LIVE     — consulta en vivo a Aunesa con la fecha CORREGIDA
                       (desde = fecha + 1 día hábil), parseo crudo SIN exclusiones.

Para cada especie muestra CANTIDAD / PRECIO / VALUACIÓN de las 3 → así ves si lo
que no cruza es la cantidad (problema de fecha/settlement) o el precio (toggle
"Actualizar cotizaciones" que todavía NO mandamos). NO escribe nada.

Cuenta al AZAR: si NO pasás cuenta (solo la fecha), el script elige al azar una
cuenta que tenga tenencia ese día en portafolio.tenencia → comparás directo sin
buscar un número a mano.

Uso:
    python -m scripts.reconciliar_tenencia 255 2026-05-29     # cuenta fija
    python -m scripts.reconciliar_tenencia 2026-04-30         # cuenta AL AZAR de esa fecha
    python -m scripts.reconciliar_tenencia 805 2026-05-29 --ticker GD35
"""
from __future__ import annotations

import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import holidays

from core.mongo import get_mongo_client_read
from core.postgres import get_pool
from jobs.aum import _calcular_valuacion, autenticar, consultar_posicion

_FERIADOS = holidays.Argentina()


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _prox_habil(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5 or d in _FERIADOS:
        d = d + timedelta(days=1)
    return d


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


# ── cuenta al azar (de las que tienen tenencia esa fecha en SQL) ─────────────
def _cuenta_random(fecha_iso: str) -> str | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT id_cuenta FROM portafolio.tenencia WHERE fecha = %s",
                    (fecha_iso,))
        ids = [r[0] for r in cur.fetchall()]
    return random.choice(ids) if ids else None


# ── Fuente 1: Mongo AuM ─────────────────────────────────────────────────────
def _mongo_aum(id_cuenta: str, fecha_iso: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    cur = get_mongo_client_read()["Valuaciones"]["AuM"].find(
        {"id_cuenta": id_cuenta, "fecha_snapshot": fecha_iso},
        {"_id": 0, "unidad": 1, "cantidad": 1, "precio": 1, "valuacion": 1})
    for d in cur:
        u = d.get("unidad")
        if not u:
            continue
        s = out.setdefault(u, {"cantidad": 0.0, "precio": 0.0, "valuacion": 0.0})
        s["cantidad"] += _f(d.get("cantidad"))
        s["precio"] = max(s["precio"], _f(d.get("precio")))
        s["valuacion"] += _f(d.get("valuacion"))
    return out


# ── Fuente 2: portafolio.tenencia (SQL) ─────────────────────────────────────
def _portafolio_sql(id_cuenta: str, fecha_iso: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    try:
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT unidad, cantidad, precio, valuacion FROM portafolio.tenencia "
                "WHERE id_cuenta = %s AND fecha = %s", (id_cuenta, fecha_iso))
            for unidad, cant, prec, val in cur.fetchall():
                s = out.setdefault(unidad, {"cantidad": 0.0, "precio": 0.0, "valuacion": 0.0})
                s["cantidad"] += _f(cant)
                s["precio"] = max(s["precio"], _f(prec))
                s["valuacion"] += _f(val)
    except Exception as e:
        print(f"  (portafolio.tenencia no disponible: {type(e).__name__}: {e})")
    return out


# ── Fuente 3: Aunesa LIVE (fecha corregida, parseo crudo sin exclusiones) ────
def _aunesa_live(id_cuenta: str, desde_ddmmyyyy: str) -> dict[str, dict]:
    headers = autenticar()
    data, reauth = consultar_posicion(id_cuenta, headers, desde=desde_ddmmyyyy, timeout=240)
    if reauth:
        headers = autenticar()
        data, _ = consultar_posicion(id_cuenta, headers, desde=desde_ddmmyyyy, timeout=240)
    if not isinstance(data, list):
        return {}
    grupos: dict[tuple, dict] = defaultdict(lambda: {"cantidad": 0.0, "precio": 0.0, "tipo": ""})
    for r in data:
        if not isinstance(r, dict) or r.get("informacion") != "Acumulado":
            continue
        u = r.get("unidad")
        if not u:
            continue
        g = grupos[(u, r.get("tipoTitulo") or "")]
        g["cantidad"] += _f(r.get("cantidad")) * -1
        g["precio"] = max(g["precio"], _f(r.get("precio")))
        g["tipo"] = r.get("tipoTitulo") or ""
    out: dict[str, dict] = {}
    for (u, tipo), g in grupos.items():
        if g["cantidad"] == 0:
            continue
        val = _calcular_valuacion({"precio": g["precio"], "cantidad": g["cantidad"], "tipoTitulo": tipo})
        s = out.setdefault(u, {"cantidad": 0.0, "precio": 0.0, "valuacion": 0.0})
        s["cantidad"] += g["cantidad"]
        s["precio"] = max(s["precio"], g["precio"])
        s["valuacion"] += _f(val)
    return out


def _row(label, s):
    return f"{label:<8} {s['cantidad']:>16,.2f} {s['precio']:>14,.4f} {s['valuacion']:>18,.2f}"


def main() -> int:
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(pos) >= 2:
        id_cuenta, fecha_iso = pos[0], pos[1]
    elif len(pos) == 1:                                   # solo fecha → cuenta al azar
        fecha_iso = pos[0]
        id_cuenta = _cuenta_random(fecha_iso)
        if not id_cuenta:
            print(f"No hay cuentas con tenencia en {fecha_iso} (¿corriste el backfill de esa fecha?).")
            return 1
        print(f"🎲 cuenta al azar para {fecha_iso}: {id_cuenta}\n")
    else:
        print("Uso: python -m scripts.reconciliar_tenencia [<id_cuenta>] <YYYY-MM-DD> [--ticker X]")
        return 1
    filtro = (_opt("--ticker") or "").upper()

    objetivo = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
    desde = _prox_habil(objetivo).strftime("%d/%m/%Y")

    print("=" * 78)
    print(f"RECONCILIACIÓN · cuenta {id_cuenta} · fecha objetivo {fecha_iso}")
    print(f"  Mongo AuM         → busca fecha_snapshot = {fecha_iso} (etiqueta actual, puede mentir)")
    print(f"  portafolio.SQL    → busca fecha = {fecha_iso} (regla corregida)")
    print(f"  Aunesa LIVE       → desde = {desde} (objetivo + 1 hábil) · SIN toggle cotizaciones")
    print("=" * 78)

    src_mongo = _mongo_aum(id_cuenta, fecha_iso)
    src_port = _portafolio_sql(id_cuenta, fecha_iso)
    src_live = _aunesa_live(id_cuenta, desde)

    todas = sorted(set(src_mongo) | set(src_port) | set(src_live))
    if filtro:
        todas = [u for u in todas if filtro in u.upper()]

    z = {"cantidad": 0.0, "precio": 0.0, "valuacion": 0.0}
    for u in todas:
        m, p, l = src_mongo.get(u, z), src_port.get(u, z), src_live.get(u, z)
        # marca diferencias gruesas entre las 3 (cantidad y precio por separado)
        cants = {round(s["cantidad"], 2) for s in (m, p, l) if s is not z}
        precs = {round(s["precio"], 2) for s in (m, p, l) if s is not z}
        flag_c = "  ⚠CANT" if len(cants) > 1 else ""
        flag_p = "  ⚠PRECIO" if len(precs) > 1 else ""
        print(f"\n── {u}{flag_c}{flag_p}")
        print(f"         {'CANTIDAD':>16} {'PRECIO':>14} {'VALUACIÓN':>18}")
        print("  " + _row("MONGO", m))
        print("  " + _row("PORTAF", p))
        print("  " + _row("LIVE", l))

    def _tot(src):
        return sum(s["valuacion"] for s in src.values())

    print("\n" + "=" * 78)
    print(f"  TOTAL VALUACIÓN   MONGO={_tot(src_mongo):,.2f}   "
          f"PORTAF={_tot(src_port):,.2f}   LIVE={_tot(src_live):,.2f}")
    print(f"  especies          MONGO={len(src_mongo)}   PORTAF={len(src_port)}   LIVE={len(src_live)}")
    print("=" * 78)
    print("\nLeé esto AL LADO de tu sistema contable:")
    print("  · ¿Qué columna reproduce la CANTIDAD del contable? → ahí está la fecha correcta.")
    print("  · ¿El PRECIO no cruza en ninguna? → es el toggle 'Actualizar cotizaciones' (falta).")
    print("(NO se escribió nada — read-only.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
