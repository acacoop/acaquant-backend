"""diag_retorno_flujos.py — valida el retorno TOTAL (precio + flujos cobrados).

La vista Estrategia > Retorno Total hoy calcula:
    retorno = precio_final / precio_base − 1
sin sumar cupones ni amortizaciones. Cuando un bono paga cupón o amortiza,
el precio CAE (devuelve plata al tenedor) → la vista lo cuenta como pérdida
fake. Ej. TX26 muestra ~−47% cuando en realidad amortizó capital.

Este script, por ticker, imprime: precio base, precio final, retorno "naive"
(solo precio), los flujos de Trading.Curvas que caen en el período (campos
CRUDOS + monto calculado) y el retorno "total" corregido. Sirve para
confirmar la ESCALA de los flujos ANTES de tocar backend + frontend.

Para CER imprime dos versiones del flujo: nominal y CER-ajustado (cash real
≈ monto_nominal × CER_liquidación / cer_emisión).

Corre:
  python -m scripts.diag_retorno_flujos [--tickers TX26,TX28]
                                        [--desde YYYY-MM-DD] [--hasta YYYY-MM-DD]
"""
from __future__ import annotations

import argparse

from core.mongo import get_mongo_client_read
from engines.curvas import (
    cargar_cer,
    cargar_dias_habiles,
    fecha_flujo,
    get_cer_liquidacion,
    monto_flujo,
    monto_flujo_cer,
    monto_flujo_soberano,
)


def _precio_asof(serie: list[tuple[str, float]], target: str):
    """serie [(fecha, precio)] asc → último (fecha, precio) con fecha <= target."""
    pick = None
    for f, p in serie:
        if f <= target:
            pick = (f, p)
        else:
            break
    return pick


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="TX26,TX28",
                    help="CSV de ticker_corto (default TX26,TX28)")
    ap.add_argument("--desde", help="ISO. Default: primer cierre disponible.")
    ap.add_argument("--hasta", help="ISO. Default: último cierre disponible.")
    args = ap.parse_args()

    cli = get_mongo_client_read()
    trd = cli["Trading"]
    cer_dict = cargar_cer(cli, dias=900)
    dias_habiles = cargar_dias_habiles(cli)

    for tk in [t.strip() for t in args.tickers.split(",") if t.strip()]:
        print("=" * 74)
        print(f"TICKER: {tk}")
        print("=" * 74)

        doc = (trd["Curvas"].find_one({"ticker_corto": tk})
               or trd["Curvas"].find_one({"ticker": tk}))
        if not doc:
            print("  no está en Trading.Curvas — skip.\n")
            continue
        curva = str(doc.get("curva", ""))
        tipo = str(doc.get("tipo", ""))
        cer_emision = doc.get("cer_emision")
        flujos = doc.get("flujos") or []
        print(f"  curva={curva!r}  tipo={tipo!r}  cer_emision={cer_emision}  "
              f"n_flujos={len(flujos)}")

        cur = trd["SnapshotsCierre"].find(
            {"ticker_corto": tk},
            {"_id": 0, "ts_cierre": 1, "ultimo_precio": 1},
        ).sort("ts_cierre", 1)
        serie = [
            (d["ts_cierre"], float(d["ultimo_precio"]))
            for d in cur
            if d.get("ts_cierre") and d.get("ultimo_precio") is not None
        ]
        if len(serie) < 2:
            print(f"  solo {len(serie)} cierres en SnapshotsCierre — sin datos.\n")
            continue

        desde = args.desde or serie[0][0]
        hasta = args.hasta or serie[-1][0]
        base = _precio_asof(serie, desde)
        final = _precio_asof(serie, hasta)
        if not base or not final or base[1] <= 0:
            print("  no hay precio base/final válido para el rango.\n")
            continue
        f_base, p_base = base
        f_final, p_final = final
        print(f"  PRECIO base   {f_base}  =  {p_base:,.2f}")
        print(f"  PRECIO final  {f_final}  =  {p_final:,.2f}")
        ret_naive = (p_final / p_base - 1) * 100
        print(f"  >> retorno NAIVE (solo precio): {ret_naive:+.2f}%")

        es_cer = "cer" in curva.lower()
        es_sob = tipo.lower() in ("globales", "bonares") or "sober" in curva.lower()

        print(f"  FLUJOS con fecha en ({f_base} , {f_final}]:")
        suma_nom = 0.0
        suma_adj = 0.0
        n_win = 0
        for fl in flujos:
            fd = fecha_flujo(fl)
            if not fd:
                continue
            fs = fd.isoformat()
            if fs <= f_base or fs > f_final:
                continue
            n_win += 1
            if es_sob:
                m = monto_flujo_soberano(fl, 100)
                suma_nom += m
                print(f"    {fs}  monto_soberano={m:>10,.4f}   crudo={dict(fl)}")
            elif es_cer:
                m = monto_flujo_cer(fl, 100)
                suma_nom += m
                cer_liq = get_cer_liquidacion(cer_dict, dias_habiles, fs)
                if cer_liq and cer_emision:
                    m_adj = m * cer_liq / float(cer_emision)
                    suma_adj += m_adj
                    adj_txt = f"{m_adj:,.4f}  (cer_liq={cer_liq})"
                else:
                    adj_txt = f"sin CER (cer_liq={cer_liq})"
                print(f"    {fs}  monto_cer_nominal={m:>10,.4f}  "
                      f"cer_ajustado={adj_txt}")
                print(f"           crudo={dict(fl)}")
            else:
                m = monto_flujo(fl)
                suma_nom += m
                print(f"    {fs}  monto_tasafija={m:>10,.4f}   crudo={dict(fl)}")
        if n_win == 0:
            print("    (ningún flujo en el período)")

        print(f"  Σ flujos nominal      = {suma_nom:,.4f}")
        ret_corr_nom = ((p_final + suma_nom) / p_base - 1) * 100
        print(f"  >> retorno CORREGIDO (precio + Σnominal):    {ret_corr_nom:+.2f}%")
        if es_cer:
            print(f"  Σ flujos CER-ajustado = {suma_adj:,.4f}")
            ret_corr_adj = ((p_final + suma_adj) / p_base - 1) * 100
            print(f"  >> retorno CORREGIDO (precio + ΣCER-ajust):  {ret_corr_adj:+.2f}%")
        print()


if __name__ == "__main__":
    main()
