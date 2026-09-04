"""scripts/add_cedear.py — alta/edición de un CEDEAR en el master `mercado.cedears`.

⚠️ **Desde el 2026-09-04 el camino normal es el AV AGENT**: la habilidad
`cedear_faltante` lista lo que Primary cotiza y no tenemos, y el arreglo
`alta_cedear` (ENCONTRÓ → tildar → dar de alta) verifica el símbolo contra
Primary, escribe el master, trae la historia EOD y el ADR, y el motor lo
suscribe solo (`docs/AGENT.md` §0.dl). Este script queda para lo que el agente
no ofrece: un ADR/ETF **sin pata BYMA** (`sin_cedear`) o un alta a ciegas desde
la consola. **Escribe por la MISMA puerta** (`core.cedears_sql.alta`): no hay
dos ideas de qué es dar de alta.

UN doc en `mercado.cedears` (activo=True) cablea TODO el universo de renta variable:
  - `engines/motor_cedears` lo suscribe (relee el master cada 60 s: sin reiniciar).
  - `jobs/adr_live` trae el ADR del `underlying` (símbolo US) cada 15'.
  - `jobs/precios_acciones_daily` trae los precios EOD (Yahoo) del underlying.
  - el Scanner (`/renta-variable`) y Manager → TÍTULOS → RENTA VARIABLE lo muestran.

`ratio_cedear` es SOLO display (el scanner lo pasa al front). NO afecta el tracking.
Sin `--ticker` → `sin_cedear=True` (ETF/ADR US sin cedear BYMA; columnas CEDEAR vacías).

Idempotente: upsert por `ticker` (el símbolo BYMA, PK). Dry-run por default; aplica con --apply.

    python -m scripts.add_cedear --ticker-corto SPCX --underlying SPCX \
        --ticker "MERV - XMEV - SPCX - 24hs" --nombre SPCX            # dry-run
    python -m scripts.add_cedear ... --apply                          # aplica
"""
import argparse

from psycopg.types.json import Jsonb

from core import cedears_sql
from core.postgres import get_pool


def _alta_sin_cedear(tc: str, underlying: str, nombre: str, sector, ratio) -> None:
    """ADR/ETF sin pata BYMA: no hay símbolo que suscribir, así que no pasa por
    la puerta del alta (que exige uno). Se guarda con `ticker = ticker_corto`,
    la convención de siempre para estas filas."""
    doc = {"ticker": tc, "ticker_corto": tc, "underlying": underlying, "nombre": nombre,
           "sector": sector, "ratio_cedear": ratio, "sin_cedear": True, "activo": True}
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO mercado.cedears (ticker, ticker_corto, underlying, activo, data) "
            "VALUES (%s, %s, %s, true, %s) "
            "ON CONFLICT (ticker) DO UPDATE SET ticker_corto = EXCLUDED.ticker_corto, "
            "underlying = EXCLUDED.underlying, activo = true, "
            "data = COALESCE(mercado.cedears.data, '{}'::jsonb) || EXCLUDED.data",
            (tc, tc, underlying, Jsonb({k: v for k, v in doc.items() if v is not None})))
        conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker-corto", required=True, help="ej. SPCX")
    ap.add_argument("--underlying", default=None, help="símbolo US (default = ticker-corto)")
    ap.add_argument("--ticker", default=None,
                    help="ticker BYMA del cedear (ej. 'MERV - XMEV - SPCX - 24hs'). "
                         "Si NO se pasa → sin_cedear=True (ADR/ETF only).")
    ap.add_argument("--nombre", default=None)
    ap.add_argument("--sector", default=None)
    ap.add_argument("--ratio", type=float, default=None, help="ratio CEDEAR:acción (solo display)")
    ap.add_argument("--apply", action="store_true", help="aplica (sin esto = dry-run)")
    args = ap.parse_args()

    tc = args.ticker_corto.upper()
    underlying = (args.underlying or tc).upper()
    nombre = args.nombre or tc
    sin_cedear = not args.ticker

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM mercado.cedears WHERE ticker = %s", (args.ticker or tc,))
        existe = cur.fetchone() is not None
    print(f"{'APLICA' if args.apply else 'DRY-RUN'} — upsert mercado.cedears ticker_corto={tc}")
    print(f"  ya existe: {'sí' if existe else 'no'}  ·  sin_cedear: {sin_cedear}")
    print(f"  símbolo={args.ticker or tc!r} underlying={underlying!r} nombre={nombre!r} "
          f"sector={args.sector!r} ratio={args.ratio!r}")
    if not args.apply:
        print("\n(dry-run) Revisá el doc y re-corré con --apply.")
        return 0

    if sin_cedear:
        _alta_sin_cedear(tc, underlying, nombre, args.sector, args.ratio)
    else:
        r = cedears_sql.alta(tc, simbolo=args.ticker, underlying=underlying, nombre=nombre,
                             sector=args.sector, ratio_cedear=args.ratio, actor="scripts.add_cedear")
        print(f"  antes: {r['antes']}  →  después: {r['despues']}")
    print("\n✅ upsert OK en mercado.cedears.")
    print("Próxima corrida de precios_acciones_daily / adr_live ya lo incluyen.")
    if not sin_cedear:
        print("El motor lo suscribe solo en ≤ 60 s si está corriendo (relee el master); "
              "si no, al próximo arranque.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
