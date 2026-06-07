"""scripts/compare_pnl_sql_vs_mongo.py — valida PnL Títulos SQL == Mongo.

100% LECTURA. Compara pnl_por_cuenta (Mongo) vs pnl_por_cuenta_sql (SQL, mismo motor cost-basis,
datos de Postgres) para una muestra de cuentas del último snapshot. Gate del flag PNL_SQL.

    python -m scripts.compare_pnl_sql_vs_mongo [-n 30] [-v]
"""
from __future__ import annotations

import argparse
import json
import sys

from psycopg.rows import dict_row

from api.services import pnl as M
from api.services import pnl_sql as S
from core.postgres import get_pool

_TOL = 1.0


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _close(a, b) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(a - b) <= max(_TOL, abs(b) * 1e-6)


def _skey(d) -> str:
    if isinstance(d, dict):
        for k in ("ticker", "match_key", "comprobante", "fecha"):
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30, help="cuentas a muestrear")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT DISTINCT id_cuenta FROM aum WHERE fecha_snapshot = "
                    "(SELECT max(fecha_snapshot) FROM aum) ORDER BY id_cuenta LIMIT %s",
                    (args.n,))
        cuentas = [r["id_cuenta"] for r in cur.fetchall()]
    if not cuentas:
        print("⚠ aum SQL vacía — ¿sync?")
        return 1

    fails = 0
    for idc in cuentas:
        m = M.pnl_por_cuenta.__wrapped__(id_cuenta=idc)
        s = S.pnl_por_cuenta_sql(id_cuenta=idc)
        d = diff(m, s, idc)
        if d:
            fails += 1
            print(f"[FAIL] cuenta {idc}")
            for x in d[:6]:
                print(f"        {x}")
        elif args.v:
            print(f"[ok] {idc}")
    print(f"\n{len(cuentas) - fails}/{len(cuentas)} cuentas OK.")
    if fails:
        print(f"❌ {fails} con diferencias — NO prender PNL_SQL.")
        return 1
    print("✅ PnL SQL == Mongo. Seguro para PNL_SQL=1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
