"""scripts/diag_interbanking_incoherentes.py — por qué 9 días no cuadran.

READ-ONLY: solo `SELECT` sobre `bancos.*`. No toca Interbanking ni escribe nada.

## Qué pregunta responde

La corrida del 2026-08-18 reportó `dias_incoherentes = 9` sobre 70 días: días
donde la cantidad de movimientos que guardamos NO coincide con el
`total_movements` que declara el propio extracto del banco para ese día.

Eso lo detecta el auto-chequeo de la ingesta, pero el contador solo dice CUÁNTOS.
No dice cuáles, ni de qué lado está la diferencia — y el SIGNO es justamente lo
que discrimina la causa, porque las dos hipótesis dan contadores idénticos:

  · guardado < declarado  → **estamos PERDIENDO movimientos**. La causa esperable
    es una COLISIÓN DE HASH: dos movimientos distintos del mismo día que comparten
    todos los campos del hash (extracto, correlativo, importe, tipo, código,
    comprobante, fecha) colapsan en una sola fila. Es el riesgo conocido de no
    tener id natural. Es el caso GRAVE.

  · guardado > declarado  → tenemos DE MÁS. Causa esperable: el banco devolvió el
    mismo movimiento con algún campo cambiado (una corrección) y el hash, que
    incluye importe y código a propósito, generó una fila NUEVA en vez de pisar la
    vieja. Es el comportamiento BUSCADO ("duplicar es visible; perder no"), no un
    bug — pero hay que verlo para confirmarlo.

  · declarado NULL → el extracto de ese día no trajo `total_movements`. No falta
    nada: falta el número contra el cual comparar.

## Qué NO significa

**Ningún saldo depende de esto.** Los saldos de la vista (apertura, cierre,
créditos, débitos) salen de `extracto_dia`, que son los totales que informa el
banco — no se suman desde el detalle. Una incoherencia acá afecta al DETALLE de
movimientos, no a la plata que muestra el consolidado.

Uso:
    python -m scripts.diag_interbanking_incoherentes
    python -m scripts.diag_interbanking_incoherentes --desde 2026-08-01
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

from core.postgres import get_pool

SEP = "=" * 78


def _q(sql: str, params: tuple) -> list[dict]:
    from psycopg.rows import dict_row
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser(description="Días de Interbanking que no cuadran")
    ap.add_argument("--desde", type=date.fromisoformat,
                    default=date.today() - timedelta(days=30))
    args = ap.parse_args()

    print(f"\n{SEP}\nDÍAS INCOHERENTES desde {args.desde}\n{SEP}")

    filas = _q(
        """SELECT c.bank_name, c.currency, c.account_type, right(c.account_number, 4) AS ref,
                  e.fecha, e.total_movimientos AS declarado, e.numero_extracto,
                  (SELECT count(*) FROM bancos.movimientos m
                    WHERE m.cuenta_id = e.cuenta_id AND m.fecha = e.fecha) AS guardado,
                  e.cierra, e.diferencia
             FROM bancos.extracto_dia e
             JOIN bancos.cuentas c ON c.id = e.cuenta_id
            WHERE e.fecha >= %s
              AND e.total_movimientos IS DISTINCT FROM
                  (SELECT count(*) FROM bancos.movimientos m
                    WHERE m.cuenta_id = e.cuenta_id AND m.fecha = e.fecha)
            ORDER BY e.fecha, c.bank_name""",
        (args.desde,),
    )

    if not filas:
        print("  Ninguno. Todos los días cuadran con lo que declara el banco.")
    else:
        print(f"\n  {'FECHA':<12}{'BANCO':<18}{'CTA':<8}{'DECL':>6}{'GUAR':>6}{'DIF':>6}  QUÉ ES")
        print("  " + "-" * 74)
        faltan = sobran = sin_dato = 0
        for r in filas:
            dec, gua = r["declarado"], r["guardado"]
            if dec is None:
                que, dif, sin_dato = "el extracto no declaró total", "—", sin_dato + 1
            elif gua < dec:
                que, dif, faltan = "FALTAN (posible colisión de hash)", gua - dec, faltan + 1
            else:
                que, dif, sobran = "sobran (corrección del banco)", gua - dec, sobran + 1
            cta = f"{r['account_type']}/{r['currency']} …{r['ref']}"
            print(f"  {r['fecha']!s:<12}{(r['bank_name'] or '')[:17]:<18}{cta:<8}"
                  f"{dec if dec is not None else '—':>6}{gua:>6}{dif:>6}  {que}")

        print(f"\n  RESUMEN: {faltan} con faltantes · {sobran} con sobrantes · "
              f"{sin_dato} sin total declarado")
        if faltan:
            print("  ⚠️  Los faltantes son el caso grave: hay movimientos del banco que "
                  "NO están en la base.")

    # Evidencia directa de la hipótesis de colisión: movimientos del banco que
    # comparten TODOS los campos del hash. Si esto devuelve filas, la colisión no
    # es una hipótesis — es lo que está pasando.
    print(f"\n{SEP}\nCANDIDATOS A COLISIÓN (mismo día y mismos campos identificatorios)\n{SEP}")
    dup = _q(
        """SELECT c.bank_name, m.fecha, m.numero_extracto, m.correlativo,
                  m.importe, m.tipo, m.codigo_operacion_ib, count(*) AS n
             FROM bancos.movimientos m
             JOIN bancos.cuentas c ON c.id = m.cuenta_id
            WHERE m.fecha >= %s
            GROUP BY 1,2,3,4,5,6,7
           HAVING count(*) > 1
            ORDER BY count(*) DESC
            LIMIT 20""",
        (args.desde,),
    )
    if not dup:
        print("  Ninguno guardado. OJO: esto NO descarta la colisión — si dos "
              "movimientos colapsaron en el hash, quedó UNA sola fila y acá no se ve.\n"
              "  Lo que la delata es la columna FALTAN de la tabla de arriba.")
    else:
        for r in dup:
            print(f"  {r['fecha']} {(r['bank_name'] or '')[:16]:<17} ext={r['numero_extracto']} "
                  f"corr={r['correlativo']} ${r['importe']} {r['tipo']} → {r['n']} filas")

    print(f"\n{SEP}\nÚLTIMAS CORRIDAS DEL JOB\n{SEP}")
    for r in _q(
        """SELECT corrida_at, count(*) AS cuentas,
                  sum(movimientos) AS movs, sum(incoherentes) AS incoh,
                  count(*) FILTER (WHERE NOT ok) AS con_error
             FROM bancos.sync_log
            WHERE corrida_at >= now() - interval '3 days'
            GROUP BY date_trunc('minute', corrida_at), corrida_at
            ORDER BY corrida_at DESC LIMIT 10""", (),
    ):
        print(f"  {r['corrida_at']:%Y-%m-%d %H:%M}  cuentas={r['cuentas']:<4} "
              f"movs={r['movs']:<6} incoherentes={r['incoh']:<4} errores={r['con_error']}")
    print()


if __name__ == "__main__":
    main()
