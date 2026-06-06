"""scripts/compare_negocio_sql_vs_mongo.py — compara la vista NEGOCIO Mongo vs SQL.

100% LECTURA. Corre cada endpoint /negocio/* por los dos caminos (Mongo actual, SQL nuevo)
sobre una grilla representativa, normaliza y reporta diferencias. Gate del cutover: hasta que
no dé 0 diffs, NO se prende NEGOCIO_SQL. Ver docs/MIGRACION_MONGO_SUPABASE.md.

    python -m scripts.compare_negocio_sql_vs_mongo [-v]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from api.routers import operaciones as M
from api.services import negocio_sql as S

_TOL = 0.5
_IDF = ("comprobante", "cuenta", "fecha")


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _close(a, b) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(a - b) <= max(_TOL, abs(b) * 1e-7)


def _norm(x):
    if x == "":
        return "(sin)"
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


def _skey(d) -> str:
    if isinstance(d, dict):
        for k in _IDF:
            if k in d:
                return str(d[k])
    return json.dumps(d, sort_keys=True, default=str)


def diff(a, b, path: str = "") -> list[str]:
    out: list[str] = []
    if _isnum(a) and _isnum(b):
        if not _close(a, b):
            out.append(f"{path}: mongo={a} sql={b}")
    elif isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: falta en {'MONGO' if k not in a else 'SQL'}")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        sa, sb = sorted(a, key=_skey), sorted(b, key=_skey)
        if len(sa) != len(sb):
            out.append(f"{path}: len mongo={len(sa)} sql={len(sb)}")
        else:
            for i, (x, y) in enumerate(zip(sa, sb, strict=True)):
                out += diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: mongo={a!r} sql={b!r}")
    return out


_R: list[tuple[str, list[str]]] = []


def chk(label: str, mfn, sfn, **kw) -> None:
    m = mfn.__wrapped__(**kw) if hasattr(mfn, "__wrapped__") else mfn(**kw)
    s = sfn(**kw)
    _R.append((label, diff(_norm(m), _norm(s))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    fechas = S.negocio_fechas()["fechas"]
    if not fechas:
        print("⚠ negocio_movimientos SQL vacía — ¿corriste el sync --full?")
        return 1
    f0 = fechas[0]["fecha"]
    mes0 = f"{f0[:7]}-01"
    ano0 = (date.fromisoformat(f0) - timedelta(days=365)).isoformat()
    filtros = ("todas", "accionistas", "sin_accionistas", "cooperativas", "productores")

    chk("fechas", M.negocio_fechas, S.negocio_fechas)
    chk("cuentas-list", M.negocio_cuentas_list, S.negocio_cuentas_list, scope=None)
    chk("meta(con data)", M.negocio, S.negocio, fecha=f0)
    chk("meta(vacío)", M.negocio, S.negocio, fecha="1990-01-01")

    for mon in ("ARS", "USD"):
        for cf in filtros:
            chk(f"serie {mon}/{cf}", M.negocio_serie, S.negocio_serie,
                moneda=mon, cuenta_filter=cf, cuenta=None, scope=None)

    for cat in ("compra", "venta", "suscripciones", "cauc_tom", "cauc_col"):
        chk(f"cuentas {cat}", M.negocio_cuentas, S.negocio_cuentas, moneda="ARS",
            cuenta_filter="todas", categoria=cat, desde=ano0, hasta=f0, cuenta=None, scope=None)
    for cf in filtros:
        chk(f"matrix {cf}", M.negocio_cuentas_matrix, S.negocio_cuentas_matrix, moneda="ARS",
            cuenta_filter=cf, desde=mes0, hasta=f0, cuenta=None, scope=None)

    # boletos: una cuenta con data en f0 (top del matrix de ese día).
    mtx = S.negocio_cuentas_matrix(moneda="ARS", cuenta_filter="todas", desde=f0, hasta=f0)
    if mtx["cuentas"]:
        cta = mtx["cuentas"][0]["cuenta"]
        chk("boletos", M.negocio_boletos, S.negocio_boletos, fecha=f0, cuenta=cta,
            moneda="ARS", categoria=None, scope=None)
        chk("boletos USD", M.negocio_boletos, S.negocio_boletos, fecha=f0, cuenta=cta,
            moneda="USD", categoria=None, scope=None)

    fails = [(l, d) for l, d in _R if d]
    for l, d in _R:
        if d or args.v:
            print(f"[{'FAIL' if d else 'ok'}] {l}")
            for x in d[:8]:
                print(f"        {x}")
    print(f"\n{len(_R) - len(fails)}/{len(_R)} checks OK.")
    if fails:
        print(f"❌ {len(fails)} con diferencias — NO prender NEGOCIO_SQL todavía.")
        return 1
    print("✅ SQL == Mongo en NEGOCIO. Listo para el cutover.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
