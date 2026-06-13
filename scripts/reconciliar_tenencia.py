"""scripts/reconciliar_tenencia.py — RECONCILIAR tenencia de UNA cuenta a UNA fecha.

Compara, por especie, las DOS fuentes que importan para el problema y las cruzás
contra tu sistema contable (la verdad de referencia):

  1) MONGO AuM   — lo que acaquant MUESTRA HOY en AUM y CARTERAS
                   (Valuaciones.AuM, fecha_snapshot = fecha). El sospechoso.
  2) AUNESA LIVE — lo que Aunesa devuelve en vivo con la fecha CORREGIDA
                   (desde = fecha + 1 día hábil), parseo crudo SIN exclusiones.
                   Es lo que el dato DEBERÍA decir.

Para cada especie muestra CANTIDAD / PRECIO / VALUACIÓN de las dos → así ves si lo
que no cruza es la cantidad (problema de fecha) o el precio (toggle "Actualizar
cotizaciones" que todavía NO mandamos). NO escribe nada.

Uso:
    python -m scripts.reconciliar_tenencia 255 2026-05-29
    python -m scripts.reconciliar_tenencia 805 2026-05-29 --ticker GD35
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta

import holidays

from core.mongo import get_mongo_client_read
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


# ── Fuente 2: Aunesa LIVE (fecha corregida, parseo crudo sin exclusiones) ────
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
    if len(pos) < 2:
        print("Uso: python -m scripts.reconciliar_tenencia <id_cuenta> <YYYY-MM-DD> [--ticker X]")
        return 1
    id_cuenta, fecha_iso = pos[0], pos[1]
    filtro = (_opt("--ticker") or "").upper()

    objetivo = datetime.strptime(fecha_iso, "%Y-%m-%d").date()
    desde = _prox_habil(objetivo).strftime("%d/%m/%Y")

    print("=" * 70)
    print(f"RECONCILIACIÓN · cuenta {id_cuenta} · fecha objetivo {fecha_iso}")
    print(f"  MONGO  → lo que acaquant MUESTRA HOY (Valuaciones.AuM, fecha_snapshot={fecha_iso})")
    print(f"  LIVE   → Aunesa en vivo, desde={desde} (objetivo + 1 hábil) · SIN toggle cotizaciones")
    print("=" * 70)

    src_mongo = _mongo_aum(id_cuenta, fecha_iso)
    src_live = _aunesa_live(id_cuenta, desde)

    todas = sorted(set(src_mongo) | set(src_live))
    if filtro:
        todas = [u for u in todas if filtro in u.upper()]

    z = {"cantidad": 0.0, "precio": 0.0, "valuacion": 0.0}
    for u in todas:
        m, l = src_mongo.get(u, z), src_live.get(u, z)
        cant_dif = round(m["cantidad"], 2) != round(l["cantidad"], 2)
        prec_dif = round(m["precio"], 2) != round(l["precio"], 2)
        flag_c = "  ⚠CANT" if cant_dif else ""
        flag_p = "  ⚠PRECIO" if prec_dif else ""
        print(f"\n── {u}{flag_c}{flag_p}")
        print(f"         {'CANTIDAD':>16} {'PRECIO':>14} {'VALUACIÓN':>18}")
        print("  " + _row("MONGO", m))
        print("  " + _row("LIVE", l))

    def _tot(src):
        return sum(s["valuacion"] for s in src.values())

    print("\n" + "=" * 70)
    print(f"  TOTAL VALUACIÓN   MONGO={_tot(src_mongo):,.2f}   LIVE={_tot(src_live):,.2f}")
    print(f"  especies          MONGO={len(src_mongo)}   LIVE={len(src_live)}")
    print("=" * 70)
    print("\nLeé esto AL LADO de tu sistema contable:")
    print("  · ⚠CANT  → la CANTIDAD difiere: problema de FECHA (qué día es realmente).")
    print("  · ⚠PRECIO→ el PRECIO difiere: toggle 'Actualizar cotizaciones' (falta mandarlo).")
    print("(NO se escribió nada — read-only.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
