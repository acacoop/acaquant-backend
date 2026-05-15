"""diag_fci_fechas.py — por qué el detalle FCI viene vacío en snapshots viejos.

El selector de fechas del frontend sale de `fci_serie()` (lee
`Valuaciones.AuMResumenFCI`, normaliza la fecha a string YYYY-MM-DD).
El detalle sale de `fci_snapshot()`, que hace un match EXACTO
`{"fecha_snapshot": <fecha_str>}` contra `Valuaciones.AuM`.

Si las fechas de AuM no coinciden con las del selector — sea por TIPO
(datetime ISODate vs string) o por VALOR (día 1 vs fin de mes, por el
cambio de régimen de cálculo) — el match devuelve [] y el detalle queda
en $0.

Este script NO modifica nada. Solo reporta el desalineo.

Corre:  python -m scripts.diag_fci_fechas
"""
from __future__ import annotations

from collections import Counter

from api.services.portfolio import _fci_assets_map, fci_serie, fci_snapshot
from core.mongo import get_mongo_client


def _tipo(v: object) -> str:
    return type(v).__name__


def main() -> None:
    db = get_mongo_client()["Valuaciones"]

    # ── 1. Unidades FCI ────────────────────────────────────────────────
    print("=" * 72)
    print("1) UNIDADES FCI (segun _fci_assets_map de portfolio.py)")
    assets_map = _fci_assets_map()
    unidades_fci = list(assets_map.keys())
    print(f"   {len(unidades_fci)} unidades FCI")
    print(f"   sample: {unidades_fci[:15]}")
    if not unidades_fci:
        print("   !! Sin unidades FCI — _fci_assets_map vacio. Cortamos.")
        return

    # ── 2. fecha_snapshot en Valuaciones.AuM (filtrado a unidades FCI) ──
    print()
    print("=" * 72)
    print("2) VALORES DISTINTOS DE fecha_snapshot EN Valuaciones.AuM (solo FCI)")
    cur = db["AuM"].find(
        {"unidad": {"$in": unidades_fci}},
        {"_id": 0, "fecha_snapshot": 1},
    )
    tipos: Counter = Counter()
    por_valor: Counter = Counter()
    for d in cur:
        fs = d.get("fecha_snapshot")
        tipos[_tipo(fs)] += 1
        # clave normalizada para agrupar (str de los primeros 10 chars)
        clave = fs.strftime("%Y-%m-%d") if hasattr(fs, "strftime") else str(fs)[:10]
        por_valor[clave] += 1
    print(f"   tipos de dato encontrados: {dict(tipos)}")
    print(f"   {len(por_valor)} fechas distintas en AuM (FCI). Detalle:")
    for fecha, n in sorted(por_valor.items()):
        print(f"      {fecha!r:16}  {n:6d} docs")

    # ── 3. fechas que el selector ofrece (fci_serie -> AuMResumenFCI) ──
    print()
    print("=" * 72)
    print("3) FECHAS QUE OFRECE EL SELECTOR (fci_serie -> AuMResumenFCI)")
    serie = fci_serie(desde=None, hasta=None, cuenta_filter="todas")
    fechas_selector = [r.get("fecha") for r in serie]
    print(f"   {len(fechas_selector)} fechas en la serie:")
    for r in serie:
        print(f"      fecha={r.get('fecha')!r:16}  total={r.get('total'):,.0f}")

    # ── 4. raw fecha_snapshot en AuMResumenFCI (tipo de dato) ──────────
    print()
    print("=" * 72)
    print("4) fecha_snapshot RAW EN AuMResumenFCI (tipo de dato)")
    tipos_resumen: Counter = Counter()
    for d in db["AuMResumenFCI"].find({}, {"_id": 0, "fecha_snapshot": 1}):
        fs = d.get("fecha_snapshot")
        tipos_resumen[_tipo(fs)] += 1
        print(f"      {fs!r}   (tipo {_tipo(fs)})")
    print(f"   tipos: {dict(tipos_resumen)}")

    # ── 5. EL TEST REAL: para cada fecha del selector, fci_snapshot() ──
    print()
    print("=" * 72)
    print("5) TEST: fci_snapshot(fecha) POR CADA FECHA DEL SELECTOR")
    print("   (filas=0 => detalle vacio => BUG en esa fecha)")
    rotas: list[str] = []
    for fecha in fechas_selector:
        if not fecha:
            continue
        filas = fci_snapshot(fecha=fecha, cuenta_filter="todas")
        total = sum(f.get("valuacion", 0) for f in filas)
        flag = ""
        if not filas:
            flag = "  <-- VACIO (bug)"
            rotas.append(fecha)
        print(f"      fecha={fecha!r:16}  filas={len(filas):4d}  total={total:,.0f}{flag}")

    # ── 6. Para una fecha rota, mostrar que SI hay en AuM ese mes ──────
    if rotas:
        print()
        print("=" * 72)
        print(f"6) DIAGNOSTICO de la primera fecha rota: {rotas[0]!r}")
        mes = rotas[0][:7]  # YYYY-MM
        print(f"   buscando en AuM (FCI) cualquier fecha_snapshot del mes {mes}...")
        encontradas: Counter = Counter()
        for d in db["AuM"].find(
            {"unidad": {"$in": unidades_fci}},
            {"_id": 0, "fecha_snapshot": 1},
        ):
            fs = d.get("fecha_snapshot")
            clave = fs.strftime("%Y-%m-%d") if hasattr(fs, "strftime") else str(fs)[:10]
            if clave.startswith(mes):
                tipo = _tipo(fs)
                encontradas[(clave, tipo)] += 1
        if encontradas:
            print("   AuM SI tiene docs FCI ese mes, con estas (fecha, tipo):")
            for (clave, tipo), n in sorted(encontradas.items()):
                print(f"      fecha={clave!r:16} tipo={tipo:10} {n:5d} docs")
            print()
            print("   >> El selector pide", repr(rotas[0]), "pero AuM tiene las de arriba.")
            print("   >> Si el tipo es 'datetime' o la fecha difiere => ese es el desalineo.")
        else:
            print(f"   AuM NO tiene NINGUN doc FCI en el mes {mes}.")
            print("   >> El snapshot viejo nunca se persistio en AuM raw (solo en el resumen).")

    print()
    print("=" * 72)
    print(f"RESUMEN: {len(rotas)}/{len(fechas_selector)} fechas del selector dan detalle vacio.")
    if rotas:
        print(f"   fechas rotas: {rotas}")


if __name__ == "__main__":
    main()
