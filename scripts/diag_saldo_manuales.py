"""scripts/diag_saldo_manuales.py — ¿el cierre de ayer es la apertura de hoy?

READ-ONLY. Solo hace SELECT; no escribe una sola fila.

**Para qué existe.** El back office reportó que una cuenta con registros manuales
muestra el saldo correcto el día que se carga el movimiento y **al día siguiente
arranca con el saldo crudo de Interbanking**, sin el manual. La regla que tiene
que cumplirse:

    saldo(F)          =  saldo del banco(F)  +  Σ manuales hasta F
    saldo_inicial(F)  =  saldo(F − 1 hábil)

o sea «el saldo inicial de hoy es el saldo final de ayer», aplicado todos los
días. Es ACUMULADO porque hay movimientos que Interbanking no informa nunca
(Comafi, BNY, la Patagonia recaudadora): si el ajuste durara un día, esas cuentas
volverían a cero teniendo la plata. Cuando un manual SÍ termina apareciendo en el
extracto, el back office carga otro EN CONTRA y los dos se cancelan.

Este diag lo muestra numéricamente, cuenta por cuenta y día por día, y marca con
`<<<` las filas donde la apertura no coincide con el cierre anterior — que es
exactamente el síntoma que se reportó.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.diag_saldo_manuales                # las cuentas con manuales
    python -m scripts.diag_saldo_manuales --cuenta 7     # una sola
    python -m scripts.diag_saldo_manuales --dias 10      # ventana (default 7)
    python -m scripts.diag_saldo_manuales --todas        # también las que no tienen
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from api.services._sql import _f, _q


def _plata(v: float | None) -> str:
    return "               —" if v is None else f"{v:>16,.2f}"


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
            WHERE c.activa AND (%s OR c.id = %s)
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
        print("=" * 104)
        print(f"[{c['id']}] {c['bank_name']} · {c['account_type']} {c['currency']} "
              f"{c['account_number']}"
              + ("   (cuenta MANUAL: Interbanking no la informa)"
                 if c["origen"] == "manual" else ""))
        print("-" * 104)
        print(f"{'fecha':<12}{'saldo banco':>16}{'manual del día':>16}"
              f"{'ACUMULADO':>16}{'SALDO':>16}{'INICIAL=ayer':>16}")
        print("-" * 104)

        # Se recorre el día anterior primero para poder mostrar la APERTURA de
        # cada fila, que ES el cierre de la fila de arriba. Esa igualdad es toda
        # la regla que este diag verifica.
        cierre_previo: float | None = None
        d = desde
        while d <= hasta:
            fila = _q(
                """SELECT e.saldo_cierre,
                          coalesce(s.saldo_operativo, s.saldo_dia) AS informado,
                          (SELECT sum(CASE WHEN tipo = 'C' THEN abs(importe)
                                           ELSE -abs(importe) END)
                             FROM bancos.movimientos_manuales m
                            WHERE m.cuenta_id = %s AND m.fecha = %s) AS manual,
                          (SELECT sum(CASE WHEN tipo = 'C' THEN abs(importe)
                                           ELSE -abs(importe) END)
                             FROM bancos.movimientos_manuales m
                            WHERE m.cuenta_id = %s AND m.fecha <= %s) AS acum
                     FROM (SELECT 1) x
                     LEFT JOIN bancos.extracto_dia e
                            ON e.cuenta_id = %s AND e.fecha = %s
                     LEFT JOIN bancos.saldos s
                            ON s.cuenta_id = %s AND s.fecha = %s""",
                (c["id"], d, c["id"], d, c["id"], d, c["id"], d))
            r = fila[0] if fila else {}
            banco = _f(r.get("saldo_cierre"))
            if banco is None:
                banco = _f(r.get("informado"))
            man = _f(r.get("manual")) or 0.0
            acum = _f(r.get("acum")) or 0.0
            saldo = (None if banco is None and not acum
                     else round((banco or 0.0) + acum, 2))

            # `<<<` = el acumulado no es lo del día, o sea que hay manuales de
            # días anteriores pesando. Es justo donde el modelo viejo mostraba
            # el saldo crudo de Interbanking.
            marca = "   <<< acá fallaba" if round(acum - man, 2) else ""

            print(f"{d.isoformat():<12}{_plata(banco)}{_plata(man)}{_plata(acum)}"
                  f"{_plata(saldo)}{_plata(cierre_previo)}{marca}")

            cierre_previo = saldo
            d += timedelta(days=1)

        detalle = _q(
            """SELECT fecha, descripcion, importe, tipo, creado_por
                 FROM bancos.movimientos_manuales
                WHERE cuenta_id = %s AND fecha >= %s
                ORDER BY fecha, creado_at""", (c["id"], desde))
        if detalle:
            print("\n  Movimientos manuales de la ventana:")
            for m in detalle:
                signo = "+" if m["tipo"] == "C" else "−"
                print(f"    {m['fecha']}  {signo}{abs(_f(m['importe']) or 0):>14,.2f}  "
                      f"{(m['descripcion'] or '')[:45]:<45}  {m['creado_por'] or ''}")
        print()

    print("=" * 104)
    print("Cómo leerlo:")
    print("  · SALDO    = saldo banco + ACUMULADO de manuales. Es lo que muestran")
    print("               CONSOLIDADO, el saldo de la vista y CIERRE BANCO de CONCILIAR.")
    print("  · INICIAL  = el SALDO del día hábil anterior («el saldo inicial de hoy")
    print("               es el saldo final de ayer»).")
    print("  · Las filas marcadas son las que el modelo viejo mostraba SIN el manual.")
    print("  · Si un manual aparece DESPUÉS en el extracto, el back office carga otro")
    print("    EN CONTRA ese día: los dos se cancelan y el saldo no se duplica.")


if __name__ == "__main__":
    main()
