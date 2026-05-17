"""diag_retorno_total.py — reproduce el 500 de /api/analitica/retorno-total.

Llama get_retorno_total_data() directo (sin HTTP) para las 3 curvas y
muestra el traceback completo si algo revienta — así se ve la causa
exacta del error 500 que tira la vista Estrategia > Retorno Total.

Corre:  python -m scripts.diag_retorno_total
"""
from __future__ import annotations

import traceback

from api.services.renta_fija import get_retorno_total_data


def main() -> None:
    for curva in ("tasa_fija", "cer", "soberanos"):
        print("=" * 70)
        print(f"curva = {curva}")
        print("=" * 70)
        try:
            r = get_retorno_total_data(curva=curva)
            rows = r.get("rows", [])
            mep = r.get("mep", {})
            ofi = r.get("oficial", {})
            flujos = r.get("flujos", {})
            n_cobros = sum(len(v) for v in flujos.values())
            print(f"  OK — rows={len(rows)}  mep={len(mep)}  oficial={len(ofi)}")
            print(f"  flujos: {len(flujos)} tickers, {n_cobros} cobros totales")
            if rows:
                print(f"  primera fila: {rows[0]}")
            for tk in ("TX26", "TX28"):
                if tk in flujos:
                    print(f"  flujos[{tk}] = {flujos[tk]}")
        except Exception:
            print("  >>> EXCEPCION:")
            traceback.print_exc()
        print()


if __name__ == "__main__":
    main()
