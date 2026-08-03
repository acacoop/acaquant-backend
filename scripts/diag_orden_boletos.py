"""Detecta fechas mal cargadas usando el ORDEN de los boletos BOL (read-only).

Los boletos se emiten correlativos: un boleto MÁS GRANDE nunca puede tener una
fecha ANTERIOR a la de uno más chico. Cualquier inversión = fecha corrupta.
No necesita archivo de referencia: la propia numeración delata el error.

    python -m scripts.diag_orden_boletos                 # desde 2026-05-01
    python -m scripts.diag_orden_boletos --desde 2026-01-01
    python -m scripts.diag_orden_boletos --detalle       # lista todas

NO ESCRIBE NADA.
"""
import argparse
import re

from core.postgres import get_pool

_NUM = re.compile(r"^BOL\s*(\d+)$")


def _traer(desde: str) -> list[tuple[str, object]]:
    """Boletos BOL del rango de numeración que ocupa la ventana, SIN filtrar fecha.

    Primero busca el boleto mínimo y máximo con concertacion >= `desde`, y después
    trae todo ese rango. Un filtro por fecha escondería justamente el error que
    buscamos: un boleto de la ventana que quedó con una fecha vieja.
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT min(boleto), max(boleto) FROM operaciones.operaciones "
            "WHERE concertacion >= %s AND boleto LIKE 'BOL %%'",
            (desde,))
        lo, hi = cur.fetchone()
        if not lo:
            return []
        cur.execute(
            "SELECT boleto, concertacion FROM operaciones.operaciones "
            "WHERE boleto LIKE 'BOL %%' AND boleto >= %s AND boleto <= %s "
            "  AND concertacion IS NOT NULL ORDER BY boleto",
            (lo, hi))
        return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2026-05-01")
    ap.add_argument("--detalle", action="store_true")
    args = ap.parse_args()

    items = []
    for boleto, fecha in _traer(args.desde):
        m = _NUM.match((boleto or "").strip())
        if m:
            items.append((int(m.group(1)), boleto, fecha))
    if not items:
        print(f"No hay boletos BOL con concertacion >= {args.desde}.")
        return
    items.sort()
    n = len(items)

    # Inversión = fecha menor que el máximo de las fechas de los boletos MÁS
    # CHICOS (o mayor que el mínimo de las de los MÁS GRANDES). O(n).
    pre_max: list = []
    mejor = None
    for _num, boleto, fecha in items:
        pre_max.append(mejor)
        if mejor is None or fecha > mejor[0]:
            mejor = (fecha, boleto)
    suf_min: list = [None] * n
    peor = None
    for i in range(n - 1, -1, -1):
        suf_min[i] = peor
        f, b = items[i][2], items[i][1]
        if peor is None or f < peor[0]:
            peor = (f, b)

    malos = []
    for i, (_num, boleto, fecha) in enumerate(items):
        antes, despues = pre_max[i], suf_min[i]
        if antes and fecha < antes[0]:
            malos.append((boleto, fecha, "anterior a", antes[1], antes[0]))
        elif despues and fecha > despues[0]:
            malos.append((boleto, fecha, "posterior a", despues[1], despues[0]))

    print(f"\nBOL desde {args.desde} · {items[0][1]} … {items[-1][1]} · {n} boletos")
    if not malos:
        print("✓ ORDEN CORRECTO — ninguna fecha contradice la numeración.\n")
        return

    print(f"✗ FUERA DE ORDEN: {len(malos)} de {n}")
    print(f"  fechas afectadas: {', '.join(sorted({str(m[1]) for m in malos}))}\n")
    print("  BOLETO             SU FECHA     PERO ES        CHOCA CON       FECHA")
    for boleto, fecha, rel, otro, ofecha in (malos if args.detalle else malos[:20]):
        print(f"  {boleto:<18} {fecha!s:<12} {rel:<14} {otro:<15} {ofecha}")
    if not args.detalle and len(malos) > 20:
        print(f"  … y {len(malos) - 20} más (corré con --detalle)")
    print()


if __name__ == "__main__":
    main()
