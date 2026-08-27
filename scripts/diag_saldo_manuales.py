"""scripts/diag_saldo_manuales.py — ¿el saldo arrastra los movimientos manuales?

READ-ONLY. Solo hace SELECT; no escribe una sola fila.

**Para qué existe.** El back office reportó que una cuenta con registros manuales
muestra el saldo correcto el día que se carga el movimiento y al día siguiente
vuelve al saldo crudo de Interbanking, como si la carga no hubiera existido. La
causa es que el ajuste manual se calculaba POR DÍA, cuando un manual es plata que
el banco **no va a informar nunca** y por lo tanto sigue formando parte del saldo
todos los días siguientes.

Este diag lo muestra numéricamente, cuenta por cuenta y día por día:

    saldo del banco  +  acumulado de manuales (≤ ese día)  =  nuestro saldo

Y marca con `!!` la fila donde el ACUMULADO y lo DEL DÍA no coinciden, que es
exactamente donde el modelo viejo mostraba mal.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_saldo_manuales                # todas las cuentas con manuales
    python -m scripts.diag_saldo_manuales --cuenta 7     # una cuenta
    python -m scripts.diag_saldo_manuales --dias 10      # ventana (default 7)
    python -m scripts.diag_saldo_manuales --todas        # incluí las que no tienen manuales
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from api.services._sql import _f, _q


def _plata(v: float | None) -> str:
    return "        —" if v is None else f"{v:>16,.2f}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", type=int, help="cuenta_id de bancos.cuentas")
    ap.add_argument("--dias", type=int, default=7, help="ventana hacia atrás (default 7)")
    ap.add_argument("--todas", action="store_true",
                    help="también las cuentas sin ningún movimiento manual")
    args = ap.parse_args()

    hasta = date.today()
    desde = hasta - timedelta(days=args.dias)

    cuentas = _q(
        """SELECT c.id, c.bank_name, c.account_number, c.account_type, c.currency,
                  c.origen,
                  (SELECT count(*) FROM bancos.movimientos_manuales m
                    WHERE m.cuenta_id = c.id) AS manuales
             FROM bancos.cuentas c
            WHERE c.activa
              AND (%s OR c.id = %s)
            ORDER BY c.bank_name, c.currency, c.account_number""",
        (args.cuenta is None, args.cuenta))

    if not args.todas:
        cuentas = [c for c in cuentas if c["manuales"]]

    if not cuentas:
        print("No hay cuentas con movimientos manuales. Nada que mirar.")
        print("(Con --todas se listan igual todas las cuentas activas.)")
        return

    print(f"Ventana: {desde} → {hasta}   ({len(cuentas)} cuenta(s))\n")

    for c in cuentas:
        etiqueta = (f"[{c['id']}] {c['bank_name']} · {c['account_type']} "
                    f"{c['currency']} {c['account_number']}"
                    + ("  (cuenta MANUAL)" if c["origen"] == "manual" else ""))
        print("=" * 100)
        print(etiqueta)
        print(f"  movimientos manuales cargados en total: {c['manuales']}")
        print("-" * 100)
        print(f"{'fecha':<12}{'saldo banco':>17}{'manual del día':>17}"
              f"{'ACUMULADO':>17}{'nuestro saldo':>17}   ")
        print("-" * 100)

        d = desde
        while d <= hasta:
            saldo = _q(
                """SELECT coalesce(e.saldo_cierre,
                                   s.saldo_operativo, s.saldo_dia) AS saldo
                     FROM (SELECT 1) x
                     LEFT JOIN bancos.extracto_dia e
                            ON e.cuenta_id = %s AND e.fecha = %s
                     LEFT JOIN bancos.saldos s
                            ON s.cuenta_id = %s AND s.fecha = %s""",
                (c["id"], d, c["id"], d))
            banco = _f(saldo[0]["saldo"]) if saldo else None

            man = _q(
                """SELECT sum(CASE WHEN tipo = 'C' THEN abs(importe)
                                   ELSE -abs(importe) END) AS acumulado,
                          sum(CASE WHEN fecha = %s
                                   THEN (CASE WHEN tipo = 'C' THEN abs(importe)
                                              ELSE -abs(importe) END)
                                   ELSE 0 END) AS del_dia
                     FROM bancos.movimientos_manuales
                    WHERE cuenta_id = %s AND fecha <= %s""",
                (d, c["id"], d))
            acum = _f(man[0]["acumulado"]) or 0.0 if man else 0.0
            dia = _f(man[0]["del_dia"]) or 0.0 if man else 0.0

            if banco is None and not acum:
                nuestro = None
            else:
                nuestro = round((banco or 0.0) + acum, 2)

            # `!!` = el acumulado NO es lo del día. Es justo la fila donde el
            # modelo viejo (ajuste por día) mostraba un saldo sin el manual.
            marca = "  <<< acá fallaba" if round(acum - dia, 2) else ""
            print(f"{d.isoformat():<12}{_plata(banco)}{_plata(dia)}"
                  f"{_plata(acum)}{_plata(nuestro)}{marca}")
            d += timedelta(days=1)

        detalle = _q(
            """SELECT fecha, descripcion, importe, tipo, creado_por
                 FROM bancos.movimientos_manuales
                WHERE cuenta_id = %s ORDER BY fecha, creado_at""", (c["id"],))
        if detalle:
            print("\n  Los movimientos manuales de esta cuenta (TODOS, no solo la ventana):")
            for m in detalle:
                signo = "+" if m["tipo"] == "C" else "−"
                print(f"    {m['fecha']}  {signo}{abs(_f(m['importe']) or 0):>14,.2f}  "
                      f"{(m['descripcion'] or '')[:45]:<45}  {m['creado_por'] or ''}")
        print()

    print("=" * 100)
    print("Cómo leerlo:")
    print("  · nuestro saldo = saldo banco + ACUMULADO. Es lo que tienen que mostrar")
    print("    CONSOLIDADO, el saldo de la vista y el SALDO INICIO de CONCILIAR")
    print("    (donde el saldo inicio de un día es el nuestro del día hábil anterior).")
    print("  · Las filas marcadas son las que el modelo viejo mostraba SIN el manual.")
    print("  · Si alguno de esos manuales aparece DESPUÉS en el extracto del banco,")
    print("    queda contado dos veces: hay que BORRARLO desde la misma pantalla.")


if __name__ == "__main__":
    main()
