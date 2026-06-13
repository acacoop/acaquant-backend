"""scripts/tenencia_fin_de_mes.py — tenencia CORREGIDA al último día hábil de cada mes.

Para UNA cuenta y un rango de meses, calcula el último día hábil de cada mes y le
pide a Aunesa la posición de ESE día con la fecha CORREGIDA (desde = díahábil + 1,
porque Aunesa con desde=X devuelve la posición del día hábil anterior). Imprime,
por mes, las CANTIDADES (nominales) por especie — que es lo que vale para valuar
carteras y lo que NO depende del precio.

Read-only: NO escribe nada. Es para VALIDAR contra el sistema contable antes de
reconstruir nada. Una vez que confirmás que estas cantidades son las correctas,
con la misma receta reconstruimos el dato para todas las cuentas.

Uso:
    python -m scripts.tenencia_fin_de_mes 805 --desde 2025-07 --hasta 2026-05
    python -m scripts.tenencia_fin_de_mes 805 --desde 2025-07 --hasta 2026-05 --ticker AO28
"""
from __future__ import annotations

import sys
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

import holidays

from jobs.aum import _calcular_valuacion, autenticar, consultar_posicion

_FERIADOS = holidays.Argentina()


def _opt(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _es_habil(d: date) -> bool:
    return d.weekday() < 5 and d not in _FERIADOS


def _prox_habil(d: date) -> date:
    d = d + timedelta(days=1)
    while not _es_habil(d):
        d = d + timedelta(days=1)
    return d


def _ultimo_habil_del_mes(anio: int, mes: int) -> date:
    d = date(anio, mes, monthrange(anio, mes)[1])
    while not _es_habil(d):
        d = d - timedelta(days=1)
    return d


def _meses(desde_ym: str, hasta_ym: str) -> list[tuple[int, int]]:
    y0, m0 = (int(x) for x in desde_ym.split("-"))
    y1, m1 = (int(x) for x in hasta_ym.split("-"))
    out, y, m = [], y0, m0
    while (y, m) <= (y1, m1):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _f(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _tenencia_corregida(id_cuenta: str, objetivo: date, headers: dict) -> tuple[dict, dict]:
    """Devuelve (headers_actualizado, {unidad: {cantidad, precio, valuacion}}) a la
    fecha objetivo, pidiendo a Aunesa con desde = objetivo + 1 día hábil."""
    desde = _prox_habil(objetivo).strftime("%d/%m/%Y")
    data, reauth = consultar_posicion(id_cuenta, headers, desde=desde, timeout=240)
    if reauth:
        headers = autenticar()
        data, _ = consultar_posicion(id_cuenta, headers, desde=desde, timeout=240)
    grupos: dict[tuple, dict] = defaultdict(lambda: {"cantidad": 0.0, "precio": 0.0, "tipo": ""})
    if isinstance(data, list):
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
    return headers, out


def main() -> int:
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pos:
        print("Uso: python -m scripts.tenencia_fin_de_mes <id_cuenta> --desde 2025-07 --hasta 2026-05")
        return 1
    id_cuenta = pos[0]
    desde_ym = _opt("--desde", "2025-07")
    hasta_ym = _opt("--hasta", "2026-05")
    filtro = (_opt("--ticker") or "").upper()

    meses = _meses(desde_ym, hasta_ym)
    print("=" * 72)
    print(f"TENENCIA CORREGIDA · cuenta {id_cuenta} · fines de mes {desde_ym} → {hasta_ym}")
    print("  (Aunesa en vivo, fecha corregida desde = último hábil + 1. CANTIDADES = nominales)")
    print("=" * 72)

    headers = autenticar()
    for y, m in meses:
        objetivo = _ultimo_habil_del_mes(y, m)
        headers, ten = _tenencia_corregida(id_cuenta, objetivo, headers)
        especies = sorted(ten.items(), key=lambda kv: -kv[1]["valuacion"])
        if filtro:
            especies = [(u, s) for u, s in especies if filtro in u.upper()]
        total = sum(s["valuacion"] for s in ten.values())
        print(f"\n── {y}-{m:02d}  (último hábil: {objetivo.isoformat()})  "
              f"· {len(ten)} especies · valuación ${total:,.0f}")
        print(f"   {'ESPECIE':<44} {'CANTIDAD':>16} {'PRECIO':>14}")
        for u, s in especies:
            print(f"   {u[:44]:<44} {s['cantidad']:>16,.2f} {s['precio']:>14,.4f}")

    print("\n" + "=" * 72)
    print("Cruzá las CANTIDADES contra tu sistema contable, mes por mes.")
    print("(NO se escribió nada — read-only.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
