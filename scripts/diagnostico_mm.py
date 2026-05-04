"""diagnostico_mm.py — testea los 6 endpoints MM Microstructure llamando los
services directo (sin HTTP, sin auth, sin headers). Si esto anda, el HTTP 500
del frontend es problema de auth/proxy, no del código.

Uso:
    python -m scripts.diagnostico_mm
"""
import traceback

from api.services import mm_microstructure as svc

TICKER = svc.DEFAULT_TICKER


def _try(label: str, fn):
    print(f"\n[{label}]")
    try:
        result = fn()
        if isinstance(result, dict) and result.get("error"):
            print(f"    [WARN] error reportado: {result['error']}")
        # Resumen del result según shape.
        if isinstance(result, dict):
            keys = list(result.keys())
            print(f"    [OK]   keys: {keys}")
            # Algunas snapshots útiles:
            if "metrics" in result:
                m = result["metrics"]
                print(f"           mid={m.get('mid')} micro={m.get('microprice')} "
                      f"obi={m.get('obi')} qs_bps={m.get('qs_bps')}")
            if "n" in result and "trades" in result:
                print(f"           n_trades={result['n']}")
                if result["trades"]:
                    t0 = result["trades"][0]
                    print(f"           primer trade: ts={t0.get('timestamp')} "
                          f"price={t0.get('price')} side={t0.get('side')} "
                          f"es_bps={t0.get('es_bps')}")
            if "buckets" in result:
                buckets = result["buckets"]
                print(f"           n_buckets={len(buckets)}")
                if buckets:
                    print(f"           primer: {buckets[0]}")
            if "permanent_impact" in result:
                pi = result["permanent_impact"]
                ti = result["temporary_impact"]
                print(f"           b={pi.get('b')} (R²={pi.get('r2')}, "
                      f"n={pi.get('n_buckets')})")
                print(f"           k={ti.get('k')} (R²={ti.get('r2')}, "
                      f"n={ti.get('n_trades')})")
            if "n_obs" in result:
                print(f"           n_obs={result['n_obs']} skew={result.get('skewness')} "
                      f"kurt={result.get('kurtosis')}")
        else:
            print(f"    [OK]   result tipo {type(result).__name__}")
    except Exception:
        print("    [!! ] EXCEPTION:")
        for line in traceback.format_exc().splitlines():
            print(f"          {line}")


def main():
    print("=" * 60)
    print(" Diagnóstico MM Microstructure")
    print(f" Ticker: {TICKER}")
    print("=" * 60)

    _try("1/6 live",
         lambda: svc.get_live(ticker=TICKER))

    _try("2/6 tape (últimos 30 min)",
         lambda: svc.get_tape(ticker=TICKER, ventana_min=30, limit=200))

    _try("3/6 intraday (último día con trades)",
         lambda: svc.get_intraday(ticker=TICKER, bucket_min=5))

    _try("4/6 impact (5 días)",
         lambda: svc.get_impact(ticker=TICKER, dias=5, bucket_min=1))

    _try("5/6 smile (5 días, buckets 30 min)",
         lambda: svc.get_smile(ticker=TICKER, dias=5, bucket_min=30))

    _try("6/6 stylized facts (5 días, buckets 1 min)",
         lambda: svc.get_stylized_facts(ticker=TICKER, dias=5, bucket_min=1))

    print("\n" + "=" * 60)
    print(" Si todo dice [OK], el código del service está bien.")
    print(" Si algún [WARN] dice 'menos de 30 obs' o similar, es data,")
    print(" no bug.")
    print(" Si hay [!! ] EXCEPTION, ese es el bug real — copiar el")
    print(" traceback completo al asistente.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    main()
