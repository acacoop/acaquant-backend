"""diag_cupo_vs_flujos.py — ¿cuánto se desvió el cupo transaccional desde que se cargó?

READ-ONLY. No escribe una sola fila.

EL PROBLEMA (detectado por el user, 2026-08-09)
-----------------------------------------------
`clientes.comitentes.cupo_usado_ars` es una FOTO: se carga a mano desde
Manager → Clientes (carga masiva del cupo de fondeo del custodio) y nada la
vuelve a tocar — `cupo_cargado_en` guarda cuándo fue. Desde esa fecha los
clientes siguieron depositando y extrayendo plata, y esos flujos YA se calculan
en la vista NEGOCIO (`api/services/valuaciones.py`, categorías depósito /
transferencia / extracción sobre `operaciones.negocio_movimientos`, por
id_cuenta y pesificados con el MEP del día). Nadie cruza las dos cosas.

O sea: el cupo usado que muestra el tablero comercial es de hace meses.

QUÉ CONTESTA ESTE DIAG (hay que saberlo ANTES de codear el fix — REGLA #2)
-------------------------------------------------------------------------
1. ¿Cuándo se cargó el cupo y a cuántas cuentas? (¿es una sola fecha o varias?)
2. ¿Cuánto flujo hubo DESDE esa fecha? ¿A cuántas cuentas les movería el número?
3. **La pregunta que define la fórmula**: ¿el cupo usado se comporta como
   "fondeo acumulado" (depósitos suman, extracciones restan)? Si al aplicar los
   flujos aparecen muchos DISPONIBLES NEGATIVOS o muchos usados que ya hoy
   superan el transaccional, la semántica es otra (ventana móvil de 12 meses,
   reseteo anual, o el cupo mide otra cosa) y la fórmula cambia entera.
4. ¿`negocio_movimientos` cubre el período completo desde la carga? Si el
   histórico arranca después, faltan flujos y el cálculo saldría corto.

Uso (en el Droplet):

    python -m scripts.diag_cupo_vs_flujos                      # ancla 2026-06-01
    python -m scripts.diag_cupo_vs_flujos --desde 2026-06-01   # ancla explícita
    python -m scripts.diag_cupo_vs_flujos --top 40  # más filas en los rankings
"""
from __future__ import annotations

import sys

# Se importan de PRODUCCIÓN a propósito: si mañana alguien suma una categoría de
# flujo externo, este diag la toma sola y no puede contradecir a la vista.
from api.services.valuaciones import (
    _FLUJO_EXTERNO_DEPOSITO,
    _FLUJO_EXTERNO_EXTRACCION,
    _FLUJOS_EXTERNOS_ALL,
)
from core.postgres import get_pool

# Pesificación: misma regla que el motor de PnL (docs/MOTOR_VALUACIONES.md) —
# el importe en USD se multiplica por el MEP snapshot de la fila. Si falta el
# mep, la fila se cuenta aparte en vez de inventar una cotización.
_IMPORTE_ARS = ("CASE WHEN COALESCE(m.moneda,'ARS') = 'ARS' THEN m.importe "
                "     ELSE m.importe * m.mep END")


def _fmt(v, dec: int = 0) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.{dec}f}".replace(",", "@").replace(".", ",").replace("@", ".")


def main() -> None:
    top = 20
    if "--top" in sys.argv:
        try:
            top = int(sys.argv[sys.argv.index("--top") + 1])
        except (IndexError, ValueError):
            pass
    # `cupo_cargado_en` está NULL en las 1.567 cuentas (medido 2026-08-09): la
    # carga masiva nunca lo escribió. Sin esa fecha no hay desde cuándo acumular
    # los flujos, así que se pasa a mano. Default = 2026-06-01, que es cuando el
    # user dice haber cargado el cupo.
    desde_ancla = "2026-06-01"
    if "--desde" in sys.argv:
        try:
            desde_ancla = sys.argv[sys.argv.index("--desde") + 1]
        except IndexError:
            pass

    with get_pool().connection() as conn, conn.cursor() as cur:
        print("=" * 78)
        print("1) COBERTURA DEL CUPO — cuántas cuentas lo tienen y desde cuándo")
        print("=" * 78)
        cur.execute("""
            SELECT count(*) FILTER (WHERE cupo_transaccional_ars IS NOT NULL) AS con_transaccional,
                   count(*) FILTER (WHERE cupo_usado_ars IS NOT NULL)         AS con_usado,
                   count(*)                                                   AS total
            FROM clientes.comitentes""")
        con_t, con_u, total = cur.fetchone()
        print(f"comitentes totales           : {_fmt(total)}")
        print(f"con cupo transaccional cargado: {_fmt(con_t)}")
        print(f"con cupo usado cargado        : {_fmt(con_u)}")

        cur.execute("""
            SELECT cupo_cargado_en::date AS dia, cupo_fuente, count(*)
            FROM clientes.comitentes
            WHERE cupo_transaccional_ars IS NOT NULL
            GROUP BY 1, 2 ORDER BY 1 DESC NULLS LAST""")
        print("\nfechas de carga (si hay UNA sola, el cupo nunca se refrescó):")
        for dia, fuente, n in cur.fetchall():
            print(f"  {dia!s:12s}  fuente={fuente or '—'!s:12s}  {_fmt(n):>6s} cuentas")

        print("\n" + "=" * 78)
        print("2) CONTROL DE SEMÁNTICA — ¿el usado ya supera al transaccional HOY?")
        print("=" * 78)
        cur.execute("""
            SELECT count(*) FILTER (WHERE cupo_usado_ars > cupo_transaccional_ars),
                   count(*) FILTER (WHERE cupo_usado_ars <= cupo_transaccional_ars),
                   count(*) FILTER (WHERE cupo_usado_ars < 0)
            FROM clientes.comitentes
            WHERE cupo_transaccional_ars IS NOT NULL AND cupo_usado_ars IS NOT NULL""")
        exceden, ok, negativos = cur.fetchone()
        print(f"usado > transaccional : {_fmt(exceden)}   <- si es alto, 'usado' NO es "
              f"un consumo acotado por el transaccional")
        print(f"usado <= transaccional: {_fmt(ok)}")
        print(f"usado negativo        : {_fmt(negativos)}")

        print("\n" + "=" * 78)
        print("3) COBERTURA TEMPORAL de negocio_movimientos (flujos externos)")
        print("=" * 78)
        cur.execute("""
            SELECT min(fecha), max(fecha), count(*)
            FROM operaciones.negocio_movimientos
            WHERE categoria = ANY(%s)""", (list(_FLUJOS_EXTERNOS_ALL),))
        desde, hasta, n_mov = cur.fetchone()
        print(f"categorías consideradas: {sorted(_FLUJOS_EXTERNOS_ALL)}")
        print(f"  (depósito: {sorted(_FLUJO_EXTERNO_DEPOSITO)} · "
              f"extracción: {sorted(_FLUJO_EXTERNO_EXTRACCION)})")
        print(f"movimientos de flujo externo: {_fmt(n_mov)}  desde {desde}  hasta {hasta}")
        cur.execute("""
            SELECT count(*) FROM operaciones.negocio_movimientos
            WHERE categoria = ANY(%s) AND COALESCE(moneda,'ARS') <> 'ARS' AND mep IS NULL""",
                    (list(_FLUJOS_EXTERNOS_ALL),))
        (sin_mep,) = cur.fetchone()
        if sin_mep:
            print(f"⚠️ {_fmt(sin_mep)} movimientos en USD SIN mep snapshot → no se pueden "
                  f"pesificar con la regla de producción (quedan fuera de los totales).")

        print("\n" + "=" * 78)
        print(f"4) EL IMPACTO — flujo neto por cuenta DESDE {desde_ancla}")
        print("=" * 78)
        print("ancla: se usa `cupo_cargado_en` cuando está cargado y, si está NULL "
              f"(el caso hoy), la fecha {desde_ancla} pasada por --desde.")
        # `denominacion` NO vive en comitentes: es de clientes.cuentas (mismo join
        # que hace la vista comercial, comercial_sql.py::_col).
        cur.execute(f"""
            WITH cupo AS (
                SELECT c.id_cuenta, c.cupo_transaccional_ars AS trans,
                       c.cupo_usado_ars AS usado,
                       COALESCE(c.cupo_cargado_en::date, %s::date) AS desde,
                       u.denominacion
                FROM clientes.comitentes c
                LEFT JOIN clientes.cuentas u ON u.id_cuenta = c.id_cuenta
                WHERE c.cupo_transaccional_ars IS NOT NULL
            ),
            flujo AS (
                SELECT c.id_cuenta,
                       sum(CASE WHEN m.categoria = ANY(%s) THEN {_IMPORTE_ARS} ELSE 0 END) AS dep,
                       sum(CASE WHEN m.categoria = ANY(%s) THEN {_IMPORTE_ARS} ELSE 0 END) AS ext,
                       count(*) AS n
                FROM cupo c
                JOIN operaciones.negocio_movimientos m
                  ON m.id_cuenta = c.id_cuenta
                 AND m.fecha >= c.desde
                 AND m.categoria = ANY(%s)
                GROUP BY 1
            )
            SELECT c.id_cuenta, c.denominacion, c.trans, c.usado,
                   COALESCE(f.dep,0), COALESCE(f.ext,0), COALESCE(f.n,0)
            FROM cupo c LEFT JOIN flujo f ON f.id_cuenta = c.id_cuenta
        """, (desde_ancla, list(_FLUJO_EXTERNO_DEPOSITO), list(_FLUJO_EXTERNO_EXTRACCION),
              list(_FLUJOS_EXTERNOS_ALL)))
        filas = cur.fetchall()

    # El signo del importe ya viene "con signo cliente" (+ depósito, − extracción)
    # según valuaciones.py, así que el neto es dep + ext (ext ya es negativo).
    con_flujo = [f for f in filas if f[6]]
    print(f"cuentas con cupo cargado          : {_fmt(len(filas))}")
    print(f"cuentas CON flujo desde esa fecha : {_fmt(len(con_flujo))}"
          f"   <- a estas les cambia el número HOY")
    if not con_flujo:
        print("\n(no hay flujos posteriores a la carga: el cupo no se desvió)")
        return

    enriquecidas = []
    for idc, den, trans, usado, dep, ext, n in con_flujo:
        neto = float(dep) + float(ext)
        usado_hoy = float(usado or 0) + neto
        disp_antes = float(trans) - float(usado or 0)
        disp_ahora = float(trans) - usado_hoy
        enriquecidas.append((idc, den, float(trans), float(usado or 0), neto,
                             usado_hoy, disp_antes, disp_ahora, n))

    negativos = [e for e in enriquecidas if e[7] < 0]
    print(f"quedarían con DISPONIBLE NEGATIVO : {_fmt(len(negativos))}"
          f"   <- si son muchas, la semántica del cupo es otra")

    print(f"\nTOP {top} por magnitud del desvío (|flujo neto| desde la carga):")
    print(f"{'cuenta':>8s}  {'denominación':30s} {'usado hoy(foto)':>16s} "
          f"{'flujo neto':>16s} {'usado real':>16s} {'disp. real':>16s}")
    for e in sorted(enriquecidas, key=lambda x: abs(x[4]), reverse=True)[:top]:
        idc, den, trans, usado, neto, usado_hoy, _da, disp_ahora, _n = e
        print(f"{idc:>8s}  {(den or '—')[:30]:30s} {_fmt(usado):>16s} "
              f"{_fmt(neto):>16s} {_fmt(usado_hoy):>16s} {_fmt(disp_ahora):>16s}")

    total_neto = sum(e[4] for e in enriquecidas)
    print(f"\nflujo neto TOTAL no reflejado en el cupo: {_fmt(total_neto)} ARS")
    print("\nCÓMO LEER ESTO:")
    print("  · Si 'disponible negativo' es CERO o casi, la fórmula")
    print("    usado_real = usado_foto + depósitos − extracciones  es correcta y el fix es directo.")
    print("  · Si son muchas, el cupo NO es un acumulado histórico (probable ventana móvil o")
    print("    reseteo anual) → hay que preguntarle a compliance antes de codear nada.")


if __name__ == "__main__":
    main()
