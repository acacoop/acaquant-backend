"""jobs/ap5_portfolio.py — la posición de FUTUROS de la cámara, todos los días.

Trae `PosTrade/PositionReport` de la API Postrade (A3 Mercados / ACyRSA) y lo
persiste en `ap5.portfolio`, más el alta de cuentas nuevas en `ap5.cuentas`.

Cron: **9:00 ART** (12:00 UTC), todos los días. Siempre consulta el **último día
hábil** — o sea hoy menos un día hábil.

Por qué el último día hábil y no hoy: el reporte de posición es de CIERRE
(`SettlSessID = EOD`), así que a las 9 de la mañana el día de hoy todavía no
existe. Y por qué "hábil" y no "ayer": un lunes, ayer es domingo. El cálculo usa
`core.calendario.restar_habiles`, que además de findes saltea **feriados** — el
repo ya se comió una vez el bug de contar solo `weekday < 5`.

Idempotente: la PK es (business_date, account, symbol, position_type) y se hace
UPSERT. Re-correrlo el mismo día no duplica ni acumula; corregir una corrida
parcial es volver a correrlo.

Uso:
    python -m jobs.ap5_portfolio                  # último día hábil
    python -m jobs.ap5_portfolio --fecha 20260821
    python -m jobs.ap5_portfolio --fecha 20260821 --dry   # no escribe nada
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

from core import postrade, postrade_posicion
from core.calendario import restar_habiles
from core.job_runs import JobRunLogger
from core.postgres import get_pool

TIPO = "ap5_portfolio"


def ultimo_dia_habil(hoy: date | None = None) -> str:
    """AAAAMMDD del último día hábil = hoy menos UN día hábil.

    Saltea findes **y feriados**. Si hoy es lunes devuelve el viernes; si el
    viernes fue feriado, el jueves.
    """
    return restar_habiles(hoy or date.today(), 1).strftime("%Y%m%d")


def _guardar_posiciones(filas: list[dict]) -> int:
    """UPSERT en `ap5.portfolio`. Devuelve las filas escritas."""
    if not filas:
        return 0
    sql = """
        INSERT INTO ap5.portfolio (
            business_date, account, symbol, position_type, cfi_code,
            unit_of_measure, daily_settlement, settlement_price,
            settlement_currency, long_qty, short_qty, actualizado_at
        ) VALUES (
            %(business_date)s, %(account)s, %(symbol)s, %(position_type)s,
            %(cfi_code)s, %(unit_of_measure)s, %(daily_settlement)s,
            %(settlement_price)s, %(settlement_currency)s,
            %(long_qty)s, %(short_qty)s, now()
        )
        ON CONFLICT (business_date, account, symbol, position_type) DO UPDATE SET
            cfi_code            = EXCLUDED.cfi_code,
            unit_of_measure     = EXCLUDED.unit_of_measure,
            daily_settlement    = EXCLUDED.daily_settlement,
            settlement_price    = EXCLUDED.settlement_price,
            settlement_currency = EXCLUDED.settlement_currency,
            long_qty            = EXCLUDED.long_qty,
            short_qty           = EXCLUDED.short_qty,
            actualizado_at      = now()
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, filas)
    return len(filas)


def _altas_de_cuentas(cuentas: set[str]) -> int:
    """Da de alta las cuentas que aparezcan, SIN pisar el nombre cargado a mano.

    `name` no está en el UPDATE a propósito: es carga manual y el job no opina
    sobre ella. Si estuviera, cada corrida borraría el trabajo del día anterior
    sin que nadie se entere — el modo de falla más caro de todos.
    """
    if not cuentas:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ap5.cuentas (account, visto_at) VALUES (%s, now()) "
            "ON CONFLICT (account) DO UPDATE SET visto_at = now()",
            [(c,) for c in sorted(cuentas)],
        )
    return len(cuentas)


def _colisiones(filas: list[dict]) -> list[str]:
    """Filas distintas que comparten PK. Si aparecen, el grano está mal pensado.

    No se corrige solo: se REPORTA. Un UPSERT con PK repetida deja la última y
    pierde la anterior en silencio, que es exactamente el tipo de pérdida que
    después no se puede reconstruir.
    """
    c = Counter(
        (f["business_date"], f["account"], f["symbol"], f["position_type"]) for f in filas
    )
    return [f"{k[1]}|{k[2]}|{k[3]} ×{v}" for k, v in c.items() if v > 1]


def run(fecha: str | None = None, *, dry: bool = False) -> None:
    with JobRunLogger(TIPO) as run_log:
        f = postrade.fecha_api(fecha) if fecha else ultimo_dia_habil()
        run_log.set_stat("fecha", f)
        run_log.log(f"AP5 · PositionReport por el día hábil {f}")

        filas, stats = postrade_posicion.traer(f)
        for k, v in stats.items():
            run_log.set_stat(k, v)
        run_log.log(
            f"  recibidas={stats['recibidas']} futuros={stats['futuros']} "
            f"descartadas_no_futuro={stats['descartadas_no_futuro']} "
            f"sin_position_qty={stats['sin_position_qty']} → {len(filas)} filas"
        )

        repetidas = _colisiones(filas)
        if repetidas:
            run_log.error(
                f"{len(repetidas)} claves repetidas — el UPSERT deja solo la última: "
                + "; ".join(repetidas[:10])
            )
        run_log.set_stat("claves_repetidas", len(repetidas))

        if not filas:
            # Sin filas no se escribe nada, pero tampoco se declara "todo bien":
            # un día sin posición es posible, y un método que dejó de responder
            # también. Los dos se ven igual desde acá, así que se avisa.
            run_log.error(f"PositionReport no devolvió posiciones de futuros para {f}")
            return

        cuentas = {f_["account"] for f_ in filas if f_["account"]}
        run_log.set_stat("cuentas", len(cuentas))

        largo = sum(f_["long_qty"] for f_ in filas)
        corto = sum(f_["short_qty"] for f_ in filas)
        run_log.set_stat("long_qty_total", largo)
        run_log.set_stat("short_qty_total", corto)
        run_log.log(f"  {len(cuentas)} cuentas | long={largo:,.0f} short={corto:,.0f}")

        if dry:
            run_log.log("  --dry: no se escribe nada")
            for f_ in filas[:10]:
                run_log.log(f"    {f_}")
            return

        escritas = _guardar_posiciones(filas)
        altas = _altas_de_cuentas(cuentas)
        run_log.set_stat("filas_escritas", escritas)
        run_log.set_stat("cuentas_vistas", altas)
        run_log.log(f"  ✓ {escritas} filas en ap5.portfolio · {altas} cuentas en ap5.cuentas")


def main() -> None:
    ap = argparse.ArgumentParser(description="Posición de futuros de la cámara → ap5.")
    ap.add_argument("--fecha", help="AAAAMMDD (default: último día hábil)")
    ap.add_argument("--dry", action="store_true", help="no escribe en la base")
    args = ap.parse_args()
    run(args.fecha, dry=args.dry)


if __name__ == "__main__":
    main()
