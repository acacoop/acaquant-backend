"""scripts/diag_variacion_sql.py — valida el path SQL de variacion_titulos.

Corre la descomposición mercado/operado desde `portafolio.tenencia` para una cuenta
y verifica el INVARIANTE contable: `delta_total == delta_mercado + delta_operado`
(por fila y en el total). NO hay baseline Mongo (Valuaciones.AuM fue eliminada) → la
validación es el invariante + que los números cierren con sentido.

Uso:
  python -m scripts.diag_variacion_sql --cuenta 805 [--fecha 2026-05-30]
"""
import argparse

from api.services import valuaciones_sql as v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cuenta", required=True)
    ap.add_argument("--fecha", default=None, help="YYYY-MM-DD (cierre de mes); default = último")
    args = ap.parse_args()

    fecha = args.fecha
    if not fecha:
        rows = v._q("SELECT DISTINCT fecha FROM portafolio.tenencia "
                    "WHERE id_cuenta = %(c)s AND aum = 'si' ORDER BY fecha", {"c": args.cuenta})
        fechas = [v._iso(r["fecha"]) for r in rows if r["fecha"] is not None]
        if not fechas:
            print(f"sin tenencia para cuenta {args.cuenta}")
            return
        cierres: dict[str, str] = {}
        for f in fechas:
            cierres[f[:7]] = f
        fecha = sorted(cierres.values())[-1]
        print(f"(fecha auto = último cierre de mes: {fecha})")

    r = v.variacion_titulos(id_cuenta=args.cuenta, fecha=fecha)
    if r.get("error"):
        print(f"ERROR: {r['error']}  (fecha={fecha})")
        return

    t = r["totales"]
    print(f"cuenta {args.cuenta} · {r['fecha_anterior']} → {r['fecha']}")
    print(f"  val_anterior={t['val_anterior']:,.2f}   val_actual={t['val_actual']:,.2f}")
    print(f"  mercado={t['delta_mercado']:,.2f}   operado={t['delta_operado']:,.2f}   "
          f"total={t['delta_total']:,.2f}")

    suma = round(t["delta_mercado"] + t["delta_operado"], 2)
    ok = abs(suma - t["delta_total"]) < 0.5
    print(f"  INVARIANTE total: merc+oper={suma:,.2f} vs total={t['delta_total']:,.2f}  "
          f"→ {'OK ✅' if ok else 'FALLA ❌'}")

    fallas = [f for f in r["filas"]
              if abs(round(f["delta_mercado"] + f["delta_operado"], 2) - f["delta_total"]) > 0.5]
    print(f"  filas: {len(r['filas'])}  ·  que NO cierran: {len(fallas)}")

    print("  top 5 por |delta_total|:")
    for f in r["filas"][:5]:
        print(f"    {f['unidad']:<26} merc={f['delta_mercado']:>14,.2f} "
              f"oper={f['delta_operado']:>14,.2f} tot={f['delta_total']:>14,.2f} [{f['estado']}]")
    o = r["otros"]
    print(f"  otros (cash): {o['val_anterior']:,.2f} → {o['val_actual']:,.2f}  "
          f"total={o['delta_total']:,.2f}  (n={o['n']})")


if __name__ == "__main__":
    main()
