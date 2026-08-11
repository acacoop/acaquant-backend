"""Limpia los cheques RECIBIDOS que el espejo creó de MÁS en su primer día.

QUÉ PASÓ (2026-08-11, primer día en producción). El cron arrancó con una ventana
de 3 días hábiles, así que el martes 11 alcanzó los movimientos del **viernes 07**
— días que el back office YA había cargado a mano el lunes. Resultado: 5 filas
espejo duplicando cheques que ya existían, y encima visibles en el tablero del
MARTES (el tablero filtraba por día de CARGA, no por el día en que la plata entra).

Las dos causas quedaron arregladas en el código:
  · la ventana del cron bajó a 2 días hábiles (ayer + hoy), que es el flujo real;
  · el tablero pasó a ubicar los espejo por `fecha_pago` (`_DIA_RECIBIDO`).

Pero las filas ya creadas siguen ahí, y desde la vista NO se pueden borrar (los
`origen='aunesa'` están protegidos). Esto las saca.

QUÉ BORRA — sólo lo que es seguro borrar, las tres condiciones a la vez:
  · `origen='aunesa'`  → nació del espejo, nadie lo tipeó;
  · `estado='pendiente'` y SIN banco → nadie lo tocó ni lo imputó a ningún saldo;
  · `fecha_pago` ANTERIOR al corte → pertenece a un día ya cerrado a mano.
Si el back office ya le puso banco o lo finalizó, NO se toca: eso es trabajo de
alguien y se resuelve mirándolo, no borrando.

Idempotente (re-correrlo no hace nada) y read-only salvo que pases --apply.

Uso (desde la raíz, en el Droplet):
    python -m scripts.fix_espejo_recibidos              # solo lista
    python -m scripts.fix_espejo_recibidos --apply      # borra
    python -m scripts.fix_espejo_recibidos --antes-de 2026-08-11 --apply
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from api.services._sql import _q
from core.postgres import get_pool

_TABLA = "operaciones.tesoreria_cheques"
# Mismas tres condiciones de la docstring, en un solo lugar: lo listado y lo
# borrado no pueden divergir.
_WHERE = (
    "lado = 'recibido' AND origen = 'aunesa' AND estado = 'pendiente' "
    "AND COALESCE(banco, '') = '' AND fecha_pago IS NOT NULL AND fecha_pago < %(corte)s"
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--antes-de", dest="antes",
                    help="YYYY-MM-DD; borra los de fecha_pago ANTERIOR (default: hoy ART)")
    ap.add_argument("--apply", action="store_true", help="borra de verdad (default: solo lista)")
    args = ap.parse_args()

    corte = (datetime.strptime(args.antes, "%Y-%m-%d").date() if args.antes
             else (datetime.now(UTC) - timedelta(hours=3)).date())

    filas = _q(
        f"SELECT id, mov_id, comitente_denominacion, unidad, importe, fecha_pago, creado_at "
        f"  FROM {_TABLA} WHERE {_WHERE} ORDER BY fecha_pago, id", {"corte": corte})

    print(f"\nESPEJO de RECIBIDOS a limpiar — fecha_pago anterior a {corte}")
    print(f"Candidatos (espejo + pendiente + sin banco): {len(filas)}\n")
    if not filas:
        print("Nada para borrar. ✔")
        return 0

    tot: dict[str, float] = {}
    for r in filas:
        u = str(r["unidad"] or "ARS")
        tot[u] = tot.get(u, 0.0) + float(r["importe"] or 0)
        print(f"   #{r['id']:<6} {r['mov_id'] or ''!s:<22} {u:>4} "
              f"{float(r['importe'] or 0):>18,.2f}  pago={r['fecha_pago']}  "
              f"{str(r['comitente_denominacion'] or '')[:34]}")
    print("\nTotal por moneda: " + " · ".join(f"{u} {v:,.2f}" for u, v in sorted(tot.items())))

    # Los que NO se tocan, para que quede claro qué queda vivo y por qué.
    intocables = _q(
        f"SELECT count(*) AS n FROM {_TABLA} "
        " WHERE lado = 'recibido' AND origen = 'aunesa' "
        "   AND (estado <> 'pendiente' OR COALESCE(banco, '') <> '')")[0]["n"]
    if intocables:
        print(f"\n({intocables} espejo(s) NO entran: ya tienen banco o estado tocado — "
              "esos se revisan a mano, no se borran a ciegas.)")

    if not args.apply:
        print("\n[DRY] No se borró nada. Repetir con --apply para hacerlo efectivo.")
        return 0

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {_TABLA} WHERE {_WHERE}", {"corte": corte})
        n = cur.rowcount
        conn.commit()
    print(f"\n✔ Borrados {n} cheque(s) espejo. El cron no los vuelve a crear: su ventana "
          "ahora es de 2 días hábiles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
