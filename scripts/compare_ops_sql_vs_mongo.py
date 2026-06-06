"""scripts/compare_ops_sql_vs_mongo.py — compara la vista OPERACIONES Mongo vs SQL.

100% LECTURA. Corre cada endpoint /ops/* por los DOS caminos (Mongo, el actual; SQL, el
nuevo de api/services/operaciones_sql) sobre una grilla de params representativos, normaliza
y reporta diferencias. Sale con código ≠0 si encuentra cualquiera → es el GATE del cutover:
mientras no dé 0 diffs, NO se prende OPERACIONES_SQL.

Los handlers Mongo están decorados con @cached → se llaman vía `.__wrapped__` para saltear
el cache. El scope se pasa None (vista admin) salvo un caso scopeado.

    python -m scripts.compare_ops_sql_vs_mongo
    python -m scripts.compare_ops_sql_vs_mongo -v   # imprime cada check (no solo los FAIL)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from api.routers import operaciones as M
from api.services import operaciones_sql as S

_TOL_ABS = 0.5  # pesos/toneladas; los counts (int) se comparan exactos


def _isnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _num_close(a, b) -> bool:
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    return abs(a - b) <= max(_TOL_ABS, abs(b) * 1e-7)


def _canon(x):
    import json
    return json.dumps(x, sort_keys=True, default=str)


# Campos identidad para alinear filas entre las dos listas (NO ordenar por el JSON
# completo: el arancel/bruto difiere por sub-centavos de redondeo rollup-vs-live y
# desalinea las filas → falsos positivos).
_IDF = ("fecha", "periodo", "clave", "operacion", "denominacion", "instrumento",
        "cuenta", "boleto", "comprobante", "c", "d", "i", "p")


def _norm(x):
    """'' → '(sin)' (el sync normaliza ''→NULL y COALESCE lo muestra '(sin)'; Mongo deja
    '' vía $ifNull). Cosmético, los montos no cambian. Aplica a ambos lados."""
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
    return _canon(d)


def diff(a, b, path: str = "") -> list[str]:
    out: list[str] = []
    if _isnum(a) and _isnum(b):
        if not _num_close(a, b):
            out.append(f"{path}: mongo={a} sql={b}")
    elif isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: falta en MONGO")
            elif k not in b:
                out.append(f"{path}.{k}: falta en SQL")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        sa = sorted(a, key=_skey)
        sb = sorted(b, key=_skey)
        if len(sa) != len(sb):
            out.append(f"{path}: len mongo={len(sa)} sql={len(sb)}")
        else:
            for i, (x, y) in enumerate(zip(sa, sb, strict=True)):
                out += diff(x, y, f"{path}[{i}]")
    elif a != b:
        out.append(f"{path}: mongo={a!r} sql={b!r}")
    return out


_RESULTS: list[tuple[str, list[str]]] = []


def chk(label: str, mfn, sfn, **kw) -> None:
    m = mfn.__wrapped__(**kw) if hasattr(mfn, "__wrapped__") else mfn(**kw)
    s = sfn(**kw)
    if label == "cuentas-list":
        # denominacion es no-determinística (Mongo $first arbitrario vs SQL max): la misma
        # cuenta tiene varias grafías en distintos boletos. Comparamos solo el set de ids.
        m = sorted(r["cuenta"] for r in m["cuentas"])
        s = sorted(r["cuenta"] for r in s["cuentas"])
    _RESULTS.append((label, diff(_norm(m), _norm(s))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", action="store_true", help="imprime todos los checks")
    args = ap.parse_args()

    # ── valores reales para la grilla (data-driven) ──
    fechas = S.ops_fechas()["fechas"]
    if not fechas:
        print("⚠ operaciones SQL vacía — ¿corriste el sync --full?")
        return 1
    f0 = fechas[0]["fecha"]                       # día más reciente con data
    f0d = date.fromisoformat(f0)
    mes0 = f"{f0[:7]}-01"                         # inicio de mes
    ano0 = (f0d - timedelta(days=365)).isoformat()
    rangos = [(f0, f0), (mes0, f0), (ano0, f0)]   # 1 día, MTD, ~1 año

    mercados = S.ops_mercados()["mercados"]
    merc = mercados[0] if mercados else None
    segmentos = S.ops_segmentos()["segmentos"]
    seg = segmentos[0] if segmentos else None

    # operacion/denominacion reales: del resumen del último año.
    base_res = S.ops_resumen(moneda="ARS", desde=ano0, hasta=f0)
    op0 = base_res["por_operacion"][0]["operacion"] if base_res["por_operacion"] else None
    den0 = base_res["por_denominacion"][0]["denominacion"] if base_res["por_denominacion"] else None

    # ── selectores (raw) ──
    chk("mercados", M.ops_mercados, S.ops_mercados)
    chk("segmentos", M.ops_segmentos, S.ops_segmentos)
    chk("fechas", M.ops_fechas, S.ops_fechas)
    chk("cuentas-list", M.ops_cuentas_list, S.ops_cuentas_list, scope=None)
    chk("meta(con data)", M.ops_meta, S.ops_meta, fecha=f0)
    chk("meta(día vacío)", M.ops_meta, S.ops_meta, fecha="1990-01-01")

    # ── serie ──
    for mon in ("ARS", "USD"):
        for mrc in (None, "todos", merc):
            chk(f"serie m={mon} merc={mrc}", M.ops_serie, S.ops_serie,
                moneda=mon, mercado=mrc, operacion=None, denominacion=None,
                cuenta=None, segmento=None, scope=None)
    chk(f"serie seg={seg}", M.ops_serie, S.ops_serie, moneda="ARS", mercado=None,
        operacion=None, denominacion=None, cuenta=None, segmento=seg, scope=None)

    # ── resumen (incl. cross-filter en ambas direcciones) ──
    for desde, hasta in rangos:
        chk(f"resumen {desde}..{hasta}", M.ops_resumen, S.ops_resumen, moneda="ARS",
            mercado=None, desde=desde, hasta=hasta, operacion=None, denominacion=None,
            cuenta=None, segmento=None, scope=None)
    chk("resumen xf=operacion", M.ops_resumen, S.ops_resumen, moneda="ARS", mercado=None,
        desde=ano0, hasta=f0, operacion=op0, denominacion=None, cuenta=None,
        segmento=None, scope=None)
    chk("resumen xf=denominacion", M.ops_resumen, S.ops_resumen, moneda="ARS", mercado=None,
        desde=ano0, hasta=f0, operacion=None, denominacion=den0, cuenta=None,
        segmento=None, scope=None)

    # ── boletos ──
    chk("boletos", M.ops_boletos, S.ops_boletos, desde=mes0, hasta=f0, moneda="ARS",
        denominacion=None, cuenta=None, operacion=None, mercado=None, segmento=None, scope=None)
    chk("boletos den", M.ops_boletos, S.ops_boletos, desde=ano0, hasta=f0, moneda="ARS",
        denominacion=den0, cuenta=None, operacion=None, mercado=None, segmento=None, scope=None)

    # ── aranceles (la grilla más densa: dim × sel_dim × serie_full) ──
    for dim in ("nivel3", "operacion", "operador"):
        ar = S.ops_aranceles(desde=ano0, hasta=f0, dim=dim)
        sel = ar["por_dim"][0]["clave"] if ar["por_dim"] else None
        for sd in (None, sel):
            chk(f"aranceles dim={dim} sel={sd}", M.ops_aranceles, S.ops_aranceles,
                moneda="ARS", desde=ano0, hasta=f0, agg="MENSUAL", cuenta=None,
                instrumento=None, sel_dim=sd, segmento=None, dim=dim, serie_full=False,
                scope=None)
    chk("aranceles serie_full", M.ops_aranceles, S.ops_aranceles, moneda="ARS",
        desde=ano0, hasta=f0, agg="MENSUAL", cuenta=None, instrumento=None, sel_dim=None,
        segmento=None, dim="nivel3", serie_full=True, scope=None)
    chk("aranceles DIARIO", M.ops_aranceles, S.ops_aranceles, moneda="ARS", desde=mes0,
        hasta=f0, agg="DIARIO", cuenta=None, instrumento=None, sel_dim=None, segmento=None,
        dim="nivel3", serie_full=False, scope=None)

    # ── agro ──
    for agg in ("MENSUAL", "DIARIO"):
        for comm in (None, "SOJA"):
            chk(f"agro agg={agg} comm={comm}", M.ops_agro, S.ops_agro, desde=ano0,
                hasta=f0, agg=agg, commodity=comm, cuenta=None, scope=None)

    # ── reporte ──
    fails = [(lbl, ds) for lbl, ds in _RESULTS if ds]
    for lbl, ds in _RESULTS:
        if ds or args.v:
            estado = "FAIL" if ds else "ok"
            print(f"[{estado}] {lbl}")
            for d in ds[:8]:
                print(f"        {d}")
            if len(ds) > 8:
                print(f"        … (+{len(ds) - 8} más)")
    print(f"\n{len(_RESULTS) - len(fails)}/{len(_RESULTS)} checks OK.")
    if fails:
        print(f"❌ {len(fails)} con diferencias — NO prender OPERACIONES_SQL todavía.")
        return 1
    print("✅ SQL == Mongo en toda la grilla. Listo para el cutover.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
