"""diag_espejo_assets — POR QUÉ un bono sale como «sin espejo en assets».

    python -m scripts.diag_espejo_assets            # los que hoy disparan
    python -m scripts.diag_espejo_assets PLC5O S13N6

Read-only. Cero escrituras, cero llamadas a 1816.

⚠️ **PARA QUÉ EXISTE.** El user, mirando PLC5O y S13N6 marcados: *«el problema
es que no está en tenencias… ¿qué tiene que ver la paridad y la valuación? Si es
solamente meter un asset en un lugar donde hoy no figura. Además LO VEO EN
TENENCIAS (AuM), los dos están. Claramente está bugueada esta feature porque los
bonos SÍ están»*.

Tiene razón en que hay algo que no cierra, y este diag decide cuál de las tres
cosas es — que se arreglan distinto y hoy se ven iguales:

    A. no hay fila en `portafolio.assets`          → falta el alta del título
    B. la fila existe pero `ticker` está VACÍO     → falta UN campo, no la fila
    C. la fila existe con OTRO `ticker`            → se escriben distinto y el
                                                     join no los encuentra

La regla del detector es literalmente esta (`av_agent.detectar_tasas_sospechosas`):

    tc NOT IN en_assets   AND   tc IN en_cartera

donde `tc` = `mercado.curvas.ticker_corto`, `en_assets` = los `ticker` DISTINCT
no vacíos de `portafolio.assets`, y `en_cartera` = los códigos sacados de la
UNIDAD de `portafolio.tenencia` del último día con `aum='si'`.

O sea: **el detector NO mira si existe la fila de assets — mira la COLUMNA
`ticker` de esa fila.** B y C disparan igual que A, y el texto del hallazgo dice
«no está en portafolio.assets», que en esos dos casos es falso. Eso solo se
puede distinguir mirando la base.
"""
from __future__ import annotations

import sys

from core.postgres import get_pool


def _fila(cur, sql: str, params: tuple) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def mirar(tc: str, cur) -> None:
    tc = tc.strip().upper()
    print(f"\n{'=' * 72}\n{tc}\n{'=' * 72}")

    # 1. La curva
    curva = _fila(cur, "SELECT ticker, instrumento, curva FROM mercado.curvas "
                       "WHERE upper(btrim(ticker)) = %s", (tc,))
    print(f"  mercado.curvas          : {curva[0] if curva else 'NO ESTÁ'}")

    # 2. ¿Está en la tenencia? (lo que el user ve en AuM)
    ten = _fila(cur,
                "SELECT unidad, count(*), max(fecha)::text FROM portafolio.tenencia "
                "WHERE aum = 'si' AND upper(unidad) LIKE %s GROUP BY unidad",
                (f"%{tc}%",))
    print(f"  portafolio.tenencia     : {len(ten)} unidad/es con aum='si'")
    for u, n, f in ten:
        print(f"      · {u!r}  ({n} filas, última {f})")

    # 3. ¿Entra al conjunto `en_cartera` del detector? (último día de AuM)
    en_cart = _fila(cur,
                    "SELECT unidad FROM portafolio.tenencia WHERE aum = 'si' "
                    "  AND fecha = (SELECT max(fecha) FROM portafolio.tenencia "
                    "               WHERE aum = 'si') "
                    "  AND upper(unidad) LIKE %s", (f"%{tc}%",))
    print(f"  → en_cartera (hoy)      : {'SÍ' if en_cart else 'NO'}"
          + ("" if en_cart else "   ← si es NO, el hallazgo NO debería existir"))

    # 4. La fila de assets — por TICKER (lo que mira el detector)
    por_tk = _fila(cur, "SELECT unidad, ticker, cartera, emisor, vigente "
                        "FROM portafolio.assets WHERE upper(btrim(ticker)) = %s",
                   (tc,))
    print(f"  assets POR TICKER       : {len(por_tk)} fila/s   ← ESTO es lo que "
          f"decide el hallazgo")
    for r in por_tk:
        print(f"      · {r}")

    # 5. La fila de assets — por UNIDAD (existe aunque el ticker esté mal)
    por_un = _fila(cur, "SELECT unidad, ticker, cartera, emisor, vigente "
                        "FROM portafolio.assets WHERE upper(unidad) LIKE %s",
                   (f"%{tc}%",))
    print(f"  assets POR UNIDAD       : {len(por_un)} fila/s")
    for r in por_un:
        print(f"      · unidad={r[0]!r} ticker={r[1]!r} cartera={r[2]!r} "
              f"emisor={r[3]!r} vigente={r[4]}")

    # ── EL VEREDICTO ────────────────────────────────────────────────────────
    if not en_cart:
        print("  ⇒ FALSO POSITIVO: no está en la cartera de hoy.")
    elif por_tk:
        print("  ⇒ FALSO POSITIVO: hay fila de assets con ese ticker exacto. "
              "El hallazgo quedó viejo (se arregló después de la corrida).")
    elif por_un:
        vacios = [r for r in por_un if not (r[1] or "").strip()]
        if vacios:
            print("  ⇒ CASO B — la fila EXISTE y le falta el `ticker`. "
                  "NO hay que dar de alta nada: hay que completar UN campo.")
        else:
            print("  ⇒ CASO C — la fila existe con OTRO ticker "
                  f"({[r[1] for r in por_un]}). Se escriben distinto y el join "
                  "no los encuentra.")
    else:
        print("  ⇒ CASO A — no hay ninguna fila de assets. Falta el alta.")


def main() -> None:
    pedidos = [a for a in sys.argv[1:] if not a.startswith("-")]
    with get_pool().connection() as conn, conn.cursor() as cur:
        if not pedidos:
            # Los que HOY dispararían la regla, con el mismo predicado exacto.
            cur.execute(
                "SELECT DISTINCT upper(btrim(c.ticker)) FROM mercado.curvas c "
                "WHERE upper(btrim(c.ticker)) NOT IN ("
                "        SELECT upper(btrim(ticker)) FROM portafolio.assets "
                "        WHERE ticker IS NOT NULL AND ticker <> '') "
                "ORDER BY 1")
            candidatos = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute(
                "SELECT DISTINCT unidad FROM portafolio.tenencia WHERE aum = 'si' "
                "  AND fecha = (SELECT max(fecha) FROM portafolio.tenencia "
                "               WHERE aum = 'si')")
            from api.services.acreencias import codigo_de_unidad
            cartera = {codigo_de_unidad(r[0]) for r in cur.fetchall() if r[0]}
            pedidos = sorted(set(candidatos) & cartera)
            print(f"Disparan la regla HOY: {len(pedidos)} → {pedidos}")
            if not pedidos:
                print("Ninguno. Si la pantalla muestra alguno, su hallazgo es "
                      "de una corrida vieja y no se recontroló.")
                return
        for tc in pedidos:
            mirar(tc, cur)

    print("\nA = falta la fila · B = falta el campo `ticker` · C = ticker distinto")


if __name__ == "__main__":
    main()
