"""compare_renta_fija_sql_vs_mongo.py — GATE del 4° corte (renta fija LIVE → SQL).

Read-only. Compara el path SQL (`renta_fija_sql`) vs Mongo (`renta_fija`) de las 3
funciones migradas, para decidir el cutover de lectura (flag RENTA_FIJA_SQL):

  - get_renta_fija       (snapshot live + flujo_vencimiento + tc_breakeven)
  - listar_curva         (curva enriquecida, por cada curva válida + 'on')
  - get_historico_curva  (serie diaria por ticker)

NO exige byte-parity: el snapshot es LIVE (last_price/TEA/paridad mutan ~1s) → esos
campos driftan por timing y es ESPERADO. El gate exige paridad de **identidad +
estructura + campos estables** (set de tickers, vencimiento, tipo, cer_fijado,
flujo_vencimiento) y reporta el drift de campos live aparte (informativo).

    python -m scripts.compare_renta_fija_sql_vs_mongo
"""
from __future__ import annotations

from api.services import renta_fija as mg
from api.services import renta_fija_sql as sq
from api.services.renta_fija import _CURVAS_VALIDAS

# Campos cuya igualdad EXIGIMOS (identidad/estructura). Los demás (tea/tem/paridad/
# ultimo_precio/duration/…) son LIVE → drift por timing tolerado.
_ESTABLES_CURVA = ("ticker_corto", "tipo", "fecha_vencimiento", "cer_fijado",
                   "is_zero_coupon", "cer_emision", "emisor", "sector", "moneda")
_LIVE_CURVA = ("tea", "tem", "paridad", "ultimo_precio", "duration", "mod_duration",
               "convexity", "total_nominals_dia", "tc_breakeven", "meses_al_vto")


def _idx(rows: list[dict], key: str) -> dict:
    return {r.get(key): r for r in rows if r.get(key)}


def _cmp_sets(nombre: str, a: set, b: set) -> bool:
    """a=SQL, b=Mongo. True si idénticos. Reporta los que faltan de cada lado."""
    solo_sql, solo_mg = a - b, b - a
    ok = not solo_sql and not solo_mg
    estado = "✅ IDÉNTICO" if ok else "❌ DIFIERE"
    print(f"  {nombre:<34} SQL={len(a):>4}  Mongo={len(b):>4}  {estado}")
    if solo_sql:
        print(f"      solo en SQL ({len(solo_sql)}): {', '.join(map(str, list(solo_sql)[:8]))}")
    if solo_mg:
        print(f"      solo en Mongo ({len(solo_mg)}): {', '.join(map(str, list(solo_mg)[:8]))}")
    return ok


def _cmp_estables(nombre: str, isql: dict, img: dict) -> tuple[int, int]:
    """Compara campos ESTABLES de los tickers en común. Devuelve (drift_estable, drift_live)."""
    drift_est = drift_live = 0
    for tk in set(isql) & set(img):
        s, m = isql[tk], img[tk]
        for f in _ESTABLES_CURVA:
            if f in s or f in m:
                if str(s.get(f)) != str(m.get(f)):
                    drift_est += 1
                    print(f"      ⚠ ESTABLE {tk}.{f}: SQL={s.get(f)!r} vs Mongo={m.get(f)!r}")
        drift_live += sum(1 for f in _LIVE_CURVA if str(s.get(f)) != str(m.get(f)))
    return drift_est, drift_live


def main() -> int:
    fallas = 0

    print("\n═══ get_renta_fija (snapshot live) ═══")
    rf_sql = {d["instrumento"]: d for d in sq.get_renta_fija()}
    rf_mg = {d["instrumento"]: d for d in mg.get_renta_fija()}
    if not _cmp_sets("instrumentos", set(rf_sql), set(rf_mg)):
        fallas += 1
    # flujo_vencimiento es estable (de curvas) → debe coincidir donde ambos lo tienen.
    fv_drift = 0
    for tk in set(rf_sql) & set(rf_mg):
        fs = (rf_sql[tk].get("metrics") or {}).get("flujo_vencimiento")
        fm = (rf_mg[tk].get("metrics") or {}).get("flujo_vencimiento")
        if str(fs) != str(fm):
            fv_drift += 1
            print(f"      ⚠ flujo_vencimiento {tk}: SQL={fs} vs Mongo={fm}")
    print(f"  flujo_vencimiento drift: {fv_drift} (debería ser 0)")
    if fv_drift:
        fallas += 1

    print("\n═══ listar_curva (por curva) ═══")
    curvas = sorted(_CURVAS_VALIDAS) + ["on"]
    tot_est = tot_live = 0
    for curva in curvas:
        a = sq.listar_curva(curva)
        b = mg.listar_curva(curva)
        ok = _cmp_sets(f"curva={curva}", set(_idx(a, "ticker")), set(_idx(b, "ticker")))
        if not ok:
            fallas += 1
        de, dl = _cmp_estables(curva, _idx(a, "ticker"), _idx(b, "ticker"))
        tot_est += de
        tot_live += dl
        if de:
            fallas += 1
    print(f"\n  Drift campos ESTABLES (debe ser 0): {tot_est}")
    print(f"  Drift campos LIVE (tolerado, timing ~1s): {tot_live}")

    print("\n═══ get_historico_curva (serie diaria) ═══")
    for curva in ("cer", "tasa_fija"):
        a = sq.get_historico_curva(curva)
        b = mg.get_historico_curva(curva)
        fa = {r.get("fecha") for r in a}
        fb = {r.get("fecha") for r in b}
        _cmp_sets(f"fechas curva={curva}", fa, fb)
        print(f"      filas: SQL={len(a)}  Mongo={len(b)}")

    print("\n" + "═" * 60)
    if fallas:
        print(f"❌ GATE: {fallas} discrepancia(s) de IDENTIDAD/ESTRUCTURA — NO prender RENTA_FIJA_SQL.")
        print("   (El drift de campos LIVE es esperado y NO cuenta como falla.)")
    else:
        print("✅ GATE OK: identidad + estructura + estables en paridad. Se puede prender RENTA_FIJA_SQL=1.")
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main())
