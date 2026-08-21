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

    print(f"\n  tablas del agente     {len(ciclo.REGISTRO)}")
    print(f"  formas distintas      {len(formas)}")
    print(f"  CON ciclo de verdad   {len(ciclo.sin_migrar())}   ← la deuda")
    print(f"  append-only / config  {len(ciclo.REGISTRO) - len(ciclo.sin_migrar())}"
          "   (no necesitan ciclo)")

    print("\n" + "─" * 74)
    print("  LA DEUDA: las que tienen ciclo y todavía lo dicen a su manera")
    print("─" * 74)
    for t in ciclo.sin_migrar():
        f = next(x for x in ciclo.REGISTRO if x.tabla == t)
        print(f"\n  {t}")
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
