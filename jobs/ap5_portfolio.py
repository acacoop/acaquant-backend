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

Idempotente: la PK es (business_date, account, symbol, position_type, side) y se
hace UPSERT. Re-correrlo el mismo día no duplica ni acumula; corregir una corrida
parcial es volver a correrlo.

Uso:
    python -m jobs.ap5_portfolio                  # último día hábil
    python -m jobs.ap5_portfolio --fecha 20260821
    python -m jobs.ap5_portfolio --fecha 20260821 --dry   # no escribe nada
"""
from __future__ import annotations

import argparse
from datetime import date

from core import postrade, postrade_cuentas, postrade_posicion
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
            business_date, account, symbol, position_type, side, cfi_code,
            unit_of_measure, currency, avg_px, daily_settlement,
            settlement_price, settlement_currency, long_qty, short_qty,
            actualizado_at
        ) VALUES (
            %(business_date)s, %(account)s, %(symbol)s, %(position_type)s,
            %(side)s, %(cfi_code)s, %(unit_of_measure)s, %(currency)s,
            %(avg_px)s, %(daily_settlement)s, %(settlement_price)s,
            %(settlement_currency)s, %(long_qty)s, %(short_qty)s, now()
        )
        ON CONFLICT (business_date, account, symbol, position_type, side) DO UPDATE SET
            cfi_code            = EXCLUDED.cfi_code,
            unit_of_measure     = EXCLUDED.unit_of_measure,
            currency            = EXCLUDED.currency,
            avg_px              = EXCLUDED.avg_px,
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


def deduplicar(filas: list[dict]) -> tuple[list[dict], list[str]]:
    """Colapsa filas idénticas y REPORTA las que comparten clave pero difieren.

    Dos cosas muy distintas comparten el síntoma "clave repetida":

    - **Filas idénticas**: guardar una de dos filas iguales no pierde nada. Se
      colapsan en silencio, porque no hay información que se pueda perder.
    - **Filas que DIFIEREN**: ahí el UPSERT elegiría una y perdería la otra sin
      fallar. Eso no se puede resolver solo, así que se devuelve QUÉ CAMPO
      divergió — con el nombre del campo, no un conteo: un conteo dice que hay
      un problema, el campo dice cuál es.

    Devuelve `(filas_a_escribir, divergencias)`.
    """
    porclave: dict[tuple, list[dict]] = {}
    for f in filas:
        k = (f["business_date"], f["account"], f["symbol"], f["position_type"], f["side"])
        porclave.setdefault(k, []).append(f)

    salida: list[dict] = []
    divergencias: list[str] = []
    for k, grupo in porclave.items():
        salida.append(grupo[0])
        if len(grupo) == 1:
            continue
        distintos = sorted(
            campo for campo in grupo[0]
            if len({str(g.get(campo)) for g in grupo}) > 1
        )
        if distintos:
            detalle = "; ".join(
                f"{c}=" + "/".join(sorted({str(g.get(c)) for g in grupo})[:3])
                for c in distintos[:4]
            )
            divergencias.append(
                f"{k[1]}|{k[2]}|{k[3]}|{k[4]} ×{len(grupo)} difieren en {detalle}"
            )
    return salida, divergencias


def _guardar_multiplicadores(mults: dict[str, dict]) -> int:
    """UPSERT en `ap5.contratos`, SIN pisar lo cargado a mano.

    El `WHERE fuente <> 'manual'` es el mismo invariante de `ap5.cuentas.name`:
    lo automático completa, lo humano manda. Un símbolo cuyo multiplicador no se
    pudo resolver se carga a mano una vez, y el job no vuelve a opinar.

    `fuente` distingue `derivado` (despejado de la identidad del settlement) de
    `hermano` (heredado de otro vencimiento del mismo contrato). Son distintos
    grados de evidencia y por eso no se guardan iguales: el heredado es correcto
    hasta que un contrato cambie de tamaño, y ahí lo único que lo delata es
    saber que nunca se midió.
    """
    if not mults:
        return 0
    sql = """
        INSERT INTO ap5.contratos (
            symbol, multiplicador, unit_of_measure, fuente, filas_base,
            dispersion, actualizado_at
        ) VALUES (
            %(symbol)s, %(multiplicador)s, %(unit_of_measure)s, %(fuente)s,
            %(filas_base)s, %(dispersion)s, now()
        )
        ON CONFLICT (symbol) DO UPDATE SET
            multiplicador   = EXCLUDED.multiplicador,
            unit_of_measure = EXCLUDED.unit_of_measure,
            fuente          = EXCLUDED.fuente,
            filas_base      = EXCLUDED.filas_base,
            dispersion      = EXCLUDED.dispersion,
            actualizado_at  = now()
        WHERE ap5.contratos.fuente <> 'manual'
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, list(mults.values()))
    return len(mults)


def _cuentas_sin_nombre() -> list[str]:
    """Las que todavía no tienen denominación de la cámara.

    Se piden SOLO esas: `AccountDetails` es de a una cuenta por llamada y no hay
    listado (probado: `AccountList` y `PartyDetails` dan 404). Refrescar las 98
    todos los días serían 98 llamadas para un dato que no cambia; pedir solo las
    nuevas son cero llamadas en un día normal.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT account FROM ap5.cuentas WHERE denominacion IS NULL "
                    "ORDER BY account")
        return [str(r[0]) for r in cur.fetchall()]


def _guardar_denominaciones(detalles: list[dict]) -> int:
    """El nombre que da la CÁMARA. Nunca toca `name` (que es el humano)."""
    if not detalles:
        return 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(
            "UPDATE ap5.cuentas SET denominacion = %(denominacion)s, "
            "cuit = %(cuit)s, netting = %(netting)s, denominacion_at = now() "
            "WHERE account = %(account)s",
            detalles,
        )
    return len(detalles)


def run(fecha: str | None = None, *, dry: bool = False, refrescar_nombres: bool = False) -> None:
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

        if not filas:
            # Sin filas no se escribe nada, pero tampoco se declara "todo bien":
            # un día sin posición es posible, y un método que dejó de responder
            # también. Los dos se ven igual desde acá, así que se avisa.
            run_log.error(f"PositionReport no devolvió posiciones de futuros para {f}")
            return

        # Las filas idénticas se colapsan (no se pierde nada); las que comparten
        # clave pero DIFIEREN se cantan con el campo que divergió, porque ahí sí
        # el UPSERT elegiría una y perdería la otra sin fallar.
        a_escribir, divergencias = deduplicar(filas)
        run_log.set_stat("filas_crudas", len(filas))
        run_log.set_stat("filas_unicas", len(a_escribir))
        run_log.set_stat("claves_divergentes", len(divergencias))
        if len(a_escribir) != len(filas):
            run_log.log(f"  {len(filas)} filas → {len(a_escribir)} claves únicas")
        if divergencias:
            run_log.error(
                f"{len(divergencias)} claves con filas DISTINTAS (se guarda una y se "
                f"pierde el resto): " + " | ".join(divergencias[:8])
            )

        cuentas = {f_["account"] for f_ in a_escribir if f_["account"]}
        run_log.set_stat("cuentas", len(cuentas))

        largo = sum(f_["long_qty"] for f_ in a_escribir)
        corto = sum(f_["short_qty"] for f_ in a_escribir)
        run_log.set_stat("long_qty_total", largo)
        run_log.set_stat("short_qty_total", corto)
        run_log.log(f"  {len(cuentas)} cuentas | long={largo:,.0f} short={corto:,.0f}")

        # El tamaño del contrato, despejado de la identidad del settlement. Sin
        # esto la posición se muestra en contratos y el reporte pide toneladas.
        mults, sin_mult = postrade_posicion.multiplicadores(a_escribir)
        heredados = sum(1 for m in mults.values() if m["fuente"] == "hermano")
        run_log.set_stat("multiplicadores", len(mults))
        run_log.set_stat("multiplicadores_heredados", heredados)
        run_log.set_stat("simbolos_sin_multiplicador", len(sin_mult))
        if heredados:
            run_log.log(f"  {heredados} multiplicadores heredados de un hermano del "
                        f"mismo contrato (vencimiento sin settlement propio)")
        if sin_mult:
            # No es un error: un símbolo con settlement 0 y sin hermanos no tiene
            # de dónde salir. Pero SÍ hay que verlo, porque hasta que alguien lo
            # cargue esa posición no se puede expresar en su unidad.
            run_log.log(f"  ⚠ {len(sin_mult)} símbolos sin multiplicador: "
                        + ", ".join(sin_mult[:8]))

        if dry:
            run_log.log("  --dry: no se escribe nada")
            for f_ in a_escribir[:10]:
                run_log.log(f"    {f_}")
            for m in list(mults.values())[:10]:
                run_log.log(f"    mult {m['symbol']} = {m['multiplicador']:g} "
                            f"({m['unit_of_measure']}, {m['filas_base']} filas)")
            return

        escritas = _guardar_posiciones(a_escribir)
        altas = _altas_de_cuentas(cuentas)
        contratos = _guardar_multiplicadores(mults)
        run_log.set_stat("filas_escritas", escritas)
        run_log.set_stat("cuentas_vistas", altas)
        run_log.set_stat("contratos_escritos", contratos)
        run_log.log(f"  ✓ {escritas} filas en ap5.portfolio · {altas} cuentas en "
                    f"ap5.cuentas · {contratos} en ap5.contratos")

        # El nombre de la cuenta lo publica la cámara (`AccountDetails`), así que
        # no se tipea. Se piden solo las que no lo tienen: en un día normal son
        # cero llamadas, y el día que aparece una cuenta nueva viene con nombre.
        pendientes = _cuentas_sin_nombre() if not refrescar_nombres else sorted(cuentas)
        if pendientes:
            detalles, fallidas = postrade_cuentas.traer(pendientes)
            nombradas = _guardar_denominaciones(detalles)
            run_log.set_stat("nombres_resueltos", nombradas)
            run_log.set_stat("nombres_sin_resolver", len(fallidas))
            run_log.log(f"  ✓ {nombradas} nombres desde AccountDetails"
                        + (f" · {len(fallidas)} sin resolver" if fallidas else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description="Posición de futuros de la cámara → ap5.")
    ap.add_argument("--fecha", help="AAAAMMDD (default: último día hábil)")
    ap.add_argument("--dry", action="store_true", help="no escribe en la base")
    ap.add_argument("--refrescar-nombres", action="store_true",
                    help="vuelve a pedir el nombre de TODAS las cuentas del día "
                         "(por default solo el de las que no lo tienen)")
    args = ap.parse_args()
    run(args.fecha, dry=args.dry, refrescar_nombres=args.refrescar_nombres)


if __name__ == "__main__":
    main()
