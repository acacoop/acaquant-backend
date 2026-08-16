"""scripts/diag_motor_ejes.py — ¿el motor calcularía LO MISMO leyendo los EJES? READ-ONLY.

**El último lector grande de la columna `curva`.**

`engines/curvas.py` corre todo el día y calcula la TEA/paridad/duration de cada
bono. La fórmula NO es una sola: una Lecap se calcula distinto que un CER (hay
que ajustar por inflación) y que un hard dólar (hay que pasar por el MEP). Así
que antes de calcular, el motor se pregunta *"¿qué tipo de bono es este?"* — y
hoy contesta esa pregunta mirando **la palabra `curva`**, una string escrita a
mano por fila:

    if   curva == "tasa_fija"     → fórmula A     (línea 320)
    elif curva == "cer"           → fórmula B     (línea 380)
    elif curva == "soberanos"     → fórmula C     (línea 449)
    elif curva == "dolar_linked"  → fórmula D     (línea 519)
    elif curva.startswith("on")   → fórmula E     (línea 595)
    else                          → solo duration (línea 676)

Toda la app ya migró a los EJES (`emisor_tipo`/`moneda_eje`/`ajuste`). El motor
no. Por eso la columna todavía no se puede borrar —es lo único que le dice al
motor qué cuenta hacer— y por eso el alta de un corporativo tiene que seguir
escribiendo `on_<sector>`: escribir otra cosa lo mandaría a otra fórmula y la TEA
de ~140 bonos cambiaría **sin un solo error en pantalla**.

**Qué contesta este diag, ANTES de tocar una línea:** para los ~221 bonos, ¿la
rama que elige HOY por la palabra es la MISMA que elegiría por los ejes? Si para
todos da igual, el cambio no puede mover un número y lo sabemos de antemano
(mismo método que `sql_universo`, que reprodujo el universo con 0 diferencias).
Si hay diferencias, salen POR NOMBRE con las dos ramas al lado.

Mide DOS cosas, porque el motor consulta la curva en dos lugares distintos:

  1. **La RAMA DE CÁLCULO** — qué fórmula usa (`calcular_campos`).
  2. **La DEPENDENCIA DE FEED** — de qué dato externo depende esa cuenta
     (`curva_depende_de` / `dep_tasa_disponible`). Decide si un "no pude calcular
     la TEA" es un dato roto (hay que limpiar la tasa vieja) o un feed caído (NO
     tocar). Equivocarse acá borra tasas buenas.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_motor_ejes
"""
from __future__ import annotations

from core.postgres import get_pool

_SEP = "=" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


# ── La rama que elige HOY ────────────────────────────────────────────────────
# Espejo EXACTO del if/elif de `calcular_campos` (engines/curvas.py:320-676).
# Se escribe acá y no se importa porque el motor no expone "qué rama elegiste":
# la decisión está entrelazada con el cálculo. Si esa cadena cambia, este espejo
# queda viejo — por eso lleva los números de línea arriba.
def _rama_hoy(curva: str | None) -> str:
    c = (curva or "").strip()
    if c == "tasa_fija":
        return "tasa_fija"
    if c == "cer":
        return "cer"
    if c == "soberanos":
        return "soberanos"
    if c == "dolar_linked":
        return "dolar_linked"
    if c == "on" or c.startswith("on_"):
        return "on"
    return "solo_duration"


# ── La rama que elegiría por los EJES ────────────────────────────────────────
# LA PROPUESTA. Traduce cada rama a la pregunta equivalente sobre los ejes.
#
# ⚠️ EL ORDEN IMPORTA y no es el mismo que el de hoy. Con la palabra, un bono
# tenía UNA sola curva, así que el orden entre `soberanos` y `on` daba igual. Con
# los ejes, un corporativo en USD a tasa fija cumple LAS DOS condiciones
# (`moneda=USD + ajuste=fija` y `emisor=corporativo`). Para reproducir lo que el
# motor hace hoy, `corporativo` tiene que preguntarse PRIMERO.
def _rama_por_ejes(f: dict) -> str:
    emisor, moneda, ajuste = f.get("emisor_tipo"), f.get("moneda_eje"), f.get("ajuste")
    if not (emisor and moneda and ajuste):
        return "sin_ejes"                      # no se puede decidir: hay que clasificarlo
    if emisor == "corporativo":
        return "on"                            # la fórmula E ya despacha por moneda adentro
    if ajuste == "cer":
        return "cer"
    if ajuste == "dolar_linked":
        return "dolar_linked"
    if ajuste == "fija":
        return "soberanos" if moneda in ("USD", "EUR") else "tasa_fija"
    return "solo_duration"                     # tamar / badlar / tpm / caución


# ── La dependencia de feed ───────────────────────────────────────────────────
# Espejo de `curva_depende_de` (engines/curvas.py:710) en clave de ejes.
def _dep_por_ejes(f: dict, dep: str) -> bool:
    emisor, moneda, ajuste = f.get("emisor_tipo"), f.get("moneda_eje"), f.get("ajuste")
    if not (emisor and moneda and ajuste):
        return True                            # sin ejes → conservador (invalidar todo)
    es_on = emisor == "corporativo"
    es_hard = ajuste == "fija" and moneda in ("USD", "EUR") and not es_on
    if dep == "cer":
        return ajuste == "cer" or f.get("ajuste_alt") == "cer"
    if dep == "mep":
        return es_hard or es_on
    if dep == "a3500":
        return ajuste == "dolar_linked" or es_on
    return True


def main() -> None:
    print(_SEP)
    print("El MOTOR por EJES — ¿elegiría la misma fórmula que hoy?")
    print(_SEP)

    filas = _q("""
        SELECT ticker, curva, emisor_tipo, moneda_eje, ajuste, ajuste_alt, ley,
               emisor, moneda_flujo
        FROM mercado.curvas ORDER BY ticker
    """)

    # ── 1. La rama de cálculo ────────────────────────────────────────────────
    iguales, distintos, sin_ejes = 0, [], []
    matriz: dict[tuple[str, str], int] = {}
    for f in filas:
        h, n = _rama_hoy(f["curva"]), _rama_por_ejes(f)
        matriz[(h, n)] = matriz.get((h, n), 0) + 1
        if n == "sin_ejes":
            sin_ejes.append(f)
        elif h == n:
            iguales += 1
        else:
            distintos.append((f, h, n))

    print(f"\n  {len(filas)} bonos\n")
    print(f"  ✅ misma rama          {iguales:>4}")
    print(f"  ⚠️  rama DISTINTA       {len(distintos):>4}   ← acá cambiarían números")
    print(f"  ○  sin ejes            {len(sin_ejes):>4}   ← no se puede decidir")

    print(f"\n{_SEP}\n  MATRIZ hoy → por ejes\n{_SEP}")
    print(f"  {'RAMA HOY':<16}{'RAMA POR EJES':<18}{'N':>5}")
    print("  " + "-" * 41)
    for (h, n), c in sorted(matriz.items(), key=lambda kv: (-kv[1], kv[0])):
        marca = "  " if h == n else "⚠ "
        print(f"  {marca}{h:<14}{n:<18}{c:>5}")

    if distintos:
        print(f"\n{_SEP}\n  LOS QUE CAMBIARÍAN DE FÓRMULA — revisar UNO POR UNO\n{_SEP}")
        print(f"  {'TICKER':<10}{'CURVA (hoy)':<16}{'RAMA HOY':<15}{'RAMA EJES':<15}EJES")
        print("  " + "-" * 92)
        for f, h, n in distintos:
            ejes = f"{f['emisor_tipo']}/{f['moneda_eje']}/{f['ajuste']}"
            if f.get("ajuste_alt"):
                ejes += f"+{f['ajuste_alt']}"
            print(f"  {str(f['ticker'])[:9]:<10}{str(f['curva'] or '—')[:15]:<16}"
                  f"{h:<15}{n:<15}{ejes}")
    else:
        print("\n  ✅ NINGUNO cambia de fórmula. El motor puede migrar a los ejes sin")
        print("     mover un solo número — que es la condición para poder tocarlo.")

    if sin_ejes:
        print(f"\n  ○ SIN EJES ({len(sin_ejes)}): {', '.join(str(f['ticker']) for f in sin_ejes)}")
        print("    Hoy el motor los manda a la rama de su palabra; por ejes no se puede")
        print("    decidir. Clasificarlos ANTES de migrar (o el motor los degrada a")
        print("    'solo duration' y se quedan sin TEA).")

    # ── 2. La dependencia de feed ────────────────────────────────────────────
    print(f"\n{_SEP}\n  DEPENDENCIA DE FEED — de qué dato externo depende cada cuenta\n{_SEP}")
    print("  Decide si un 'no pude calcular la TEA' es dato roto (limpiar la tasa vieja)")
    print("  o feed caído (NO tocar). Equivocarse acá BORRA tasas buenas.\n")
    from engines.curvas import curva_depende_de

    print(f"  {'DEP':<10}{'IGUALES':>9}{'DISTINTOS':>11}")
    print("  " + "-" * 30)
    dep_distintos: list[tuple[str, str, bool, bool]] = []
    for dep in ("cer", "mep", "a3500"):
        ig = di = 0
        for f in filas:
            h = curva_depende_de(f["curva"], dep)
            n = _dep_por_ejes(f, dep)
            if h == n:
                ig += 1
            else:
                di += 1
                dep_distintos.append((dep, str(f["ticker"]), h, n))
        print(f"  {dep:<10}{ig:>9}{di:>11}")

    if dep_distintos:
        print(f"\n  Los {len(dep_distintos)} casos (dep · ticker · hoy → ejes):")
        for dep, tk, h, n in dep_distintos[:40]:
            print(f"    {dep:<8}{tk:<10}{h!s:<7}→ {n}")
        if len(dep_distintos) > 40:
            print(f"    … y {len(dep_distintos) - 40} más")
        print("\n  OJO: acá `True` de más es INOCUO (invalida el cache y recalcula);")
        print("  `True` de menos deja pegada una tasa vieja. Mirar los True→False.")
    else:
        print("\n  ✅ idénticas en las tres dependencias.")

    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
