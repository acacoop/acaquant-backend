"""scripts/diag_vista_gastos.py — el payload EXACTO que recibe la pantalla.

Llama a `bancos.vista()`, que es la misma función que responde el endpoint del
modal de movimientos. No reimplementa nada: si acá el número da bien y en la
pantalla da mal, el problema está del navegador para acá (deploy viejo, caché,
otra fecha seleccionada) y no en el criterio.

⚠️ NO es 100% read-only: `vista()` deja UNA fila en la auditoría de accesos,
exactamente igual que abrir el modal desde la pantalla. No escribe ninguna otra
cosa — ni movimientos, ni reglas, ni el catálogo del desglose.

Imprime, para cada fecha pedida:
  · el desglose completo con TODAS sus claves (incluido `resto`);
  · el catálogo de columnas tal como viaja al front (clave, etiqueta, grupo);
  · movimiento por movimiento: es_gasto, de dónde salió la marca, y EN QUÉ BALDE
    cayó — que es el campo `gasto_balde` que la tabla hoy no dibuja.

Uso:
    python -m scripts.diag_vista_gastos --cuenta 000100010488
    python -m scripts.diag_vista_gastos --cuenta 000100010488 --fecha 2026-08-18
    python -m scripts.diag_vista_gastos --cuenta 000100010488 --dias 5
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta

from api.services import bancos as svc

SEP = "=" * 96


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cuenta", required=True,
                   help="número de cuenta o su terminación, ej. 000100010488")
    p.add_argument("--fecha", help="YYYY-MM-DD (default: los últimos --dias con datos)")
    p.add_argument("--dias", type=int, default=4,
                   help="cuántos días hacia atrás mirar si no se pasa --fecha")
    p.add_argument("--email", default="diag@acavalores.com.ar",
                   help="solo para la fila de auditoría")
    args = p.parse_args()

    ctas = [c for c in svc._q(
        """SELECT id, bank_name, account_label, account_number, account_type,
                  currency FROM bancos.cuentas
            WHERE account_number LIKE %s ORDER BY id""", (f"%{args.cuenta.strip()}%",))]
    if not ctas:
        print(f"No hay cuenta con «{args.cuenta}».")
        return

    print(SEP)
    print("CUENTAS QUE MATCHEAN (ojo si hay más de una: puede ser ARS y USD)")
    print(SEP)
    for c in ctas:
        print(f"  id={c['id']:<5} {c['bank_name']} · {c['account_label']} · "
              f"{c['account_type']} {c['currency']} · {c['account_number']}")

    if args.fecha:
        fechas = [datetime.strptime(args.fecha, "%Y-%m-%d").date()]
    else:
        hoy = svc.ahora_ar().date()
        fechas = [hoy - timedelta(days=i) for i in range(args.dias)]

    for cta in ctas:
        for f in fechas:
            data = svc.vista(args.email, cta["id"], f)
            movs = data["movimientos"]
            r = data["resumen"]
            print()
            print(SEP)
            print(f"CUENTA {cta['id']} ({cta['account_type']} {cta['currency']})  ·  {f}"
                  f"  ·  {len(movs)} movimientos")
            print(SEP)
            if not movs:
                print("  (sin movimientos ese día)")
                continue

            print(f"  resumen.gastos = {r['gastos']}")
            print("  resumen.gastos_desglose (las claves son las que lee el front):")
            for k, v in r["gastos_desglose"].items():
                print(f"     {k:<20} {v}")
            print("  data.desglose (el catálogo que dibuja las etiquetas):")
            for b in data["desglose"]:
                print(f"     {b['orden']:>4}  {b['clave']:<20} {b['etiqueta']:<26} {b['grupo']}")

            print("\n  MOVIMIENTOS marcados como gasto:")
            hubo = False
            for m in movs:
                if not m.get("es_gasto"):
                    continue
                hubo = True
                print(f"     {m['tipo']} {m['importe']:>14}  balde={m.get('gasto_balde')!r:<16} "
                      f"origen={m.get('gasto_origen')!r:<10} "
                      f"ignorado={m['ignorado']}")
                print(f"        desc={m['descripcion']!r}  concepto={m['concepto']!r}")
            if not hubo:
                print("     (ninguno)")

            # Lo que el back office viene buscando: un gasto que ninguna columna
            # agarró. Se lista aparte porque es lo único accionable de la corrida.
            sueltos = [m for m in movs
                       if m.get("es_gasto") and m.get("gasto_balde") == svc.RESTO]
            if sueltos:
                print(f"\n  ⚠️ SIN COLUMNA ({len(sueltos)}) — les falta una grafía en el desglose:")
                for m in sueltos:
                    print(f"     {m['importe']:>14}  {m['descripcion']!r}  "
                          f"concepto={m['concepto']!r}")


if __name__ == "__main__":
    main()
