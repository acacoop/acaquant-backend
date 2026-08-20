"""¿CUÁNTO DE LO QUE EL AGENTE VE, EL AGENTE PUEDE RESOLVER?

    python -m scripts.diag_puertas

Lo dictó el caso BOPREAL: un hallazgo sin arreglo posible no es un aviso, es una
**pared** — y una pared que aparece todas las ruedas enseña a ignorar la lista
entera. `pata_equivocada` fue la más cara y tardamos semanas en saberlo, porque
la única forma de enterarse era que alguien se hartara de verla.

Esto lo convierte en un número: cuántos hallazgos tienen puerta, cuántos no, y
**cuál conviene construir primero**, ordenado por cuántas veces aparece en la
pantalla. Arreglar la que sale 48 veces vale más que la que sale una.

⚠️ **No todo lo que no tiene puerta es deuda**, y mezclarlos daría una cobertura
falsamente mala. Una buena noticia no tiene nada que arreglar; `dato_partido` se
decidió no automatizar a propósito. La deuda son las que **se podrían cerrar y no
están**.

Solo LEE.
"""
from __future__ import annotations


def main() -> int:
    from api.services import av_agent
    from api.services.av_agent_vista import _hallazgos_ultima_corrida
    from core import escribe

    hallazgos, corrida = _hallazgos_ultima_corrida()
    c = av_agent.cobertura(hallazgos)

    print("═" * 74)
    print("  PUERTAS DEL AGENTE — qué puede resolver de lo que encuentra")
    print(f"  última corrida: {corrida or '—'}")
    print("═" * 74)
    if not c["total"]:
        print("\n  No hay hallazgos vigentes: nada que medir.\n")
        return 0

    print(f"\n  hallazgos       {c['total']}")
    print(f"  con puerta      {c['con_puerta']}  ({c['pct']}%)")
    print(f"  sin puerta      {c['sin_puerta']}")
    print(f"  …de eso, DEUDA  {c['deuda']}   ← lo que se podría cerrar y no está")

    if c["paredes"]:
        print("\n" + "─" * 74)
        print("  LA FILA: qué construir primero (por cuánto ruido hace)")
        print("─" * 74)
        for i, x in enumerate(c["paredes"], 1):
            ej = ", ".join(str(e) for e in x["ejemplos"] if e)
            print(f"\n  {i}. {x['regla']}  ×{x['veces']}   [{x['tipo']}]")
            print(f"     falta: {x['que_falta']}")
            if ej:
                print(f"     ej: {ej}")
            # Para `tabla_quieta`, QUÉ relanzar — o que no hay nada que
            # relanzar, que es la mitad que evita construir una puerta a
            # ninguna parte (§0.aq).
            if x["tipo"] == "tabla_quieta":
                for e in x["ejemplos"]:
                    if not e:
                        continue
                    mod = escribe.que_relanzar(e)
                    quien = escribe.la_dispara(e)
                    print(f"       {e:<28} {quien:<7} "
                          + (f"→ {mod}" if mod else "(nada que relanzar)"))
    else:
        print("\n  ✔ Ninguna pared con deuda: todo lo sin puerta es por decisión.")

    # El detalle de POR QUÉ cada tipo no la tiene — lo que evita confundir «no se
    # puede» con «no se hizo».
    print("\n" + "─" * 74)
    print("  POR QUÉ no tiene puerta cada tipo")
    print("─" * 74)
    vistos = {h.get("tipo") for h in hallazgos}
    for tipo in sorted(t for t in vistos if t):
        p = av_agent.puerta(tipo)
        if p["hay"]:
            print(f"  {tipo:<20} ✔ acción «{p['accion']}»")
        else:
            marca = "DEUDA" if p["es_deuda"] else "     "
            print(f"  {tipo:<20} {marca} [{p['clase']}] {p['porque']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
