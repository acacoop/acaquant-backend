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
    _get_mep_for_date,
)
from core.postgres import get_pool


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
            print(f"ℹ️ {_fmt(sin_mep)} movimientos en USD sin mep snapshot → se pesifican "
                  f"con el fallback de producción (cotización de esa fecha).")

        print("\n" + "=" * 78)
        print(f"4) EL IMPACTO — flujo neto por cuenta DESDE {desde_ancla}")
        print("=" * 78)
        print("ancla: se usa `cupo_cargado_en` cuando está cargado y, si está NULL "
              f"(el caso hoy), la fecha {desde_ancla} pasada por --desde.")
        # `denominacion` NO vive en comitentes: es de clientes.cuentas (mismo join
        # que hace la vista comercial, comercial_sql.py::_col).
        cur.execute("""
            SELECT c.id_cuenta, u.denominacion,
                   c.cupo_transaccional_ars, c.cupo_usado_ars
            FROM clientes.comitentes c
            LEFT JOIN clientes.cuentas u ON u.id_cuenta = c.id_cuenta
            WHERE c.cupo_transaccional_ars IS NOT NULL""")
        cupos = {r[0]: {"den": r[1], "trans": float(r[2]),
                        "usado": float(r[3] or 0)} for r in cur.fetchall()}

        # Los movimientos se traen FILA POR FILA (no agregados en SQL) para poder
        # pesificar con la MISMA regla que producción: `mep` snapshot de la fila y,
        # si viene NULL, fallback a get_mep_for_date (docs/MOTOR_VALUACIONES.md).
        # Agregando en SQL había que tirar las filas sin mep — 434 movimientos.
        cur.execute("""
            SELECT id_cuenta, fecha, categoria, importe, moneda, mep
            FROM operaciones.negocio_movimientos
            WHERE categoria = ANY(%s) AND fecha >= %s::date
              AND id_cuenta IS NOT NULL""",
                    (list(_FLUJOS_EXTERNOS_ALL), desde_ancla))
        movs = cur.fetchall()

    # Pesificación con la regla de producción + fallback.
    mep_cache: dict[str, float | None] = {}
    agg: dict[str, dict] = {}
    sin_cotizacion = 0
    for idc, fecha, categoria, importe, moneda, mep in movs:
        if idc not in cupos:
            continue
        try:
            imp = float(importe or 0)
        except (TypeError, ValueError):
            continue
        if (moneda or "ARS") == "ARS":
            imp_ars = imp
        else:
            tc = float(mep) if mep is not None else None
            if tc is None:
                clave = fecha.isoformat()
                if clave not in mep_cache:
                    mep_cache[clave] = _get_mep_for_date(clave)
                tc = mep_cache[clave]
            if tc is None:
                sin_cotizacion += 1
                continue
            imp_ars = imp * tc
        a = agg.setdefault(idc, {"deposito": 0.0, "transferencia": 0.0,
                                 "extraccion": 0.0, "n": 0})
        a[categoria] = a.get(categoria, 0.0) + imp_ars
        a["n"] += 1

    if sin_cotizacion:
        print(f"⚠️ {_fmt(sin_cotizacion)} movimientos en USD quedaron fuera: ni mep "
              f"snapshot ni cotización para esa fecha.")

    print(f"cuentas con cupo cargado          : {_fmt(len(cupos))}")
    print(f"cuentas CON flujo desde esa fecha : {_fmt(len(agg))}"
          f"   <- a estas les cambia el número HOY")
    if not agg:
        print("\n(no hay flujos posteriores al ancla: el cupo no se desvió)")
        return

    # El importe ya viene con signo cliente (+ depósito, − extracción) según
    # valuaciones.py, así que el neto es la suma de las tres categorías.
    filas_out = []
    for idc, a in agg.items():
        c = cupos[idc]
        neto = a["deposito"] + a["transferencia"] + a["extraccion"]
        usado_real = c["usado"] + neto
        filas_out.append({
            "id": idc, "den": c["den"], "trans": c["trans"], "usado": c["usado"],
            "dep": a["deposito"], "transf": a["transferencia"], "ext": a["extraccion"],
            "neto": neto, "usado_real": usado_real,
            "disp": c["trans"] - usado_real, "n": a["n"],
        })

    negativos = [f for f in filas_out if f["disp"] < 0]
    ya_excedidas = [f for f in negativos if f["trans"] - f["usado"] < 0]
    nuevas = [f for f in negativos if f["trans"] - f["usado"] >= 0]
    print(f"quedarían con DISPONIBLE NEGATIVO : {_fmt(len(negativos))}")
    print(f"   · de esas, YA estaban excedidas antes del flujo : {_fmt(len(ya_excedidas))}")
    print(f"   · las que se pasan POR EL FLUJO nuevo           : {_fmt(len(nuevas))}"
          f"  <- estas son el hallazgo")

    # ¿El exceso es real o un artefacto de contar `transferencia` como depósito?
    # `transferencia` puede ser movimiento de TÍTULOS, no de plata: para TWR es un
    # flujo externo válido, para el CUPO de fondeo puede no corresponder.
    solo_transf = [f for f in nuevas if f["transf"] and
                   (f["trans"] - (f["usado"] + f["dep"] + f["ext"])) >= 0]
    if nuevas:
        print(f"   · de las nuevas, se pasan SOLO por 'transferencia' : {_fmt(len(solo_transf))}"
              f"  <- ojo: puede ser movimiento de TÍTULOS, no de plata")

    print(f"\nTOP {top} por magnitud del desvío (|flujo neto| desde el ancla):")
    print(f"{'cuenta':>8s}  {'denominación':28s} {'usado(foto)':>15s} {'depósitos':>15s} "
          f"{'transfer.':>15s} {'extracc.':>15s} {'usado real':>15s} {'disp. real':>15s}")
    for f in sorted(filas_out, key=lambda x: abs(x["neto"]), reverse=True)[:top]:
        print(f"{f['id']:>8s}  {(f['den'] or '—')[:28]:28s} {_fmt(f['usado']):>15s} "
              f"{_fmt(f['dep']):>15s} {_fmt(f['transf']):>15s} {_fmt(f['ext']):>15s} "
              f"{_fmt(f['usado_real']):>15s} {_fmt(f['disp']):>15s}")

    print(f"\nflujo neto TOTAL no reflejado en el cupo: "
          f"{_fmt(sum(f['neto'] for f in filas_out))} ARS")
    print(f"  depósitos {_fmt(sum(f['dep'] for f in filas_out))} · "
          f"transferencias {_fmt(sum(f['transf'] for f in filas_out))} · "
          f"extracciones {_fmt(sum(f['ext'] for f in filas_out))}")
    print("\nCÓMO LEER ESTO:")
    print("  · 'se pasan POR EL FLUJO nuevo' son clientes operando por encima de su cupo")
    print("    declarado HOY, y el sistema no lo muestra. Es un tema de compliance, no un bug.")
    print("  · Si la mayoría se pasa SOLO por 'transferencia', antes de codear hay que")
    print("    confirmar si una transferencia de TÍTULOS consume cupo de fondeo o no.")


if __name__ == "__main__":
    main()
