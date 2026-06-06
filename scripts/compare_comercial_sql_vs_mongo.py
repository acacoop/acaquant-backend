"""scripts/compare_comercial_sql_vs_mongo.py — compara la vista COMERCIAL Mongo vs SQL.

100% LECTURA. Corre cada función de comercial por los dos caminos (Mongo: api.services.comercial;
SQL: api.services.comercial_sql) sobre una grilla representativa y reporta diffs. Gate del
cutover: hasta 0 diffs no se prende COMERCIAL_SQL. Ver docs/MIGRACION_MONGO_SUPABASE.md.

OJO informe_comercial: Mongo puede leer ComercialCache (precompute) y SQL agrega en vivo →
puede haber diferencias chicas de redondeo/frescura. Si aparecen, evaluar (no es bug de lógica).

    python -m scripts.compare_comercial_sql_vs_mongo [-v]
"""
from __future__ import annotations

import argparse
import json
import sys

from api.services import comercial as M
from api.services import comercial_sql as S

_TOL = 1.0
_IDF = ("operador_email", "id_cuenta", "segmento", "year_month", "unidad", "comprobante",
        "clave", "fecha")


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _close(a, b) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(a - b) <= max(_TOL, abs(b) * 1e-6)


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


def chk(label: str, fn: str, **kw) -> None:
    m = getattr(M, fn).__wrapped__(**kw)
    s = getattr(S, fn)(**kw)
    _R.append((label, diff(_norm(m), _norm(s))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    ops = S.listar_operadores_comercial()
    if not ops:
        print("⚠ COMERCIAL SQL vacío — ¿corriste el sync --full del Chunk 0?")
        return 1
    op = ops[0]["operador_email"]
    od = S.operador_comercial(operador=op)
    idc = od["clientes"][0]["id_cuenta"] if od["clientes"] else None
    seg = (S.informe_cuentas_por_segmento()["segmentos"] or [{}])[0].get("segmento")

    chk("listar_operadores", "listar_operadores_comercial")
    for mon in ("ARS", "USD"):
        chk(f"operador {mon}", "operador_comercial", operador=op, moneda=mon)
        chk(f"operador __todos__ {mon}", "operador_comercial", operador="__todos__", moneda=mon)
        chk(f"analisis {mon}", "analisis_comercial", operador=op, moneda=mon)
        chk(f"informe {mon}", "informe_comercial", moneda=mon)
    chk("analisis __todos__", "analisis_comercial", operador="__todos__")
    for metric in ("volumen", "aum"):
        chk(f"serie {metric}", "serie_comercial", operador=op, metric=metric, moneda="ARS")
    if idc:
        chk("serie cuenta", "serie_comercial", operador=op, metric="volumen",
            moneda="ARS", id_cuenta=idc)
        chk("portafolio", "portafolio_cliente", id_cuenta=idc)
        chk("operaciones", "operaciones_cliente", id_cuenta=idc, limite=300)
    chk("actividad_historica", "actividad_historica", operador=op)
    chk("actividad __todos__", "actividad_historica", operador="__todos__")
    chk("informe_segmento", "informe_cuentas_por_segmento")
    chk("informe_segmento op", "informe_cuentas_por_segmento", operador=op)
    chk("informe_aranceles_seg", "informe_aranceles_segmento", operador=op)
    chk("segmento_detalle todos", "informe_segmento_detalle", segmento="todos")
    if seg:
        chk("segmento_detalle seg", "informe_segmento_detalle", segmento=seg)
    chk("segmento_detalle op", "informe_segmento_detalle", segmento="todos", operador=op)

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
        print(f"❌ {len(fails)} con diferencias — revisar antes de prender COMERCIAL_SQL.")
        return 1
    print("✅ SQL == Mongo en COMERCIAL. Listo para el cutover.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
