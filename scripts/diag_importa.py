"""QUÉ PIDE ALGO HOY — la memoria del agente, leída (§0.bm).

    python -m scripts.diag_importa

Read-only. Existe para poder ver el ranking **sin abrir la pantalla**: la
primera pregunta después de una migración es siempre «¿y esto qué muestra
ahora?», y contestarla mirando la app mezcla dos cosas que pueden fallar por
separado (el dato y el render).

Muestra lo mismo que la tab AHORA: las bandas, el conteo que convierte la lista
en una decisión, y los que siguen abiertos sin que nadie los vuelva a mirar.
"""
from __future__ import annotations

_TXT = {
    "volvio": "VOLVIÓ después de arreglarse",
    "estancado": "lo viste y sigue igual",
    "arrastra": "abierto hace días y SIN VER",
    "nuevo": "apareció hoy",
    "mirando": "resuelto, en prueba",
}


def main() -> int:
    from api.services import av_agent_items

    r = av_agent_items.que_importa()
    if not r.get("ok"):
        print(f"\n  ✖ no pude leer: {r.get('error')}\n")
        return 1

    print("═" * 74)
    print("  QUÉ PIDE ALGO HOY")
    print("═" * 74)
    print(f"\n  abiertos              {r['abiertos']}")
    print(f"  PIDEN ALGO            {r['piden_algo']}   "
          "(volvió · estancado · arrastra — lo nuevo todavía no probó nada)")

    print("\n  por banda:")
    for b in ("volvio", "estancado", "arrastra", "nuevo"):
        n = r["por_banda"].get(b, 0)
        if n:
            print(f"    {n:>4}  {_TXT[b]}")

    filas = r["filas"]
    if filas:
        print(f"\n  los {min(20, len(filas))} primeros, en orden de prioridad:")
        print(f"    {'SUJETO':<16} {'CAUSA':<26} {'BANDA':<12} {'DÍAS':>6} {'×':>5}")
        for f in filas[:20]:
            print(f"    {f['sujeto'][:16]:<16} {f['regla'][:26]:<26} "
                  f"{f['banda']:<12} {f['dias_abierto']:>6.1f} {f['veces']:>5}")

    sm = r.get("sin_mirar") or []
    print("\n" + "─" * 74)
    print("  SIGUE ABIERTO PERO NADIE LO VOLVIÓ A MIRAR")
    print("─" * 74)
    print("  «sigue roto» y «nadie lo re-evaluó» se ven idénticos: los dos son")
    print("  una fila abierta. Si el origen volvió a correr y a éste no lo")
    print("  refrescó, el detector pasó y NO lo miró.\n")
    if not sm:
        print("  ✔ ninguno: cada origen re-evaluó todo lo suyo.\n")
    else:
        for x in sm[:20]:
            print(f"    {x['sujeto'][:16]:<16} {x['regla'][:26]:<26} "
                  f"{x['origen']:<12} hace {x['horas_sin_reevaluar']:>6.1f} h")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
