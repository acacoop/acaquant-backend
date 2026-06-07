"""scripts/compare_portfolio_sql_vs_mongo.py — compara la vista AuM/PORTFOLIO Mongo vs SQL.

100% LECTURA. Compara las funciones de AuM ya migradas (chunks 1-2): listar_aum, listar_cuentas,
fci_serie, fci_snapshot, total_serie, total_snapshot, total_diff. Gate del flag PORTFOLIO_SQL.
NO cubre tasa-fija/cer/pnl (aún en Mongo). Ver docs/MIGRACION_MONGO_SUPABASE.md.

    python -m scripts.compare_portfolio_sql_vs_mongo [-v]
"""
from __future__ import annotations

import argparse
import json
import sys

from api.services import portfolio as M
from api.services import portfolio_sql as S

_TOL = 1.0
_IDF = ("id_cuenta", "unidad", "fecha", "cuenta", "emisor", "cartera")


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _close(a, b) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(a - b) <= max(_TOL, abs(b) * 1e-6)


def _norm(x):
    if isinstance(x, float) and x != x:   # NaN (tipoTitulo sucio en Mongo)
        return "(sin)"
    if x == "" or x == "nan":             # '' o el string 'nan' que dejó el sync viejo
        return "(sin)"
    if hasattr(x, "isoformat"):  # datetime/date → ISO (listar_aum devuelve datetime)
        return x.isoformat()[:10]
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


def _skey(d) -> str:
    # Clave COMPUESTA: una fila de aum/fci/total tiene varias unidades por id_cuenta →
    # alinear por id_cuenta+unidad+fecha (no por el primer campo, que se repite).
    if isinstance(d, dict):
        parts = [f"{k}={d[k]}" for k in _IDF if k in d]
        if parts:
            return "|".join(parts)
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


def chk(label: str, fn: str, **kw) -> None:
    m = getattr(M, fn).__wrapped__(**kw) if hasattr(getattr(M, fn), "__wrapped__") \
        else getattr(M, fn)(**kw)
    s = getattr(S, fn)(**kw)
    _R.append((label, diff(_norm(m), _norm(s))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    cuentas = S.listar_cuentas()
    if not cuentas:
        print("⚠ aum SQL vacío — ¿corriste el sync --full del Chunk 0?")
        return 1
    idc = cuentas[0]["id_cuenta"]
    # fecha del último snapshot (de total_serie sale la última fecha).
    ts = S.total_serie()
    f0 = ts["serie"][-1]["fecha"] if ts["serie"] else None
    fant = ts["serie"][-2]["fecha"] if len(ts["serie"]) > 1 else f0

    chk("cuentas", "listar_cuentas", scope=None)
    chk("aum ultimo", "listar_aum", ultimo=True, scope=None)
    chk("aum x cuenta", "listar_aum", id_cuenta=idc, scope=None)
    for filtro in ("todas", "accionistas", "sin_accionistas", "cooperativas", "productores"):
        chk(f"fci_serie {filtro}", "fci_serie", cuenta_filter=filtro, scope=None)
        chk(f"total_serie {filtro}", "total_serie", cuenta_filter=filtro, scope=None)
    chk("total_serie USD", "total_serie", moneda="USD", scope=None)
    if f0:
        chk("fci_snapshot", "fci_snapshot", fecha=f0, scope=None)
        chk("total_snapshot", "total_snapshot", fecha=f0, scope=None)
        chk("total_snapshot USD", "total_snapshot", fecha=f0, moneda="USD", scope=None)
        chk("diff", "total_diff", fecha_actual=f0, fecha_anterior=fant, scope=None)
        chk("diff USD", "total_diff", fecha_actual=f0, fecha_anterior=fant,
            moneda="USD", scope=None)

    fails = [(l, d) for l, d in _R if d]
    for l, d in _R:
        if d or args.v:
            print(f"[{'FAIL' if d else 'ok'}] {l}")
            for x in d[:8]:
                print(f"        {x}")
            if len(d) > 8:
                print(f"        … (+{len(d) - 8} más)")
    print(f"\n{len(_R) - len(fails)}/{len(_R)} checks OK.")
    if fails:
        print(f"❌ {len(fails)} con diferencias — revisar antes de prender PORTFOLIO_SQL.")
        return 1
    print("✅ SQL == Mongo en AuM (chunks 1-2). Listo para el cutover parcial.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
