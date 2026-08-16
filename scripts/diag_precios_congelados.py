"""scripts/diag_precios_congelados.py — ¿el PnL está valuando con precios viejos? READ-ONLY.

`mercado.snapshots_cierre` es el FALLBACK DE PRECIO del motor de PnL: cuando no
hay precio live, la valuación usa la última fila de acá. Y esa tabla tiene dos
propiedades que juntas son peligrosas:

  · se puebla barriendo `jobs/snapshot_cierre.py::CURVAS_V1`, que son **3 de las
    7 familias** de curva (medido: barre 54 de 221 bonos);
  · **nunca borra**. Su upsert es `ON CONFLICT ... WHERE EXCLUDED.fecha >= fecha`,
    o sea que solo AVANZA. Un ticker que deja de entrar al barrido no da error:
    se queda con el último precio que tuvo, para siempre.

Resultado: hay tickers con precio de hace meses. Y SALUD no lo ve, porque su
contrato de frescura mira `max(fecha)` de la tabla — que sigue fresco mientras
cualquier otro ticker actualice.

**Un precio viejo no es automáticamente un error.** Si el bono VENCIÓ, que su
última cotización sea de abril es exactamente lo correcto. El problema son los
VIVOS. Este diag separa esos dos casos, que es justo lo que no se puede saber
mirando la tabla sola, y agrega lo único que convierte el problema en plata:
**si alguien lo tiene en cartera**.

Tres bloques:

  1) LOS CONGELADOS — cada ticker atrasado, con su vencimiento y su marca de
     vigencia. Vencido = esperado. Vigente = hay que mirarlo.
  2) ¿HAY POSICIÓN? — de los vigentes, cuáles están en la tenencia de hoy. Sin
     posición el precio viejo es feo; con posición, es una valuación mal hecha.
  3) CUÁNTO SE DESVÍA — el precio congelado contra el que hay hoy en
     `market_snapshot`. Es el número que dice si esto importa o es anecdótico.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_precios_congelados
"""
from __future__ import annotations

from core.postgres import get_pool

_SEP = "=" * 100


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def _v(x) -> str:
    return "—" if x is None or str(x).strip() == "" else str(x)


# El join va del símbolo de mercado (que es la PK de snapshots_cierre) al master y
# de ahí al catálogo: `curvas.instrumento` ES el símbolo, `curvas.ticker` el bono.
_BASE = """
    SELECT s.ticker AS simbolo, s.last_price, s.fecha::text AS fecha_cierre,
           c.ticker AS bono, c.fecha_vencimiento::text AS vto,
           c.curva, c.ajuste, c.emisor_tipo,
           bool_or(a.vigente IS NOT false) AS alguna_vigente,
           (max(s.fecha) OVER ()) - s.fecha AS dias_atras
    FROM mercado.snapshots_cierre s
    LEFT JOIN mercado.curvas c ON c.instrumento = s.ticker
    LEFT JOIN portafolio.assets a ON upper(a.ticker) = upper(c.ticker)
    GROUP BY s.ticker, s.last_price, s.fecha, c.ticker, c.fecha_vencimiento,
             c.curva, c.ajuste, c.emisor_tipo
"""


def congelados() -> list[dict]:
    filas = _q(f"WITH b AS ({_BASE}) SELECT * FROM b "
               f"WHERE dias_atras > 0 ORDER BY fecha_cierre, simbolo")
    print(f"\n{_SEP}\n1) LOS CONGELADOS — precio anterior al último cierre de la tabla\n{_SEP}")
    if not filas:
        print("  ✅ ninguno: todos los tickers tienen el precio del mismo cierre.")
        return []
    print("  Un precio viejo NO es automáticamente un error: si el bono venció, que")
    print("  su última cotización sea de hace meses es lo correcto. Lo que importa")
    print("  es la columna ¿VIVO?.\n")
    print(f"  {'BONO':<10}{'CIERRE':<12}{'DÍAS':>5}  {'VENCE':<12}{'¿VIVO?':<9}"
          f"{'CURVA':<14}{'LAST':>14}")
    print("  " + "-" * 92)
    for f in filas:
        # Vive si NO venció. `vigente` de assets es la marca humana/job; el
        # vencimiento del master es el hecho. Si no hay ninguno de los dos, no se
        # afirma nada: '?' y que lo mire un humano.
        if f["vto"]:
            vivo = "SÍ" if f["vto"] >= f["fecha_cierre"] else "no (venció)"
        elif f["alguna_vigente"] is not None:
            vivo = "SÍ" if f["alguna_vigente"] else "no (baja)"
        else:
            vivo = "?"
        print(f"  {_v(f['bono'])[:9]:<10}{f['fecha_cierre']:<12}{f['dias_atras']:>5}  "
              f"{_v(f['vto']):<12}{vivo:<9}{_v(f['curva'])[:13]:<14}"
              f"{_v(f['last_price']):>14}")
        f["_vivo"] = vivo
    vivos = [f for f in filas if f["_vivo"] == "SÍ"]
    print("  " + "-" * 92)
    print(f"  {len(filas)} congelado(s) · {len(vivos)} VIVOS (esos son el problema) · "
          f"{len(filas) - len(vivos)} vencidos o de baja (esperado)")
    return vivos


def con_posicion(vivos: list[dict]) -> None:
    print(f"\n{_SEP}\n2) ¿HAY POSICIÓN? — sin tenencia es feo; con tenencia es plata\n{_SEP}")
    if not vivos:
        print("  (no hay congelados vivos)")
        return
    bonos = [f["bono"] for f in vivos if f.get("bono")]
    if not bonos:
        print("  (los congelados vivos no matchean con el master — no se puede cruzar)")
        return
    filas = _q("""
        WITH ult AS (SELECT max(fecha) AS f FROM portafolio.tenencia WHERE aum = 'si')
        SELECT a.ticker AS bono, count(DISTINCT t.id_cuenta) AS cuentas,
               sum(COALESCE(t.cantidad, 0)) AS nominales
        FROM portafolio.tenencia t
        JOIN ult ON t.fecha = ult.f
        JOIN portafolio.assets a ON a.unidad = t.unidad
        WHERE t.aum = 'si' AND COALESCE(t.cantidad, 0) <> 0
          AND upper(a.ticker) = ANY(%s)
        GROUP BY a.ticker ORDER BY 3 DESC
    """, ([b.upper() for b in bonos],))
    if not filas:
        print("  ✅ ninguno de los congelados vivos está en la tenencia de hoy.")
        print("     El precio viejo no está valuando nada — es deuda, no un error de PnL.")
        return
    print(f"  ⚠ {len(filas)} bono(s) congelados Y en cartera. Acá el precio viejo SÍ")
    print("    está entrando a una valuación:\n")
    print(f"  {'BONO':<12}{'CUENTAS':>9}{'NOMINALES':>18}")
    print("  " + "-" * 42)
    for f in filas:
        print(f"  {_v(f['bono'])[:11]:<12}{f['cuentas']:>9}{_v(f['nominales']):>18}")


def desvio(vivos: list[dict]) -> None:
    print(f"\n{_SEP}\n3) CUÁNTO SE DESVÍA del precio de hoy\n{_SEP}")
    if not vivos:
        print("  (no hay congelados vivos)")
        return
    simbolos = [f["simbolo"] for f in vivos]
    hoy = {r["ticker"]: r["last_price"] for r in _q(
        "SELECT ticker, last_price FROM mercado.market_snapshot "
        "WHERE ticker = ANY(%s) AND last_price IS NOT NULL", (simbolos,))}
    if not hoy:
        print("  (el snapshot live no tiene precio para ninguno — fuera de rueda,")
        print("   o el motor no los suscribe. Volver a correr en rueda.)")
        return
    print(f"  {'BONO':<12}{'CONGELADO':>14}{'HOY':>14}{'DESVÍO':>12}")
    print("  " + "-" * 54)
    for f in vivos:
        h = hoy.get(f["simbolo"])
        if h is None or not f["last_price"]:
            continue
        try:
            d = (float(h) / float(f["last_price"]) - 1) * 100
        except (TypeError, ZeroDivisionError, ValueError):
            continue
        print(f"  {_v(f['bono'])[:11]:<12}{float(f['last_price']):>14,.2f}"
              f"{float(h):>14,.2f}{d:>11,.1f}%")
    print("  " + "-" * 54)
    print("  Un desvío grande y CONSTANTE entre varios bonos no es mercado: es una")
    print("  conversión de moneda (ya pasó — un factor fijo de ~1517 era el MEP).")


def main() -> None:
    print(_SEP)
    print("PRECIOS CONGELADOS — ¿el fallback de precio del PnL está valuando con datos viejos?")
    print(_SEP)
    vivos = congelados()
    con_posicion(vivos)
    desvio(vivos)
    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
