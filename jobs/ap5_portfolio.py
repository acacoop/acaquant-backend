"""jobs/ap5_portfolio.py — la posición de FUTUROS de la cámara, todos los días.

Trae `PosTrade/PositionReport` de la API Postrade (A3 Mercados / ACyRSA) y lo
persiste en `ap5.portfolio`, más el alta de cuentas nuevas en `ap5.cuentas`.

Y en la MISMA corrida, el **requerimiento de márgenes**
(`PosTrade/MarginRequirementReport`) abierto por comitente, a `ap5.margenes`.
Va acá y no en un job aparte porque es la misma fecha y la misma sesión: dos
relojes para el mismo reporte es cómo una mitad queda de un día y la otra de
otro sin que nada falle.

Cron: **10:00 ART** (13:00 UTC), todos los días. Siempre consulta el **último
día hábil** — o sea hoy menos un día hábil.

Por qué el último día hábil y no hoy: el reporte de posición es de CIERRE
(`SettlSessID = EOD`), así que a la mañana el día de hoy todavía no existe. Y por qué "hábil" y no "ayer": un lunes, ayer es domingo. El cálculo usa
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

import config
from core import (
    postrade,
    postrade_cuentas,
    postrade_margenes,
    postrade_posicion,
)
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


def _guardar_margenes(fecha: str, filas: list[dict]) -> int:
    """UPSERT en `ap5.margenes`. Devuelve las filas escritas.

    La PK es (fecha, cuenta, cuenta_compensacion, moneda) — el PAR, no la cuenta
    sola: ver el comentario de la tabla en `sql/schema.sql`.
    """
    if not filas:
        return 0
    sql = """
        INSERT INTO ap5.margenes (
            fecha, cuenta, cuenta_compensacion, concepto, moneda,
            margen, primas, inter_temporal, referencias, titular, actualizado_at
        ) VALUES (
            %(fecha)s, %(cuenta)s, %(cuenta_compensacion)s, %(concepto)s,
            %(moneda)s, %(margen)s, %(primas)s, %(inter_temporal)s,
            %(referencias)s, %(titular)s, now()
        )
        ON CONFLICT (fecha, cuenta, cuenta_compensacion, concepto, moneda)
        DO UPDATE SET
            margen         = EXCLUDED.margen,
            primas         = EXCLUDED.primas,
            inter_temporal = EXCLUDED.inter_temporal,
            referencias    = EXCLUDED.referencias,
            titular        = EXCLUDED.titular,
            actualizado_at = now()
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.executemany(sql, [{**f, "fecha": fecha} for f in filas])
    return len(filas)


def _margenes(f: str, run_log, *, dry: bool = False) -> None:
    """El requerimiento de márgenes del día, abierto por comitente.

    ⚠️ **Vacío NO es un error** (medido 2026-08-25): `MarginRequirementReport`
    contesta 200 con lista vacía cuando todavía no hay dato para esa fecha; un
    método deshabilitado tira excepción. Los dos se ven igual desde la card —un
    número que no está— así que se distinguen acá y se dicen distinto: un vacío
    se avisa y no se declara «todo bien», que es lo que haría un `return` mudo.
    """
    try:
        crudo = postrade.leer(postrade_margenes.METODO_MARGENES, {"date": f})
    except Exception as e:
        run_log.error(f"MarginRequirementReport falló para {f}: {e}")
        return

    if not crudo:
        run_log.set_stat("margenes_filas", 0)
        run_log.error(
            f"MarginRequirementReport contestó VACÍO para {f} (no dio error: el "
            f"método está habilitado, lo que no hay es dato)")
        return

    planas, stats = postrade_margenes.aplanar_margenes(crudo)
    for k, v in stats.items():
        run_log.set_stat(f"margenes_{k}", v)

    filas = postrade_margenes.por_cuenta_de_neteo(planas)
    conceptos = sorted({x["concepto"] for x in filas})
    run_log.set_stat("margenes_filas", len(filas))
    run_log.set_stat("margenes_cuentas", len({x["cuenta"] for x in filas}))
    run_log.set_stat("margenes_conceptos", len(conceptos))
    run_log.log(f"  márgenes: {len(planas)} referencias → {len(filas)} "
                f"(cuenta, compensación, concepto, moneda)")
    run_log.log(f"  conceptos: {', '.join(conceptos) or '(ninguno)'}")

    # ⚠️ Los conceptos que las cards suman tienen que EXISTIR. Si la cámara
    # renombra `Inicial A3`, la card seguiría dibujando un número —el de los
    # conceptos que sí quedaron— y nadie se enteraría. Por eso se comparan acá,
    # contra la respuesta real, y no en la pantalla.
    declarados = set(config.AP5_CONCEPTOS_REQUERIMIENTO) | set(
        config.AP5_CONCEPTOS_ACTIVO_INTEGRADO)
    faltan = sorted(declarados - set(conceptos))
    run_log.set_stat("margenes_conceptos_faltantes", len(faltan))
    if faltan:
        run_log.error(
            f"conceptos declarados en config que NO vinieron: {faltan} — las "
            f"cards que los suman van a mostrar de menos. Vinieron: {conceptos}")

    if dry:
        for x in filas[:10]:
            run_log.log(f"    {x}")
        return

    escritas = _guardar_margenes(f, filas)
    run_log.set_stat("margenes_escritas", escritas)
    run_log.log(f"  ✓ {escritas} filas en ap5.margenes")


def _rotar_acumulados(f: str, run_log) -> None:
    """Absorber el día `f` en `ap5.acumulado`: T-1 + DIARIA = ACUMULADO.

    ⚠️ **Va DESPUÉS de escribir la posición**, porque la diaria sale de ahí.
    ⚠️ **La decisión de rotar o no la toma `ap5_rotacion.rotar`**, que es pura y
    tiene sus tests: si el job corre dos veces el mismo día no duplica nada.

    Cada cambio queda en `ap5.acumulado_log` con el antes y el después — sin
    libro, un valor que se pisa a sí mismo todos los días deja de tener pasado.
    """
    from datetime import date as _date

    from api.services.ap5_rotacion import Estado, rotar

    dia = _date(int(f[:4]), int(f[4:6]), int(f[6:8]))

    with get_pool().connection() as conn, conn.cursor() as cur:
        # La diaria de CADA cuenta, por moneda, del día que se está absorbiendo.
        cur.execute(
            "SELECT account, settlement_currency AS moneda, "
            "       sum(daily_settlement) AS diaria "
            "FROM ap5.portfolio WHERE business_date = %(f)s "
            "GROUP BY 1, 2", {"f": dia})
        diarias: dict[str, dict[str, float]] = {}
        for account, moneda, diaria in cur.fetchall():
            d = diarias.setdefault(str(account), {"pesos": 0.0, "mtr": 0.0})
            if moneda == "Pesos":
                d["pesos"] += float(diaria or 0)
            elif moneda == "Dólar MtR":
                d["mtr"] += float(diaria or 0)

        cur.execute(
            "SELECT account, fecha, acumulado_t1_pesos, acumulado_t1_mtr, "
            "       diaria_pesos, diaria_mtr, acumulado_pesos, acumulado_mtr "
            "FROM ap5.acumulado")
        previos = {str(r[0]): r for r in cur.fetchall()}

        conteo = {"rota": 0, "recalcula": 0, "ignora": 0, "nuevas": 0}
        for account, d in sorted(diarias.items()):
            r = previos.get(account)
            if r is None:
                conteo["nuevas"] += 1
            previo = Estado(
                fecha=r[1] if r else None,
                t1_pesos=float(r[2] or 0) if r else 0.0,
                t1_mtr=float(r[3] or 0) if r else 0.0,
                diaria_pesos=float(r[4] or 0) if r else 0.0,
                diaria_mtr=float(r[5] or 0) if r else 0.0,
            )
            nuevo, motivo = rotar(previo, dia, d["pesos"], d["mtr"])
            conteo[motivo] += 1
            if motivo == "ignora":
                continue

            cur.execute(
                "INSERT INTO ap5.acumulado (account, acumulado_t1_pesos, "
                "  acumulado_t1_mtr, diaria_pesos, diaria_mtr, acumulado_pesos, "
                "  acumulado_mtr, fecha, rotado_at) "
                "VALUES (%(a)s,%(t1p)s,%(t1m)s,%(dp)s,%(dm)s,%(ap)s,%(am)s,%(f)s,now()) "
                "ON CONFLICT (account) DO UPDATE SET "
                "  acumulado_t1_pesos = EXCLUDED.acumulado_t1_pesos, "
                "  acumulado_t1_mtr   = EXCLUDED.acumulado_t1_mtr, "
                "  diaria_pesos       = EXCLUDED.diaria_pesos, "
                "  diaria_mtr         = EXCLUDED.diaria_mtr, "
                "  acumulado_pesos    = EXCLUDED.acumulado_pesos, "
                "  acumulado_mtr      = EXCLUDED.acumulado_mtr, "
                "  fecha              = EXCLUDED.fecha, "
                "  rotado_at          = now()",
                {"a": account, "t1p": nuevo.t1_pesos, "t1m": nuevo.t1_mtr,
                 "dp": nuevo.diaria_pesos, "dm": nuevo.diaria_mtr,
                 "ap": nuevo.acum_pesos, "am": nuevo.acum_mtr, "f": dia})

            cur.execute(
                "INSERT INTO ap5.acumulado_log (account, motivo, fecha, "
                "  t1_pesos_antes, t1_pesos_despues, t1_mtr_antes, t1_mtr_despues, "
                "  diaria_pesos, diaria_mtr, acum_pesos_antes, acum_pesos_despues, "
                "  acum_mtr_antes, acum_mtr_despues, por) "
                "VALUES (%(a)s,%(mo)s,%(f)s,%(t1pa)s,%(t1pd)s,%(t1ma)s,%(t1md)s,"
                "        %(dp)s,%(dm)s,%(apa)s,%(apd)s,%(ama)s,%(amd)s,'job')",
                {"a": account, "mo": motivo, "f": dia,
                 "t1pa": previo.t1_pesos, "t1pd": nuevo.t1_pesos,
                 "t1ma": previo.t1_mtr, "t1md": nuevo.t1_mtr,
                 "dp": nuevo.diaria_pesos, "dm": nuevo.diaria_mtr,
                 "apa": previo.acum_pesos, "apd": nuevo.acum_pesos,
                 "ama": previo.acum_mtr, "amd": nuevo.acum_mtr})

    for k, v in conteo.items():
        run_log.set_stat(f"acum_{k}", v)
    run_log.log(f"  acumulado: {conteo['rota']} rotadas · "
                f"{conteo['recalcula']} recalculadas (mismo día, no duplica) · "
                f"{conteo['ignora']} ignoradas (día viejo) · "
                f"{conteo['nuevas']} cuentas nuevas")


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
            _margenes(f, run_log, dry=True)
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

        # El acumulado ABSORBE el día: T-1 + DIARIA = ACUMULADO. Va acá y no
        # antes porque la diaria sale de las filas que se acaban de escribir.
        _rotar_acumulados(f, run_log)

        # El requerimiento de márgenes, del MISMO día hábil. Va acá y no en un
        # job aparte porque es la misma fecha y la misma sesión de Postrade: dos
        # relojes distintos para el mismo reporte es exactamente cómo una mitad
        # queda de un día y la otra de otro sin que nada falle.
        _margenes(f, run_log, dry=dry)


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
