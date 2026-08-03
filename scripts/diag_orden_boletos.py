"""Detecta fechas mal cargadas usando el ORDEN de los boletos (read-only).

Los boletos se emiten correlativos: un boleto MÁS GRANDE nunca puede tener una
fecha ANTERIOR a la de uno más chico. Cualquier inversión = fecha corrupta.

No necesita ningún archivo de referencia: la propia numeración delata el error.

    python -m scripts.diag_orden_boletos                 # desde 2026-05-01
    python -m scripts.diag_orden_boletos --desde 2026-01-01
    python -m scripts.diag_orden_boletos --detalle       # lista todas

NO ESCRIBE NADA.
"""
import argparse
import re
from collections import defaultdict

from core.postgres import get_pool

_NUM = re.compile(r"(\d+)")


def _clave(boleto: str) -> tuple[str, int] | None:
    """'BOL 2026071371' → ('BOL', 2026071371). None si no tiene número."""
    m = _NUM.search(boleto or "")
    if not m:
        return None
    return boleto[: m.start()].strip(), int(m.group(1))


def _traer(desde: str) -> list[tuple[str, object]]:
    """Boletos del rango que ocupa la ventana de fechas, sin filtrar por fecha.

    Primero busca el boleto mínimo y máximo con concertacion >= `desde`, y después
    trae TODO ese rango de numeración. Así también aparecen los boletos que
    pertenecen a la ventana pero quedaron con una fecha vieja (que es justamente
    el error que buscamos y que un filtro por fecha escondería).
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT min(boleto), max(boleto) FROM operaciones.operaciones "
            "WHERE concertacion >= %s AND boleto IS NOT NULL",
            (desde,))
        lo, hi = cur.fetchone()
        if not lo:
            return []
        cur.execute(
            "SELECT boleto, concertacion FROM operaciones.operaciones "
            "WHERE boleto >= %s AND boleto <= %s AND concertacion IS NOT NULL "
            "ORDER BY boleto",
            (lo, hi))
        return cur.fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default="2026-05-01")
    ap.add_argument("--detalle", action="store_true")
    args = ap.parse_args()

    filas = _traer(args.desde)
    if not filas:
        print(f"No hay boletos con concertacion >= {args.desde}.")
        return

    # Agrupar por prefijo: distintos mercados numeran por separado y no se comparan.
    por_prefijo: dict[str, list[tuple[int, str, object]]] = defaultdict(list)
    for boleto, fecha in filas:
        k = _clave(boleto)
        if k:
            por_prefijo[k[0]].append((k[1], boleto, fecha))

    print(f"\nVENTANA: concertacion >= {args.desde}")
    print(f"Boletos analizados: {sum(len(v) for v in por_prefijo.values())}\n")

    for prefijo, items in sorted(por_prefijo.items()):
        items.sort()
        n = len(items)
        print(f"═══ PREFIJO '{prefijo}' · {n} boletos "
              f"({items[0][1]} … {items[-1][1]}) ═══")

        # Rango de fechas por día, para ver de un vistazo si un día pisa a otro.
        rango: dict[object, list] = {}
        for num, _boleto, fecha in items:
            r = rango.setdefault(fecha, [num, num, 0])
            r[0] = min(r[0], num)
            r[1] = max(r[1], num)
            r[2] += 1

        # Una inversión existe si la fecha de un boleto es menor que el máximo de
        # las fechas de todos los boletos MÁS CHICOS (o mayor que el mínimo de las
        # de todos los MÁS GRANDES). Prefix-max / suffix-min en O(n).
        pre_max, mejor = [], None
        for _num, boleto, fecha in items:
            pre_max.append(mejor)
            if mejor is None or fecha > mejor[0]:
                mejor = (fecha, boleto)
        suf_min, peor = [None] * n, None
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

        print(f"  Boletos fuera de orden: {len(malos)} de {n}")
        print(f"  Días distintos: {len(rango)}\n")

        if malos:
            print("  BOLETO             SU FECHA     PERO ES        BOLETO CHOCA   FECHA")
            for boleto, fecha, rel, otro, ofecha in (malos if args.detalle else malos[:40]):
                print(f"  {boleto:<18} {fecha!s:<12} {rel:<14} {otro:<14} {ofecha}")
            if not args.detalle and len(malos) > 40:
                print(f"  … y {len(malos) - 40} más (corré con --detalle)")
            print()

        print("  FECHA         BOLETOS   MIN            MAX")
        for fecha in sorted(rango):
            lo, hi, cnt = rango[fecha]
            print(f"  {fecha!s:<12}  {cnt:>6}   {prefijo} {lo}   {prefijo} {hi}")
        print()


if __name__ == "__main__":
    main()
