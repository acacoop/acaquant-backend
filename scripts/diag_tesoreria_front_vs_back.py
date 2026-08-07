"""Compara el `saldo_final` que calcula el BACKEND contra el que recalcula el FRONT.

Por qué: `tesoreria-view.tsx` no usa el `saldo_final` que manda la API — lo vuelve a
calcular con su propia fórmula. Son dos copias de la misma regla de negocio, y si se
separan la grilla muestra un total distinto al del backend SIN que nada falle.

Este diag replica en Python, literal, la fórmula del front y la compara banco por
banco con la del back. Si no hay diferencias, sacar la copia del front es seguro:
la pantalla va a seguir mostrando exactamente los mismos números.

Compara las dos fuentes que el front normaliza con esa misma función:
  - la vista LIVE del día (`ingresos_egresos_dia`)
  - la FOTO de un día cerrado (`foto_dia`), que alimenta el histórico de BANCOS

Es READ-ONLY. Uso:
    python -m scripts.diag_tesoreria_front_vs_back              # hoy
    python -m scripts.diag_tesoreria_front_vs_back 2026-08-06   # una fecha
    python -m scripts.diag_tesoreria_front_vs_back --fotos      # todas las fotos
"""
from __future__ import annotations

import sys

from api.services import tesoreria as t

# Tolerancia: por debajo de un centavo es ruido de coma flotante, no una diferencia
# real de criterio. Cualquier cosa por encima es un bug que la mesa podría ver.
TOL = 0.005


def saldo_como_lo_calcula_el_front(c: dict) -> float:
    """Traducción LITERAL de `normalizarCuentas` (acaquant-frontend, tesoreria-view.tsx).

        const ini = c.saldo_inicial ?? 0;
        saldo_final: ini + c.neto + (c.ingresos_echeq ?? 0) - (c.egresos_echeq ?? 0)
                     + (c.mercados ?? 0) + (c.fci ?? 0) + (c.bb_mas ?? 0) - (c.bb_menos ?? 0)

    OJO: el front NO redondea el total (el back sí, a 2 decimales). Esa es justamente
    una de las diferencias que este diag tiene que sacar a la luz.
    """
    def n(k: str) -> float:
        v = c.get(k)
        return float(v) if v is not None else 0.0

    return (n("saldo_inicial") + n("neto") + n("ingresos_echeq") - n("egresos_echeq")
            + n("mercados") + n("fci") + n("bb_mas") - n("bb_menos"))


def comparar(nombre: str, cuentas: list[dict]) -> int:
    """Compara una grilla completa. Devuelve cuántas filas difieren."""
    print(f"\n── {nombre} · {len(cuentas)} filas ─────────────────────────────")
    if not cuentas:
        print("   (sin filas — nada que comparar)")
        return 0
    difs = 0
    peor = 0.0
    for c in cuentas:
        back = float(c.get("saldo_final") or 0)
        front = saldo_como_lo_calcula_el_front(c)
        d = abs(back - front)
        peor = max(peor, d)
        if d > TOL:
            difs += 1
            print(f"   ✗ {c.get('cuenta_operativa')} [{c.get('unidad')}]")
            print(f"       back={back:,.2f}   front={front:,.2f}   dif={back - front:,.4f}")
            print(f"       inicial={c.get('saldo_inicial')} neto={c.get('neto')} "
                  f"ing_echeq={c.get('ingresos_echeq')} egr_echeq={c.get('egresos_echeq')} "
                  f"mercados={c.get('mercados')} fci={c.get('fci')} "
                  f"bb_mas={c.get('bb_mas')} bb_menos={c.get('bb_menos')}")
    if difs == 0:
        print(f"   ✓ COINCIDEN las {len(cuentas)} filas (peor diferencia: {peor:.6f})")
    else:
        print(f"   ✗ {difs} de {len(cuentas)} filas NO coinciden")
    return difs


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    todas_fotos = "--fotos" in sys.argv
    fecha = args[0] if args else None

    print(f"\n{'=' * 72}\nSALDO FINAL — backend vs. la copia del frontend\n{'=' * 72}")
    total_difs = 0

    # 1) La vista LIVE del día.
    dia = t._dia(fecha)
    try:
        live = t.ingresos_egresos_dia(fecha=dia.isoformat(), email="")["cuentas"]
        total_difs += comparar(f"VISTA LIVE · {dia.isoformat()}", live)
    except Exception as e:
        print(f"\n   ERROR leyendo la vista live: {type(e).__name__}: {e}")

    # 2) Las FOTOS (histórico de BANCOS): el front las normaliza con la MISMA función,
    #    así que si difieren, el histórico miente igual que el día.
    try:
        fechas = [f["fecha"] for f in t.listar_snapshots()["snapshots"]]
    except Exception as e:
        print(f"\n   (no pude listar las fotos: {type(e).__name__}: {e})")
        fechas = []

    if fechas and not todas_fotos:
        fechas = fechas[:3]
        print(f"\n   (comparando las {len(fechas)} fotos más recientes — "
              f"pasá --fotos para todas)")
    for f in fechas:
        try:
            foto = t.foto_dia(str(f))
            total_difs += comparar(f"FOTO · {f}", foto.get("cuentas") or [])
        except Exception as e:
            print(f"\n   ERROR leyendo la foto {f}: {type(e).__name__}: {e}")

    print(f"\n{'=' * 72}")
    if total_difs == 0:
        print("VEREDICTO: ✓ el front y el back dan el MISMO número en todas las filas.")
        print("Se puede borrar la fórmula del front y usar `saldo_final` de la API")
        print("sin que cambie un solo número en pantalla.")
    else:
        print(f"VEREDICTO: ✗ {total_difs} filas difieren — NO borrar la fórmula del front")
        print("todavía. Hay que entender primero por qué se separaron (arriba está el")
        print("desglose de cada componente de la fila que no cierra).")
    print(f"{'=' * 72}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
