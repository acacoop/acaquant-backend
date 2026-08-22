"""scripts/diag_modal.py — imprime lo que muestran QUÉ PIDE ALGO · ¿AGUANTAN? · VIGILANCIA.

Read-only, MISMAS funciones que la pantalla (`av_agent_vista.vista()` y
`av_agent_centinela.estado()`): lo que sale acá es lo que ve el navegador, y si
difiere, el bug es del front. Complementa a `diag_ahora` (la tab AHORA) y a
`diag_encontro` (el censo de LA LISTA).

Además de los datos, cada sección dice **QUÉ TIENE QUE PASAR para que algo
salga de ahí** — que es la pregunta del user («no entiendo cómo funciona, qué
esperar de esto»).

Uso: python -m scripts.diag_modal
"""
from __future__ import annotations


def main() -> None:
    from api.services import av_agent_centinela, av_agent_vista

    v = av_agent_vista.vista()

    print("═" * 68)
    print("QUÉ PIDE ALGO — la memoria completa, priorizada")
    print("═" * 68)
    q = v.get("que_importa") or {}
    print(f"abiertos={q.get('abiertos')} · piden_algo={q.get('piden_algo')} "
          f"· comunicaciones aparte={q.get('comunicaciones')}")
    print(f"por banda: {q.get('por_banda')}")
    print("\nPOR CAUSA (el resumen que manda):")
    for g in q.get("por_causa") or []:
        print(f"  {g['regla']:<28} ×{g['n']:<4} piden={g['piden']:<3} "
              f"peor={g['peor_banda']:<9} max {g['dias_max']:.0f}d  "
              f"ej: {', '.join(g['sujetos'][:3])}")
    print(f"\nfilas individuales en el payload: {len(q.get('filas') or [])} "
          f"(topeadas — el resumen por causa es el que está completo)")
    print("CÓMO SALE ALGO DE ACÁ: el detector deja de verlo (pasa a resuelto y")
    print("a ¿AGUANTAN?), o lo ignorás / lo votás ruido. No tiene botones")
    print("propios A PROPÓSITO: el grupo te lleva a LA LISTA, que es el banco")
    print("de trabajo con los botones.")

    print("\n" + "═" * 68)
    print("¿AGUANTAN? — la sala de espera de lo ya tocado")
    print("═" * 68)
    s = v.get("seguimiento") or {}
    print(f"en_prueba={s.get('en_prueba')} casos · "
          f"aguantaron={s.get('aguantaron')} · "
          f"atendidos de la foto={v.get('atendidos')}")
    print("\nPOR CAUSA:")
    for g in s.get("por_causa") or []:
        print(f"  {g['regla']:<28} ×{g['n']:<4} "
              f"días {g['dias_min']:.1f}–{g['dias_max']:.1f} (HÁBILES) · "
              f"hitos {g['hitos']}/{g['de']} · próximo control a los "
              f"{g.get('proximo_hito_en_dias')}d")
    print("\nCÓMO SALE ALGO DE ACÁ: solo el TIEMPO lo mueve — cada hito hábil")
    print("(1·2·3·7·14·30) que pasa sin volver suma confianza; a los 30 vota")
    print("«verificado» al eval set y desaparece. Si VUELVE, salta a AHORA y")
    print("pierde todo lo acumulado. No hay nada que apretar acá: es un reloj.")

    print("\n" + "═" * 68)
    print("VIGILANCIA — el backlog acumulado del monitor en vivo")
    print("═" * 68)
    est = av_agent_centinela.estado()
    ab = est.get("abiertos") or []
    print(f"abiertos={len(ab)} · sin_ver={est.get('sin_ver')} · "
          f"resueltos (últimas 8h)={len(est.get('resueltos') or [])}")
    por_regla: dict[str, int] = {}
    for f in ab:
        por_regla[f.get("regla") or "?"] = por_regla.get(f.get("regla") or "?", 0) + 1
    for regla, n in sorted(por_regla.items(), key=lambda x: -x[1]):
        print(f"  {regla:<28} ×{n}")
    print("\nCÓMO SALE ALGO DE ACÁ: se cierra SOLO cuando el monitor deja de")
    print("verlo en una pasada que sí evaluó su tipo (los de rueda, recién en")
    print("rueda). «Marcar visto» no lo cierra: solo lo saca de 'sin ver'.")


if __name__ == "__main__":
    main()
