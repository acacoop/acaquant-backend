"""¿CUÁNTAS FORMAS DISTINTAS TIENE EL AGENTE DE DECIR «RESUELTO»?

    python -m scripts.diag_ciclo

Lo pidió el user (2026-08-21): *«los avisos, lo que encuentra, los mensajes
deberían estar codeados como objetos con sus estados; si no, esto va a escalar
mal y siempre se va a solucionar sobre la marcha»*.

Esto pone el número. **No lee la base**: sale del schema y del registro, así que
se puede correr en cualquier lado y no puede mentir sobre lo que hay declarado.
"""
from __future__ import annotations


def main() -> int:
    from core import ciclo

    print("═" * 74)
    print("  EL CICLO DE VIDA DEL AGENTE — cuántas formas de decir lo mismo")
    print("═" * 74)

    formas: dict[str, list[str]] = {}
    for f in ciclo.REGISTRO:
        formas.setdefault(f.como, []).append(f.tabla)

    deuda = ciclo.sin_migrar()
    solo_lectura = [f.tabla for f in ciclo.REGISTRO
                    if f.tabla != ciclo.CANONICA and f.tabla not in deuda]
    print(f"\n  tablas del agente     {len(ciclo.REGISTRO)}")
    print(f"  ⭐ canónica            1   ({ciclo.CANONICA})")
    print(f"  ✖ con ciclo propio    {len(deuda)}   dicen «resuelto» a su manera")
    print(f"    …de esas, PROBLEMAS  {len(ciclo.deuda_de_problemas())}   "
          "(las únicas que tienen que terminar en la canónica)")
    print(f"  · append-only/config  {len(solo_lectura)}   (no tienen ciclo)")
    print(f"\n  formas distintas      {len(formas)}"
          f"   → el objetivo es {2}: la canónica y las append-only")
    # ⚠️ **TRES ESTADOS, NO DOS.** La barra decía 1/12 y era engañosa hacia
    # abajo: cinco tablas ya tienen su objeto con historia en `av_agent_items`
    # —o sea que ya ganaron lo que la migración venía a dar— y solo les falta
    # sacar la columna vieja, que es riesgo puro y ningún beneficio nuevo.
    # Contarlas como cero pinta un proyecto que no arrancó; contarlas como
    # hechas pinta uno terminado. Se muestran aparte, con su medio bloque.
    # ⚠️⚠️ **NO TODO LO QUE TIENE ESTADO ES UN PROBLEMA.** Contar las 11 como
    # deuda de modelo sobrestimaba el trabajo y apuntaba a un objetivo
    # equivocado: un `run`, una acción, el termómetro de Aunesa y el acuse de
    # recibo de un admin tienen estado y NO son problemas. Migrarlos no
    # arreglaría nada — convertiría al modelo en un cajón.
    problemas = ciclo.deuda_de_problemas()
    espejan = [t for t in problemas if t in ciclo.espejan()]
    crudas = [t for t in problemas if t not in espejan]
    otros = [t for t in deuda if t not in problemas]
    barra = "█" + "▓" * len(espejan) + "░" * len(crudas)
    print(f"  MIGRACIÓN             {barra}  "
          f"1 canónica · {len(espejan)} con objeto · {len(crudas)} crudas")
    print("                        █ es la canónica   "
          "▓ ya tiene objeto con historia (le queda sacar su columna)   "
          "░ todavía dice lo suyo")
    print(f"\n  y {len(otros)} que NO son problemas y por lo tanto NO se migran:")
    for t in otros:
        f = next(x for x in ciclo.REGISTRO if x.tabla == t)
        print(f"      {f.clase:<9} {t}")

    print("\n" + "─" * 74)
    print("  LAS 11 CON CICLO PROPIO: cómo dice cada una que algo terminó")
    print("─" * 74)
    for t in ciclo.sin_migrar():
        f = next(x for x in ciclo.REGISTRO if x.tabla == t)
        print(f"\n  {'▓' if f.espeja else '░'} {t}")
        print(f"      hoy dice:  {f.como}")
        print(f"      columnas:  {', '.join(f.campos) or '—'}")

    print("\n" + "─" * 74)
    print("  EL VOCABULARIO ÚNICO")
    print("─" * 74)
    for e in ciclo.ESTADOS:
        salidas = ", ".join(ciclo.TRANSICIONES.get(e, ())) or "—"
        print(f"  {e:<10} → {salidas}")

    huerfanas = ciclo.tablas_del_agente() - {f.tabla for f in ciclo.REGISTRO}
    if huerfanas:
        print(f"\n  ⚠ SIN DECLARAR: {', '.join(sorted(huerfanas))}")
        print("    (el test `test_ciclo` falla por esto)")
    else:
        print("\n  ✔ todas las tablas del schema están declaradas")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
