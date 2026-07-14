"""diag_dolar_ytd.py — ¿por qué CCL y CANJE no tienen %YTD en la watchlist?

READ-ONLY. El ancla YTD de /api/argy busca el último valor NO NULO con fecha
<= 1/ene en `valuaciones.dolar` (por campo). Si un campo empezó a escribirse
después del 1/ene, no hay ancla → "—". Este diag mide, por campo (mep/ccl/canje):

  - primera y última fecha con dato,
  - cuántos días con dato hay,
  - si existe (y cuál es) el valor asof 1/ene (el ancla YTD),
  - ídem asof 1° del mes (ancla MTD, para descartar de paso).

Correr:  python -m scripts.diag_dolar_ytd
"""
from __future__ import annotations

from datetime import date

from core.postgres import get_pool

CAMPOS = ("mep", "ccl", "canje")


def main() -> None:
    hoy = date.today()
    ytd = hoy.replace(month=1, day=1)
    mtd = hoy.replace(day=1)

    print(f"\nHoy: {hoy} · ancla YTD = último dato <= {ytd} · ancla MTD = <= {mtd}\n")
    print(f"{'CAMPO':<8}{'PRIMER DATO':>14}{'ÚLTIMO DATO':>14}{'DÍAS':>7}"
          f"{'ANCLA YTD':>16}{'ANCLA MTD':>16}")

    with get_pool().connection() as conn, conn.cursor() as cur:
        for campo in CAMPOS:
            cur.execute(
                f"""
                SELECT min(timestamp)::date,
                       max(timestamp)::date,
                       count(DISTINCT timestamp::date)
                FROM dolar WHERE {campo} IS NOT NULL
                """
            )
            primero, ultimo, dias = cur.fetchone()

            def _asof(limite: date, c: str = campo) -> str:
                cur.execute(
                    f"SELECT {c}, timestamp::date FROM dolar "
                    f"WHERE {c} IS NOT NULL AND timestamp < %s "
                    f"ORDER BY timestamp DESC LIMIT 1",
                    (limite,),
                )
                r = cur.fetchone()
                return f"{float(r[0]):,.2f} ({r[1]})" if r else "SIN DATO ←"

            print(f"{campo:<8}{primero!s:>14}{ultimo!s:>14}{dias:>7}"
                  f"{_asof(ytd):>16}{_asof(mtd):>16}")

    print(
        "\nLECTURA:\n"
        "  · 'SIN DATO' en ANCLA YTD para ccl/canje = la serie arrancó después del\n"
        "    1/ene → el %YTD no puede calcularse (no es un bug del cálculo, falta\n"
        "    histórico). Opciones: backfill del CCL histórico o aceptar que el YTD\n"
        "    aparece solo el año que viene.\n"
        "  · Si el ancla YTD SÍ está para todos, el problema es del cálculo → avisar.\n"
    )


if __name__ == "__main__":
    main()
