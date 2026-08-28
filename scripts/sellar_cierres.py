"""scripts/sellar_cierres.py — sella el saldo al cierre de un rango de días.

**Qué hace.** Guarda en `bancos.cierres_diarios` el saldo al cierre de cada
cuenta para cada fecha del rango: el valor con fecha y banco que después se lee
como saldo INICIAL del día siguiente, sin recalcular nada.

⚠️ **EL ORDEN IMPORTA y por eso va de la fecha más vieja a la más nueva.** El
cierre de un día se arma como `cierre sellado de ayer + movimientos del banco de
hoy + manuales de hoy`, así que sellar el 27 antes que el 26 haría que el 27
arranque del saldo crudo de Interbanking en vez del cierre bueno. El script lo
recorre en orden solo.

**Cuándo hace falta correrlo a mano:**
  · **Una vez, después del deploy**, para sembrar los días que ya están en la
    base (la tabla nace vacía).
  · Si se corrigió un movimiento manual viejo y hay que re-sellar de ahí en
    adelante.

De ahí en más lo hace solo: `jobs/interbanking_sync` sella cada corrida, y cargar
o borrar un movimiento manual vuelve a sellar ese día.

Es idempotente: re-sellar pisa el valor anterior. No borra nada.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.sellar_cierres                    # los últimos 7 días
    python -m scripts.sellar_cierres --dias 30
    python -m scripts.sellar_cierres --desde 2026-08-20 --hasta 2026-08-27
    python -m scripts.sellar_cierres --dry              # muestra sin escribir
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from api.services import bancos as svc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--desde", help="AAAA-MM-DD")
    ap.add_argument("--hasta", help="AAAA-MM-DD (default: el día hábil anterior)")
    ap.add_argument("--dias", type=int, default=7,
                    help="cuántos días hacia atrás si no se da --desde")
    ap.add_argument("--dry", action="store_true", help="mostrar sin escribir")
    args = ap.parse_args()

    hasta = date.fromisoformat(args.hasta) if args.hasta else svc.fecha_default()
    desde = (date.fromisoformat(args.desde) if args.desde
             else hasta - timedelta(days=args.dias))
    if desde > hasta:
        raise SystemExit("--desde no puede ser posterior a --hasta.")

    print(f"Sellando de {desde} a {hasta}"
          + ("  (DRY: no escribe)" if args.dry else "") + "\n")

    d = desde
    total = 0
    while d <= hasta:
        if args.dry:
            saldos = svc._saldos_banco(d)
            print(f"  {d}  {len(saldos):>3} cuenta(s)")
            for cid, v in sorted(saldos.items())[:5]:
                print(f"        cuenta {cid:<5} {v['valor']:>16,.2f}   {v['fuente']}")
            if len(saldos) > 5:
                print(f"        … y {len(saldos) - 5} más")
        else:
            n = len(svc.sellar_cierre(d))
            total += n
            print(f"  {d}  {n:>3} cuenta(s) selladas")
        d += timedelta(days=1)

    if not args.dry:
        print(f"\nListo: {total} cierre(s) sellado(s).")
        print("El saldo inicial de cada día ya se lee de acá, sin recalcular.")


if __name__ == "__main__":
    main()
