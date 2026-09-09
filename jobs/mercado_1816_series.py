"""jobs/mercado_1816_series.py — baja las series históricas de 1816 a Postgres.

Doc madre: docs/RESEARCH.md. Alimenta `research.mkt_1816_series` (el
laboratorio de series/spreads de la vista RESEARCH). Feed SEPARADO de mercado.curvas
(nuestro motor RF de hoy) — no se pisan.

⚠️ **A QUÉ DÓLAR se baja cada bono.** No es un default global: cada ticker se
pide en la moneda que le corresponde según en qué PAGA (`core.mercado_1816.
moneda_series` sobre el `moneda_pago` del catálogo). Los que pagan en dólares van
en `mep`; el resto en `ars`. Hasta el 2026-09-09 se pedía todo con el default de
la API (`ars`), que para un bono en dólares calcula los indicadores al **CCL de
1816** — medido, 481 bps de TEA en BPOB7. Detalle en `docs/RESEARCH.md` §A.4.7b.

Dos modos:
  - `--backfill`: baja 1 AÑO (una vez, la carga inicial). Scopeado + batcheado
    (≤10 tickers/call, series topea ahí) + throttle del cliente + chequeo de
    créditos ANTES (REGLA #4). Correr FUERA de rueda.
  - default (diario, cron post-cierre): baja los últimos días y upsertea
    (idempotente por PK → re-correr no duplica ni re-paga de más).

Para VERIFICAR a qué dólar quedó cada serie (y que el rebajado en `mep` haya
entrado): correr `python -m scripts.diag_1816_moneda_series`.

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


def _universo(cur) -> list[tuple[str, str]]:
    """[(ticker, moneda)] del watch activo.

    **La moneda no se elige acá: se DERIVA** del `moneda_pago` del catálogo con
    `core.mercado_1816.moneda_series`, la misma función que usa la lectura. Un
    bono que paga en dólares se pide en `mep`; el resto —incluidos los
    dólar-linked, que están denominados en USD pero pagan en pesos— en `ars`.
    Un ticker sin ficha en el catálogo cae en `ars` (el default de la API).
    """
    cur.execute("SELECT w.ticker, i.moneda_pago "
                "FROM research.mkt_1816_watch w "
                "LEFT JOIN research.mkt_1816_instrumentos i ON i.ticker = w.ticker "
                "WHERE w.activo ORDER BY w.orden NULLS LAST, w.ticker")
    filas = [(tk, mercado_1816.moneda_series(mp)) for tk, mp in cur.fetchall()]
    # El seed son TODOS hard dollar (soberanos USD + BOPREALes) → `mep` sin
    # consultar el catálogo, que en ese escenario tampoco está poblado.
    return filas or [(tk, "mep") for tk in _SEED]


def _ya_backfilleados(cur, desde) -> set[tuple[str, str]]:
    """Pares (ticker, moneda) cuya historia YA llega hasta `desde`
    (min(fecha) <= desde) → en backfill no se re-bajan. Resumible por RANGO
    (independiente del largo de la ventana): no re-paga, y si el pull se corta
    por el límite diario, la próxima corrida sigue con los que faltan.

    ⚠️ **La moneda es parte de la clave, y no es un detalle de prolijidad.**
    Agrupando solo por ticker, los 21 hard dollar que ya tienen su historia en
    `ars` (la vieja, al CCL de 1816) se saltearían para siempre y la de `mep`
    no se bajaría nunca — el backfill diría «nada para bajar» y el gráfico
    seguiría al dólar equivocado, sin un solo error.
    """
    cur.execute(
        "SELECT ticker, moneda FROM research.mkt_1816_series "
        "GROUP BY ticker, moneda HAVING min(fecha) <= %s",
        (desde,),
    )
    return {(r[0], r[1]) for r in cur.fetchall()}


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
                    help="carga inicial (default: desde el 1-ene del año en curso). Diario si no.")
    ap.add_argument("--desde", help="backfill desde esta fecha YYYY-MM-DD "
                    "(default 1-ene del año; máx 1 año atrás por límite de la API)")
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
    if args.desde:
        desde = datetime.strptime(args.desde, "%Y-%m-%d").date()
    elif args.backfill:
        desde = hoy.replace(month=1, day=1)          # desde el 1-ene del año en curso
    else:
        desde = hoy - timedelta(days=args.dias)
    # La API rechaza rangos > 1 año → cap defensivo (364 días queda justo abajo).
    tope = hoy - timedelta(days=364)
    if desde < tope:
        desde = tope

    with get_pool().connection() as conn, conn.cursor() as cur:
        universo = _universo(cur)
        if args.backfill:
            # Resumible por RANGO: salta los que ya tienen historia hasta `desde`
            # (min(fecha) <= desde) → no re-paga, y si se corta por el límite diario
            # se continúa al día siguiente con los que faltan.
            ya = _ya_backfilleados(cur, desde)
            if ya:
                antes = len(universo)
                universo = [par for par in universo if par not in ya]
                print(f"Backfill resumible: {len(ya)} pares (ticker, moneda) ya cubren "
                      f"desde {desde} → bajo {len(universo)} nuevos (de {antes}).")

    if not universo:
        print("✓ nada para bajar (todo el universo ya tiene historia). Listo.")
        return

    # Una llamada a /series lleva UNA moneda → el universo se parte en tandas.
    # El costo total no cambia (sigue siendo tickers × campos × días): cambia
    # cuántas llamadas hacen falta, y el throttle de 2,5 s del cliente las espacia.
    por_moneda: dict[str, list[str]] = {}
    for tk, mon in universo:
        por_moneda.setdefault(mon, []).append(tk)

    dias = (hoy - desde).days + 1
    costo_est = len(universo) * len(_CAMPOS) * dias
    reparto = " · ".join(f"{mon}: {len(tks)}" for mon, tks in sorted(por_moneda.items()))
    print(f"Universo: {len(universo)} tickers ({reparto}) · {len(_CAMPOS)} campos · "
          f"{dias} días ({desde} → {hoy}) → costo estimado ~{costo_est} créditos "
          f"({'BACKFILL' if args.backfill else f'diario {args.dias}d'})")

    if args.dry_run:
        for mon, tks in sorted(por_moneda.items()):
            print(f"Tickers [{mon}]:", ", ".join(tks))
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
        for mon, tks in sorted(por_moneda.items()):
            for lote in _lotes(tks, _BATCH):
                try:
                    data = mercado_1816.series(lote, _CAMPOS, desde.isoformat(),
                                               hoy.isoformat(), moneda=mon)
                    # La `moneda` que se GRABA sale de la respuesta (parse_series la
                    # copia de ahí) y es la clave por la que la lectura va a buscar
                    # estas filas. Si la API contestara en otra moneda que la
                    # pedida, guardarlas igual sería mentir en la etiqueta: quedaría
                    # una serie al CCL rotulada `mep` y el gráfico no se quejaría.
                    # Se corta el lote y se dice cuál vino.
                    devuelta = (data or {}).get("moneda")
                    if devuelta != mon:
                        raise RuntimeError(
                            f"pedí moneda={mon!r} y la API contestó {devuelta!r}")
                    n = _upsert(mercado_1816.parse_series(data))
                except Exception as e:
                    logger.warning("mercado_1816_series: lote %s [%s] falló (%s)",
                                   lote, mon, e)
                    jr.log(f"lote {lote} [{mon}]: ERROR {e}")
                    continue
                n_total += n
                jr.log(f"lote {lote} [{mon}]: {n} puntos")
            jr.set_stat(f"tickers_{mon}", len(tks))
        jr.set_stat("puntos", n_total)
        jr.set_stat("modo", "backfill" if args.backfill else "diario")
        jr.set_stat("tickers", len(universo))
    print(f"✅ {n_total} puntos upserteados en research.mkt_1816_series "
          f"({'backfill desde ' + str(desde) if args.backfill else f'últimos {args.dias} días'})")


if __name__ == "__main__":
    main()
