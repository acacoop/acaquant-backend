"""scripts/diag_por_curva.py — ¿qué cambia si `por_curva` filtra por los EJES? READ-ONLY.

`core/curvas_sql.py::por_curva(curva)` es el helper que usan **~40 archivos** para
preguntar "dame los bonos de esta curva". Hoy responde comparando contra el campo
`curva` del blob: **una palabra escrita a mano en cada fila**.

Si ese helper pasa a usar los ejes (`curvas_ejes.curvas_de`), TODOS sus
consumidores migran de una y `mercado.curvas.curva` se queda sin lectores — que
es la condición para poder borrarla.

Pero **no es un no-op**, y por eso existe este diag: hay bonos cuya columna dice
una cosa y cuyos ejes dicen otra. Antes de cambiar un helper que tocan 40
archivos hay que ver, POR NOMBRE, quién entra y quién sale de cada curva.

Los tres casos y por qué importan:

  · **ENTRA** — el bono aparece en una tabla/cálculo donde hoy no está. Suele ser
    la corrección (un dual que ahora se ve en sus dos curvas) pero también puede
    meter un corporativo a un fit soberano.
  · **SALE**  — deja de estar donde hoy está. Es el caso peligroso: si esa curva
    alimenta una tabla que NUNCA BORRA, el bono queda congelado.
  · **HUÉRFANO** — tiene `curva` pero no tiene ejes. Con el cambio no cae en
    ninguna curva: **desaparece de todo**, sin error.

READ-ONLY. No escribe, no borra, no toca los motores.

Uso:
    python -m scripts.diag_por_curva
"""
from __future__ import annotations

from core import curvas_ejes as ce
from core.postgres import get_pool

_SEP = "=" * 96


def _q(sql: str, params: tuple = ()) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]


def main() -> None:
    print(_SEP)
    print("`por_curva` por EJES — qué cambiaría para los ~40 archivos que lo usan")
    print(_SEP)

    filas = _q("""
        SELECT ticker, curva, emisor, emisor_tipo, moneda_eje, ajuste, ajuste_alt, ley
        FROM mercado.curvas ORDER BY ticker
    """)

    # `hoy` = lo que devuelve por_curva HOY (compara contra la columna).
    # `nuevo` = lo que devolvería con los ejes. Un bono puede caer en DOS.
    hoy: dict[str, set[str]] = {}
    nuevo: dict[str, set[str]] = {}
    huerfanos: list[dict] = []
    sin_curva_nueva: list[dict] = []

    for f in filas:
        tk = f["ticker"]
        c = (f["curva"] or "").strip()
        if c:
            hoy.setdefault(c, set()).add(tk)
        ejes = ce.ejes_de_doc(f)
        cs = ce.curvas_de(ejes)
        for c2 in cs:
            nuevo.setdefault(c2, set()).add(tk)
        if c and ejes is None:
            huerfanos.append(f)
        elif c and not cs:
            sin_curva_nueva.append(f)

    print(f"\n  {len(filas)} bonos · {len(hoy)} curvas hoy · {len(nuevo)} con ejes\n")
    todas = sorted(set(hoy) | set(nuevo))
    print(f"  {'CURVA':<16}{'HOY':>6}{'EJES':>6}{'ENTRAN':>8}{'SALEN':>7}")
    print("  " + "-" * 47)
    for c in todas:
        h, n = hoy.get(c, set()), nuevo.get(c, set())
        marca = "  " if h == n else "⚠ "
        print(f"  {marca}{c[:14]:<14}{len(h):>6}{len(n):>6}"
              f"{len(n - h):>8}{len(h - n):>7}")

    for c in todas:
        h, n = hoy.get(c, set()), nuevo.get(c, set())
        if h == n:
            continue
        print(f"\n  ── {c} " + "─" * (70 - len(c)))
        if n - h:
            print(f"     ENTRAN ({len(n - h)}): {', '.join(sorted(n - h)[:18])}"
                  + (" …" if len(n - h) > 18 else ""))
        if h - n:
            print(f"     SALEN  ({len(h - n)}): {', '.join(sorted(h - n)[:18])}"
                  + (" …" if len(h - n) > 18 else ""))

    # LO QUE MÁS IMPORTA: los que hoy están en alguna curva y con los ejes no
    # caen en NINGUNA. Esos no "cambian de tabla": se evaporan.
    print(f"\n{_SEP}\n  EL RIESGO: bonos que hoy tienen curva y con los ejes no caen "
          f"en ninguna\n{_SEP}")
    perdidos = huerfanos + sin_curva_nueva
    if not perdidos:
        print("  ✅ ninguno. Todo lo que hoy está en una curva sigue estando.")
    else:
        print(f"  {len(perdidos)} bono(s). Con el cambio DESAPARECEN de todo lo que")
        print("  usa `por_curva` — sin error y sin log. Hay que clasificarlos ANTES.\n")
        print(f"  {'TICKER':<12}{'CURVA HOY':<16}{'MOTIVO':<28}EMISOR")
        print("  " + "-" * 86)
        for f in huerfanos:
            print(f"  {str(f['ticker'])[:11]:<12}{str(f['curva'])[:15]:<16}"
                  f"{'sin ejes':<28}{str(f['emisor'] or '—')[:28]}")
        for f in sin_curva_nueva:
            print(f"  {str(f['ticker'])[:11]:<12}{str(f['curva'])[:15]:<16}"
                  f"{'ajuste sin curva: ' + str(f['ajuste'])[:9]:<28}"
                  f"{str(f['emisor'] or '—')[:28]}")

    print(f"\n{_SEP}\nFIN — nada de esto escribió en la base.\n{_SEP}")


if __name__ == "__main__":
    main()
