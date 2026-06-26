"""scripts/diag_tenencia_hd.py — por qué un título no se reclasifica en Tenencia HD.

Compara la `unidad` en portafolio.assets (lo que editás en Manager → Assets) contra la
`unidad` en portafolio.tenencia (lo que valúa la vista). El cruce HD/ARS es por `unidad`
EXACTA → si difieren (espacio, mayúscula, formato) el título queda en ARS (catch-all) aunque
el asset diga cartera='HD'. Muestra todo entre [corchetes] para ver espacios. Read-only.

Uso (en el Droplet):
    python -m scripts.diag_tenencia_hd AL30        # patrón (substring, case-insensitive)
"""
from __future__ import annotations

import sys

CUENTAS = ["100", "255", "256"]


def main() -> int:
    if len(sys.argv) < 2:
        print("Falta el patrón. Ej: python -m scripts.diag_tenencia_hd AL30")
        return 1
    patron = f"%{sys.argv[1]}%"
    from core.postgres import connect

    with connect() as conn, conn.cursor() as cur:
        print(f"== portafolio.assets  (unidad ILIKE {sys.argv[1]!r}) ==")
        cur.execute(
            "SELECT unidad, cartera, actualizado_por, actualizado_at "
            "FROM portafolio.assets WHERE unidad ILIKE %s ORDER BY unidad", (patron,))
        rows = cur.fetchall()
        if not rows:
            print("  (ningún asset matchea — ¿la unidad del asset es otra?)")
        for u, cart, por, at in rows:
            print(f"  unidad=[{u}]  cartera=[{cart}]  por={por}  at={at}")

        print(f"\n== portafolio.tenencia  (unidad ILIKE {sys.argv[1]!r}, cuentas propias, última fecha) ==")
        cur.execute(
            "SELECT max(fecha) FROM portafolio.tenencia "
            "WHERE unidad ILIKE %s AND id_cuenta = ANY(%s)", (patron, CUENTAS))
        ult = cur.fetchone()[0]
        print(f"  última fecha con ese título: {ult}")
        if ult:
            cur.execute(
                "SELECT unidad, id_cuenta, cantidad, valuacion, aum "
                "FROM portafolio.tenencia "
                "WHERE unidad ILIKE %s AND id_cuenta = ANY(%s) AND fecha = %s ORDER BY unidad",
                (patron, CUENTAS, ult))
            for u, idc, cant, val, aum in cur.fetchall():
                print(f"  unidad=[{u}]  cuenta={idc}  cant={cant}  val={val}  aum={aum}")

        print("\n== VEREDICTO: ¿cada unidad de la tenencia matchea un asset HD? ==")
        cur.execute(
            """
            SELECT t.unidad,
                   a.cartera AS cartera_asset_match_exacto,
                   (t.unidad IN (SELECT unidad FROM portafolio.assets
                                 WHERE cartera = 'HD' AND unidad IS NOT NULL)) AS clasifica_hd
            FROM (SELECT DISTINCT unidad FROM portafolio.tenencia
                  WHERE unidad ILIKE %s AND id_cuenta = ANY(%s)) t
            LEFT JOIN portafolio.assets a ON a.unidad = t.unidad
            ORDER BY t.unidad
            """, (patron, CUENTAS))
        for u, cart, es_hd in cur.fetchall():
            if cart is None:
                diag = "❌ NO hay asset con esa unidad EXACTA → cae en ARS (catch-all)"
            elif es_hd:
                diag = "✅ clasifica HD"
            else:
                diag = f"❌ asset matchea pero cartera=[{cart}] (no es 'HD' exacto) → ARS"
            print(f"  tenencia.unidad=[{u}]  →  {diag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
