"""scripts/diag_cuentas_operar.py — estado del id_cuenta que usa Operar (Dólar MEP).

READ-ONLY. Verifica la hipótesis del incidente: `clientes.cuentas.id_cuenta` quedó
guardado SIN ceros a la izquierda (ej. '9' en vez de '009'), y ese id es el que Operar
manda a ROFEX (get_account_report / órdenes) → ROFEX no lo reconoce → no trae saldos.

Muestra:
  • distribución de id_cuenta por cantidad de dígitos (numéricos).
  • las cuentas de <3 dígitos con su denominación y qué daría zfill(3) ('9' → '009').
  • si el mismo id_cuenta está igual o distinto en clientes.comitentes (consistencia interna).

No imprime saldos ni PII sensible más allá de la denominación (nombre de la cuenta).

Uso: python -m scripts.diag_cuentas_operar
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        # Distribución por longitud (solo id numéricos, que es lo que lista Operar).
        cur.execute(
            "SELECT length(id_cuenta) AS digitos, COUNT(*) AS n "
            "FROM clientes.cuentas WHERE id_cuenta ~ '^[0-9]+$' "
            "GROUP BY length(id_cuenta) ORDER BY digitos")
        dist = cur.fetchall()
        print("clientes.cuentas — id_cuenta por cantidad de dígitos:")
        for dig, n in dist:
            flag = "  ← < 3 dígitos (candidatos a romper ROFEX)" if dig < 3 else ""
            print(f"    {dig} dígito(s): {n} cuentas{flag}")

        # Muestra de las cortas (<3) con denominación + qué daría zfill(3).
        cur.execute(
            "SELECT id_cuenta, denominacion FROM clientes.cuentas "
            "WHERE id_cuenta ~ '^[0-9]+$' AND length(id_cuenta) < 3 "
            "ORDER BY id_cuenta::int LIMIT 25")
        cortas = cur.fetchall()
        print(f"\ncuentas de <3 dígitos ({len(cortas)} muestra, máx 25):")
        for idc, deno in cortas:
            print(f"    id={idc!r:6} → zfill(3)={str(idc).zfill(3)!r:6}  {str(deno or '')[:40]}")

        # ¿El id_cuenta está igual en comitentes? (consistencia interna de nuestra base)
        cur.execute(
            "SELECT ct.id_cuenta AS cuentas_id, cm.id_cuenta AS comitentes_id "
            "FROM clientes.cuentas ct "
            "LEFT JOIN clientes.comitentes cm ON cm.id_cuenta = ct.id_cuenta "
            "WHERE ct.id_cuenta ~ '^[0-9]+$' AND length(ct.id_cuenta) < 3 "
            "LIMIT 10")
        print("\nmismo id en comitentes (LEFT JOIN por id_cuenta):")
        for c_id, cm_id in cur.fetchall():
            estado = "match" if cm_id is not None else "NO está en comitentes con ese id"
            print(f"    cuentas={c_id!r:6} comitentes={str(cm_id)!r:6}  → {estado}")

    print("\nLectura: si hay cuentas de 1-2 dígitos, el fix es normalizar a zfill(3) "
          "en el punto de entrada de Operar (listado_cuentas) + al llamar a ROFEX.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
