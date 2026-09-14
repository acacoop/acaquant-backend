"""¿Las cuentas que aparecen en MOVIMIENTOS tienen tenencia en la Caja?

La pregunta nace de la pantalla: en ENTREGA / RECIBE aparecen cuentas con forma
rara (222222222, 888888888, 10000, 50000, 555555555) que NO figuran en la tab
TENENCIAS. Hay tres explicaciones posibles y este diag las separa con datos:

  (a) son cuentas NUESTRAS sin tenencia hoy       → aparecen en clientes.cuentas
  (b) son cuentas de OTRO participante            → su account_number no arranca
                                                    con nuestro participantCode
  (c) son cuentas técnicas de CVSA                → ni una cosa ni la otra

⚠️ Es DECISIVO mirar `account_number` CRUDO y no `id_cuenta`: `id_cuenta` tira el
código de participante (`"6/222222222"` y `"74/222222222"` colapsan al mismo
`"222222222"`), así que dos cuentas de agentes distintos se ven idénticas. Ese
es el modo de falla de la REGLA #9 — no falla nada, contesta segura la cuenta
equivocada.

READ-ONLY: no escribe ni una fila. Uso:

    python -m scripts.diag_custodia_cuentas
"""
from __future__ import annotations

from core import custodia_cuentas as cuentas
from core.postgres import get_pool

SQL = """
WITH mov AS (
    SELECT DISTINCT id_cuenta, account_number,
           participante
      FROM portafolio.custodia_movimientos
     WHERE fecha_liq = (SELECT max(fecha_liq) FROM portafolio.custodia_movimientos)
)
SELECT m.participante,
       m.account_number,
       m.id_cuenta,
       (c.id_cuenta IS NOT NULL)                       AS es_cuenta_nuestra,
       c.denominacion,
       COALESCE(h.filas, 0)                            AS filas_en_holdings,
       h.nominales
  FROM mov m
  -- ⚠️ Solo el espacio 74 se cruza con comitentes: joinear una liquidadora o
  -- una de garantías por el número pelado devuelve un cliente que no es.
  LEFT JOIN clientes.cuentas c ON c.id_cuenta = m.id_cuenta
                              AND m.participante = '74'
  LEFT JOIN LATERAL (
       SELECT count(*) AS filas, sum(cantidad) AS nominales
         FROM portafolio.custodia_cvsa k
        WHERE k.participante = m.participante
          AND k.id_cuenta = m.id_cuenta
          AND k.fecha = (SELECT max(fecha) FROM portafolio.custodia_cvsa)
  ) h ON true
 ORDER BY m.participante, m.id_cuenta
"""


def main() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha_liq) FROM portafolio.custodia_movimientos")
        fecha_mov = (cur.fetchone() or [None])[0]
        cur.execute("SELECT max(fecha) FROM portafolio.custodia_cvsa")
        fecha_hold = (cur.fetchone() or [None])[0]
        cur.execute(SQL)
        filas = cur.fetchall()

    print(f"movimientos del {fecha_mov} · holdings del {fecha_hold}")
    print(f"{len(filas)} cuentas distintas en los movimientos\n")

    # Lo primero que hay que ver: ¿hay MÁS DE UN participante? Si lo hay,
    # `id_cuenta` solo no identifica nada y la pantalla está mostrando cuentas
    # de agentes distintos como si fueran del mismo espacio de numeración.
    participantes: dict[str, int] = {}
    for p, *_ in filas:
        participantes[p] = participantes.get(p, 0) + 1
    print("POR PARTICIPANTE:", ", ".join(f"{p}: {n} cuentas"
                                         for p, n in sorted(participantes.items())))
    if len(participantes) > 1:
        print("  ⚠️  HAY MÁS DE UN PARTICIPANTE. `id_cuenta` NO alcanza para "
              "identificar la cuenta: dos agentes pueden usar el mismo número.\n")
    else:
        print("  todas del mismo participante: `id_cuenta` alcanza.\n")

    print(f"{'ESPACIO':>13} {'ACCOUNT_NUMBER':>18} {'NUESTRA':>8} {'HOLDINGS':>9} "
          f"{'NOMINALES':>16}  DENOMINACIÓN")
    for part, acc, _idc, nuestra, deno, n_hold, nominales in filas:
        # El nombre sale del catálogo declarado cuando no es un comitente.
        nombre = deno or cuentas.denominacion(acc) or ""
        print(f"{cuentas.espacio(part) or '⚠ DESCONOCIDO':>13} {acc:>18} "
              f"{'sí' if nuestra else 'NO':>8} {n_hold:>9} "
              f"{(f'{nominales:,.2f}' if nominales is not None else '—'):>16}  {nombre}")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT participante, count(*), count(DISTINCT id_cuenta) "
                    "  FROM portafolio.custodia_cvsa "
                    " WHERE fecha = (SELECT max(fecha) FROM portafolio.custodia_cvsa) "
                    " GROUP BY participante ORDER BY participante")
        print("\nTENENCIAS por espacio (esto dice si el feed está trayendo los tres):")
        for part, n, ctas in cur.fetchall():
            print(f"  {part or '(vacío)':>8} {cuentas.espacio(part) or '⚠ DESCONOCIDO':>13} "
                  f"{n:>7} filas  {ctas:>5} cuentas")

    sin_hold = [f for f in filas if f[5] == 0]
    ajenas = [f for f in filas if not f[3]]
    print(f"\n{len(sin_hold)} cuentas operaron y NO tienen tenencia en la Caja.")
    print(f"{len(ajenas)} cuentas NO están en clientes.cuentas.")


if __name__ == "__main__":
    main()
