"""scripts/diag_potencial_clientes.py — ¿hay datos para medir POTENCIAL de cliente?

LA PREGUNTA QUE DECIDE EL DISEÑO
================================
La vista de hoy sólo mide **flujo** (cuánto arancel dejó). Con eso NO se puede
contestar "¿a quién le puedo sacar más?": un cliente que deja $2M por mes puede
estar dejando el 0,1% de una cartera de $2.000M (pésimo) y otro que deja $200k
puede estar dejando el 4% de $5M (excelente). Mirando sólo el arancel, el
primero está arriba de la lista y el segundo no aparece.

Lo que falta es el DENOMINADOR: la plata del cliente. Tenemos dos candidatos y
**ninguno de los dos sirve si está vacío o viejo** — por eso este diag, ANTES de
construir nada (REGLA #2):

  1. AuM  (`portafolio.tenencia`)          → RENDIMIENTO: arancel / lo que tiene ACÁ
  2. CUPO (`comitentes.cupo_transaccional_ars`) → CUÁNTO NO TIENE ACÁ

El (2) es el único ancla EXTERNA que tenemos: sin algo así, "share of wallet" es
un número inventado. Pero el cupo se carga a mano por Excel y `cupo_cargado_en`
está NULL — así que hay que medir cuántas cuentas lo tienen antes de diseñar una
columna alrededor.

Contesta CINCO preguntas, cada una con su veredicto:

  A. ¿Cuántas cuentas activas tienen cupo cargado y > 0?
  B. ¿Cómo se distribuye el RENDIMIENTO (arancel 12m / AuM), en bps?
  C. ¿Cuánta plata está PARADA? (AuM > 0 y CERO arancel en 12 meses)
  D. ¿Los grupos de comparación tienen tamaño suficiente para una mediana?
  E. ¿Cuántos tipos de operación distintos usa cada cliente? (amplitud)

100% READ-ONLY: no escribe una sola fila. Tarda unos segundos.

Uso (en el Droplet):
    python -m scripts.diag_potencial_clientes
"""
from __future__ import annotations

import time

# Los predicados NO se reescriben acá: se importan de donde ya viven, para que
# el diag no pueda medir una cosa distinta de la que muestra la app.
from api.services.comercial_sql import _act_where, _arancel_where
from core.postgres import get_pool

_MESES = 12


def _pct(n: int, total: int) -> str:
    return f"{100 * n / total:5.1f}%" if total else "    —"


def _fmt(x) -> str:
    if x is None:
        return "—"
    return f"{float(x):,.0f}".replace(",", ".")


def _percentiles(cur, sql: str, etiqueta: str, sufijo: str = "") -> None:
    cur.execute(sql)
    r = cur.fetchone()
    if not r or r[0] in (None, 0):
        print(f"   {etiqueta}: sin datos")
        return
    n, p10, p25, p50, p75, p90 = r
    print(f"   {etiqueta}  (n={n})")
    print(f"      p10 {_fmt(p10)}{sufijo} · p25 {_fmt(p25)}{sufijo} · "
          f"MEDIANA {_fmt(p50)}{sufijo} · p75 {_fmt(p75)}{sufijo} · p90 {_fmt(p90)}{sufijo}")


def main() -> int:
    pool = get_pool()
    t0 = time.perf_counter()
    with pool.connection() as conn, conn.cursor() as cur:
        conn.autocommit = True

        # ── Tablas de trabajo, en memoria de la sesión ───────────────────────
        # TEMP: se van solas al cerrar la conexión. No tocan el esquema.
        cur.execute("""
            CREATE TEMP TABLE _cta AS
            SELECT id_cuenta, nivel_1, nivel_3, operador_email,
                   cupo_transaccional_ars AS cupo
            FROM clientes.comitentes
            WHERE estado = 'Activa'
        """)
        cur.execute("SELECT count(*) FROM _cta")
        total = cur.fetchone()[0]

        # AuM de la última foto disponible.
        cur.execute("""
            CREATE TEMP TABLE _aum AS
            WITH f AS (SELECT max(fecha) AS fecha FROM portafolio.tenencia WHERE aum = 'si')
            SELECT t.id_cuenta, sum(t.valuacion) AS aum
            FROM portafolio.tenencia t, f
            WHERE t.fecha = f.fecha AND t.aum = 'si'
            GROUP BY t.id_cuenta
        """)
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        foto = cur.fetchone()[0]

        # Arancel de los últimos 12 meses + amplitud de producto, en una pasada.
        cur.execute(f"""
            CREATE TEMP TABLE _ar AS
            SELECT o.id_cuenta,
                   COALESCE(SUM(CASE WHEN {_arancel_where('o')}
                                     THEN abs(o.arancel) END), 0) AS arancel,
                   count(DISTINCT o.operacion) AS tipos,
                   count(DISTINCT o.concertacion) AS dias
            FROM operaciones.operaciones o
            WHERE o.concertacion >= (current_date - interval '{_MESES} months')
              AND {_act_where('o')}
            GROUP BY o.id_cuenta
        """)
        cur.execute("CREATE INDEX ON _cta (id_cuenta)")
        cur.execute("CREATE INDEX ON _aum (id_cuenta)")
        cur.execute("CREATE INDEX ON _ar (id_cuenta)")

        print(f"\nCuentas comitentes ACTIVAS: {total}")
        print(f"Última foto de tenencia:    {foto}")

        # ── A. ¿Existe el cupo? ─────────────────────────────────────────────
        print("\n" + "=" * 74)
        print("A. EL CUPO — el único ancla EXTERNA que tenemos")
        print("=" * 74)
        cur.execute("""
            SELECT count(*) FILTER (WHERE cupo IS NOT NULL),
                   count(*) FILTER (WHERE cupo > 0)
            FROM _cta
        """)
        con_campo, con_cupo = cur.fetchone()
        print(f"   con el campo cargado : {con_campo:5d}  ({_pct(con_campo, total)})")
        print(f"   con cupo > 0         : {con_cupo:5d}  ({_pct(con_cupo, total)})")
        cur.execute("SELECT count(*), min(cupo_cargado_en), max(cupo_cargado_en) "
                    "FROM clientes.comitentes WHERE cupo_cargado_en IS NOT NULL")
        n_fecha, f_min, f_max = cur.fetchone()
        print(f"   con FECHA de carga   : {n_fecha:5d}"
              + (f"  ({f_min} → {f_max})" if n_fecha else "   ← no se sabe de cuándo es"))
        if total:
            cob = 100 * con_cupo / total
            print("   VEREDICTO: " + (
                "el cupo sirve como denominador." if cob >= 70 else
                "cobertura floja — la columna 'cuánto no tiene acá' va a estar medio vacía."
                if cob >= 30 else
                "NO alcanza para construir share of wallet. Hay que recargar el Excel primero."))

        # ── B. El rendimiento ───────────────────────────────────────────────
        print("\n" + "=" * 74)
        print("B. RENDIMIENTO — arancel de 12 meses sobre lo que el cliente TIENE acá")
        print("=" * 74)
        print("   (en bps: 100 bps = 1%. Es el 'revenue on assets' del wealth management)")
        _percentiles(cur, """
            SELECT count(*),
                   percentile_cont(0.10) WITHIN GROUP (ORDER BY bps),
                   percentile_cont(0.25) WITHIN GROUP (ORDER BY bps),
                   percentile_cont(0.50) WITHIN GROUP (ORDER BY bps),
                   percentile_cont(0.75) WITHIN GROUP (ORDER BY bps),
                   percentile_cont(0.90) WITHIN GROUP (ORDER BY bps)
            FROM (SELECT 10000 * COALESCE(a.arancel, 0) / m.aum AS bps
                  FROM _aum m LEFT JOIN _ar a ON a.id_cuenta = m.id_cuenta
                  WHERE m.aum > 0) s
        """, "rendimiento de las cuentas con AuM > 0", " bps")
        print("   Si p90/p10 es enorme, hay muchísimo margen: la mitad de abajo rinde")
        print("   una fracción de lo que rinde la de arriba TENIENDO plata igual.")

        # ── C. La plata parada ──────────────────────────────────────────────
        print("\n" + "=" * 74)
        print("C. PLATA PARADA — tiene AuM y NO dejó un peso de arancel en 12 meses")
        print("=" * 74)
        for piso in (0, 10_000_000, 100_000_000):
            cur.execute("""
                SELECT count(*), COALESCE(sum(m.aum), 0)
                FROM _aum m
                JOIN _cta c ON c.id_cuenta = m.id_cuenta
                LEFT JOIN _ar a ON a.id_cuenta = m.id_cuenta
                WHERE m.aum > %s AND COALESCE(a.arancel, 0) = 0
            """, (piso,))
            n, plata = cur.fetchone()
            print(f"   AuM > {_fmt(piso):>15}  →  {n:5d} cuentas  ·  ${_fmt(plata)} quietos")
        print("   Cada una de estas es una llamada con un motivo concreto, y hoy no")
        print("   aparece en ninguna pantalla: no operan, así que no están en ninguna lista.")

        # ── D. ¿Se puede comparar contra pares? ─────────────────────────────
        print("\n" + "=" * 74)
        print("D. GRUPOS DE COMPARACIÓN — «rinde poco PARA UN CLIENTE COMO ÉL»")
        print("=" * 74)
        cur.execute("""
            SELECT COALESCE(c.nivel_3, '(sin nivel_3)') AS grupo,
                   count(*) AS n,
                   count(*) FILTER (WHERE m.aum > 0) AS con_aum,
                   percentile_cont(0.50) WITHIN GROUP (
                       ORDER BY CASE WHEN m.aum > 0
                                THEN 10000 * COALESCE(a.arancel, 0) / m.aum END) AS bps_mediana
            FROM _cta c
            LEFT JOIN _aum m ON m.id_cuenta = c.id_cuenta
            LEFT JOIN _ar a ON a.id_cuenta = c.id_cuenta
            GROUP BY 1 ORDER BY 2 DESC
        """)
        print(f"   {'grupo (nivel_3)':<28} {'cuentas':>8} {'con AuM':>8} {'rinde (bps)':>13}")
        for grupo, n, con_aum, bps in cur.fetchall():
            flag = "" if (con_aum or 0) >= 20 else "   ← grupo chico, la mediana es frágil"
            print(f"   {grupo[:28]:<28} {n:>8} {con_aum or 0:>8} {_fmt(bps):>13}{flag}")

        # ── E. Amplitud de producto ─────────────────────────────────────────
        print("\n" + "=" * 74)
        print("E. AMPLITUD — cuántos tipos de operación distintos usa cada cliente")
        print("=" * 74)
        cur.execute(
            "SELECT count(DISTINCT operacion) FROM operaciones.operaciones o "
            f"WHERE o.concertacion >= (current_date - interval '{_MESES} months') "
            f"AND {_act_where('o')}")
        print(f"   tipos de operación que existen en la casa: {cur.fetchone()[0]}")
        cur.execute("""
            SELECT tipos, count(*) FROM _ar WHERE dias > 0
            GROUP BY 1 ORDER BY 1 LIMIT 15
        """)
        print(f"   {'usa N tipos':>12} {'cuentas':>9}")
        for tipos, n in cur.fetchall():
            print(f"   {tipos:>12} {n:>9}  {'▇' * min(40, n // 5)}")
        print("   Un cliente que usa 1 tipo teniendo plata es la venta cruzada más")
        print("   barata que hay: no hay que traerle plata, hay que mostrarle un producto.")

    print(f"\n({time.perf_counter() - t0:.1f}s · read-only, no se escribió nada)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
