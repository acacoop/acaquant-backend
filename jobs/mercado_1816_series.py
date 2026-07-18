"""jobs/mercado_1816_series.py — baja las series históricas de 1816 a Postgres.

Doc madre: docs/VISTA_RESEARCH.md. Alimenta `research.mkt_1816_series` (el
laboratorio de series/spreads de la vista RESEARCH). Feed SEPARADO de mercado.curvas
(nuestro motor RF de hoy) — no se pisan.

Dos modos:
  - `--backfill`: baja 1 AÑO (una vez, la carga inicial). Scopeado + batcheado
    (≤10 tickers/call, series topea ahí) + throttle del cliente + chequeo de
    créditos ANTES (REGLA #4). Correr FUERA de rueda.
  - default (diario, cron post-cierre): baja los últimos días y upsertea
    (idempotente por PK → re-correr no duplica ni re-paga de más).

Uso:
    python -m jobs.mercado_1816_series --dry-run       # universo + costo, sin pegar
    python -m jobs.mercado_1816_series --backfill       # carga inicial 1 año
    python -m jobs.mercado_1816_series                  # diario (últimos 7 días)
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from core import mercado_1816
from core.postgres import get_pool

logger = logging.getLogger(__name__)

# Campos que se guardan. 4 esenciales para el laboratorio de spreads/valor relativo:
# tea (la tasa, para spreads A−B), paridad (nivel sin ruido de cupón), precioClean y
# duration (para curva/DV01). Se dejó tna (redundante con tea) y spread afuera para
# que el backfill de ~60 bonos entre en el límite diario (60×4×364 ≈ 87k < 100k).
_CAMPOS = ["tea", "paridad", "precioClean", "duration"]

# Seed si research.mkt_1816_watch está vacío: soberanos + hard dollar (para los
# spreads AL−GD, pendiente de curva, etc.). Se amplía editando la tabla watch.
_SEED = ["AL29", "AL30", "AL35", "AL41",
         "GD29", "GD30", "GD35", "GD38", "GD41", "GD46",
         "AE38", "AO28", "AO29"]

_BATCH = 10   # /series admite hasta 10 tickers por llamada


def _universo(cur) -> list[str]:
    cur.execute("SELECT ticker FROM research.mkt_1816_watch WHERE activo "
                "ORDER BY orden NULLS LAST, ticker")
    filas = [r[0] for r in cur.fetchall()]
    return filas or _SEED


def _ya_backfilleados(cur, minimo_dias: int = 250) -> set[str]:
    """Tickers que YA tienen ≥ minimo_dias de historia → en backfill no se re-bajan
    (resumible: si el pull se corta o se hace en 2 tandas por el límite diario, la
    próxima corrida sigue con los que faltan, sin re-pagar créditos)."""
    cur.execute(
        "SELECT ticker FROM research.mkt_1816_series "
        "GROUP BY ticker HAVING count(DISTINCT fecha) >= %s",
        (minimo_dias,),
    )
    return {r[0] for r in cur.fetchall()}


def _lotes(xs: list[str], n: int):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def _upsert(filas: list[dict]) -> int:
    if not filas:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO research.mkt_1816_series "
            "(ticker,fecha,campo,valor,fuente,moneda,plazo,convencion_tna) VALUES "
            "(%(ticker)s,%(fecha)s,%(campo)s,%(valor)s,%(fuente)s,%(moneda)s,"
            "%(plazo)s,%(convencion_tna)s) "
            "ON CONFLICT (ticker,fecha,campo,fuente,moneda,plazo) "
            "DO UPDATE SET valor = EXCLUDED.valor, ingestado_en = now()",
            filas,
        )
    return len(filas)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="baja 1 año (carga inicial, una vez). Default: últimos días.")
    ap.add_argument("--dias", type=int, default=7,
                    help="ventana del modo diario (default 7, cubre correcciones/feriados)")
    ap.add_argument("--dry-run", action="store_true",
                    help="muestra universo + costo estimado; NO pega a la API ni escribe")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not mercado_1816.disponible():
        print("✗ falta MERCADO_1816_API_KEY en el .env — no puedo bajar series")
        return

    hoy = datetime.now(UTC).date()
    # 364 (no 365): la API rechaza rangos que SUPEREN 1 año, y con ambos extremos
    # inclusive 365 días atrás = 366 días de span → HTTP 400. 364 queda justo abajo.
    desde = hoy - timedelta(days=364 if args.backfill else args.dias)

    with get_pool().connection() as conn, conn.cursor() as cur:
        universo = _universo(cur)
        if args.backfill:
            ya = _ya_backfilleados(cur)
            if ya:
                antes = len(universo)
                universo = [t for t in universo if t not in ya]
                print(f"Backfill resumible: {len(ya)} bonos ya tienen historia → "
                      f"bajo {len(universo)} nuevos (de {antes}).")

    if not universo:
        print("✓ nada para bajar (todo el universo ya tiene historia). Listo.")
        return

    dias = (hoy - desde).days + 1
    costo_est = len(universo) * len(_CAMPOS) * dias
    print(f"Universo: {len(universo)} tickers · {len(_CAMPOS)} campos · {dias} días "
          f"→ costo estimado ~{costo_est} créditos "
          f"({'BACKFILL 1 año' if args.backfill else f'diario {args.dias}d'})")

    if args.dry_run:
        print("Tickers:", ", ".join(universo))
        print("Campos:", ", ".join(_CAMPOS))
        print("(DRY-RUN — no se pegó a la API ni se escribió)")
        return

    # Chequeo de créditos ANTES (REGLA #4): no reventar el límite diario.
    try:
        d = (mercado_1816.balance().get("daily") or {})
        print(f"Créditos diarios: {d.get('used')}/{d.get('limit')}")
        if d.get("limit") and (d.get("used", 0) + costo_est) > d["limit"] * 0.9:
            print("✗ el pull excede el 90% del límite diario — abortado. "
                  "Corré más chico o esperá al reset (medianoche).")
            return
    except Exception as e:
        logger.warning("mercado_1816_series: no pude leer el balance (%s) — sigo", e)

    from core.job_runs import JobRunLogger
    with JobRunLogger("mercado_1816_series") as jr:
        n_total = 0
        for lote in _lotes(universo, _BATCH):
            try:
                data = mercado_1816.series(lote, _CAMPOS, desde.isoformat(), hoy.isoformat())
                n = _upsert(mercado_1816.parse_series(data))
            except Exception as e:
                logger.warning("mercado_1816_series: lote %s falló (%s)", lote, e)
                jr.log(f"lote {lote}: ERROR {e}")
                continue
            n_total += n
            jr.log(f"lote {lote}: {n} puntos")
        jr.set_stat("puntos", n_total)
        jr.set_stat("modo", "backfill" if args.backfill else "diario")
        jr.set_stat("tickers", len(universo))
    print(f"✅ {n_total} puntos upserteados en research.mkt_1816_series "
          f"({'backfill 1 año' if args.backfill else f'últimos {args.dias} días'})")


if __name__ == "__main__":
    main()
