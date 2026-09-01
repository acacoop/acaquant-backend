"""READ-ONLY. El ≠ del SALDO AL CIERRE: los dos números que informa el banco.

Herramienta: diag · Cero escrituras. Corre sobre las fechas que hay en la base.

EL PUNTO
========

En el CONSOLIDADO, una cuenta puede mostrar el badge **≠** con el mensaje «el
extracto cierra en X y el saldo informado dice Y». Los dos los manda Interbanking,
por **dos APIs distintas**, y la pantalla se queda con el del EXTRACTO:

  · `bancos.extracto_dia.saldo_cierre` ← API de **Extractos** (`ending_balance`).
    Viene con apertura, créditos y débitos, así que se puede AUDITAR:
    `apertura + créditos − débitos = cierre`. Solo existe los días CON movimientos.
  · `bancos.saldos`                    ← API de **Saldos**. Responde se haya
    movido la cuenta o no, y por eso existe: con la ventana corta del back office,
    una cuenta quieta no tiene ninguna fila de extracto.

La precedencia («extracto si lo hay, si no el informado») vive en UN solo lugar,
`bancos._saldos_banco()`, y es la misma para TODAS las cuentas. Cuando los dos
números coinciden —el caso normal— no se nota cuál ganó; el ≠ aparece solo cuando
difieren.

LO QUE ESTE DIAG VIENE A MEDIR
=============================

Que difieran puede ser dos cosas MUY distintas, y no se puede decidir sin ver los
números:

  (a) **No son el mismo concepto.** El lado «informado» se arma con
      `coalesce(saldo_operativo, saldo_dia)`, y `saldo_operativo` es
      `current_operating_balance` — el saldo OPERATIVO, la foto de HOY, que no es
      el cierre contable de un día. Si el ≠ sale de comparar el cierre del
      extracto contra el operativo, la diferencia es de definición y el badge
      está gritando por algo que no es un error.
  (b) **El extracto no cierra.** Si `apertura + créditos − débitos ≠ cierre`, el
      sospechoso es el extracto y ahí el ≠ está bien puesto.

Por cada cuenta y fecha imprime los DOS lados enteros, la verificación aritmética
del extracto, qué eligió `_saldos_banco()` y qué diferencia daría comparando
contra `saldo_dia` (el saldo del día) en vez de contra el operativo.

Uso:
    python -m scripts.diag_saldo_cierre
    python -m scripts.diag_saldo_cierre --solo-diferencias
    python -m scripts.diag_saldo_cierre --cuenta 12
"""
from __future__ import annotations

import argparse
import json

from api.services import bancos as B


def _n(v) -> float | None:
    return None if v is None else float(v)


def _p(v) -> str:
    return "—" if v is None else f"{v:>18,.2f}"


def _filas(cuenta: int | None) -> list[dict]:
    """Una fila por (cuenta, fecha) que exista en CUALQUIERA de las dos tablas.

    El universo de fechas es la UNIÓN y no una de las dos: si arrancara del
    extracto, las cuentas quietas —que son justo las que esta API vino a
    cubrir— no aparecerían nunca; si arrancara de saldos, se perderían los días
    que el banco informa por extracto y no por saldos.
    """
    where = "c.activa" if cuenta is None else "c.id = %s"
    args: tuple = () if cuenta is None else (cuenta,)
    return B._q(
        f"""WITH dias AS (
                SELECT cuenta_id, fecha FROM bancos.extracto_dia
                UNION
                SELECT cuenta_id, fecha FROM bancos.saldos
            )
            SELECT c.id, c.bank_name, c.account_number, c.currency, c.origen,
                   d.fecha,
                   e.saldo_apertura, e.saldo_cierre, e.total_creditos,
                   e.total_debitos, e.total_movimientos, e.cierra, e.diferencia,
                   s.saldo_dia, s.saldo_contable, s.saldo_operativo,
                   s.saldo_operativo_ini, s.proyectado_24hs, s.proyectado_48hs,
                   s.es_foto, s.raw
              FROM dias d
              JOIN bancos.cuentas   c ON c.id = d.cuenta_id
              LEFT JOIN bancos.extracto_dia e
                     ON e.cuenta_id = d.cuenta_id AND e.fecha = d.fecha
              LEFT JOIN bancos.saldos       s
                     ON s.cuenta_id = d.cuenta_id AND s.fecha = d.fecha
             WHERE {where}
             ORDER BY c.bank_name, c.account_number, d.fecha""",
        args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cuenta", type=int, default=None, help="acotar a UNA cuenta (id)")
    ap.add_argument("--solo-diferencias", action="store_true",
                    help="solo las filas donde los dos números no coinciden")
    ap.add_argument("--raw", action="store_true",
                    help="además, el bloque `balances` crudo que manda el banco")
    args = ap.parse_args()

    filas = _filas(args.cuenta)
    con_dif = 0
    solo_definicion = 0
    ext_no_cierra = 0
    mostradas = 0

    print("=" * 100)
    print("SALDO AL CIERRE — extracto vs. saldo informado")
    print("=" * 100)

    for r in filas:
        fecha = r["fecha"]
        cierre = _n(r["saldo_cierre"])
        dia = _n(r["saldo_dia"])
        operativo = _n(r["saldo_operativo"])
        # Exactamente lo que compara el consolidado para dibujar el ≠.
        informado = operativo if operativo is not None else dia
        dif = (None if cierre is None or informado is None
               else round(cierre - informado, 2))
        # La misma comparación pero contra el SALDO DEL DÍA, que es el número
        # homogéneo con un cierre. Si acá da 0 y arriba no, la diferencia era
        # de definición y no un error.
        dif_dia = None if cierre is None or dia is None else round(cierre - dia, 2)

        hay_dif = dif is not None and abs(dif) >= 0.01
        if hay_dif:
            con_dif += 1
            if dif_dia is not None and abs(dif_dia) < 0.01:
                solo_definicion += 1
        if r["cierra"] is False:
            ext_no_cierra += 1
        if args.solo_diferencias and not hay_dif:
            continue
        mostradas += 1

        print(f"\n[{r['id']}] {r['bank_name']} {r['account_number']} {r['currency']}"
              f"   {fecha}   origen={r['origen'] or 'interbanking'}"
              + ("   ← ≠ EN PANTALLA" if hay_dif else ""))

        print("  EXTRACTO (API Extractos — solo días CON movimientos)")
        print(f"    apertura            {_p(_n(r['saldo_apertura']))}")
        print(f"    + créditos          {_p(_n(r['total_creditos']))}")
        print(f"    − débitos           {_p(_n(r['total_debitos']))}")
        print(f"    = CIERRE            {_p(cierre)}   ({r['total_movimientos']} movs)")
        if r["cierra"] is None:
            print("    verificación: el banco no mandó todo lo necesario para chequearla")
        elif r["cierra"]:
            print("    verificación: ✓ apertura + créditos − débitos = cierre")
        else:
            print(f"    verificación: ✗ NO CIERRA por {_n(r['diferencia']):,.2f} "
                  "← acá el sospechoso ES el extracto")

        print("  SALDOS (API Saldos — responde se haya movido o no)")
        print(f"    saldo_dia           {_p(dia)}   ← el saldo DEL DÍA (historical_balances)")
        print(f"    saldo_contable      {_p(_n(r['saldo_contable']))}")
        print(f"    saldo_operativo     {_p(operativo)}   ← foto de HOY, NO el cierre de un día")
        print(f"    saldo_operativo_ini {_p(_n(r['saldo_operativo_ini']))}")
        print(f"    proyectado 24 / 48  {_p(_n(r['proyectado_24hs']))} / "
              f"{_p(_n(r['proyectado_48hs']))}")
        print(f"    es_foto             {r['es_foto']}")

        print("  COMPARACIÓN")
        print(f"    lo que compara la pantalla   cierre − {'operativo' if operativo is not None else 'saldo_dia'}"
              f"  = {_p(dif)}")
        print(f"    contra el SALDO DEL DÍA      cierre − saldo_dia   = {_p(dif_dia)}")
        if hay_dif and dif_dia is not None and abs(dif_dia) < 0.01:
            print("    ⚠️  Contra el saldo del día da CERO: el ≠ sale de comparar el cierre")
            print("        contra el saldo OPERATIVO, que es otra cosa. Diferencia de")
            print("        DEFINICIÓN, no un error del banco.")

        if args.raw and r.get("raw"):
            crudo = r["raw"] if isinstance(r["raw"], dict) else json.loads(r["raw"])
            print("  RAW balances: " + json.dumps(crudo.get("balances") or {},
                                                  ensure_ascii=False))

    print("\n" + "=" * 100)
    print(f"filas miradas: {len(filas)}   ·   mostradas: {mostradas}")
    print(f"con ≠ en pantalla:                         {con_dif}")
    print(f"  de esas, que dan CERO contra saldo_dia:  {solo_definicion}"
          "   ← diferencia de definición, no error")
    print(f"extractos que NO cierran (aritmética):     {ext_no_cierra}")
    print("=" * 100)


if __name__ == "__main__":
    main()
