"""agente_aplicar — vaciar ENCONTRÓ sin 176 clicks.

    python -m scripts.agente_aplicar                      # DRY: qué haría (default)
    python -m scripts.agente_aplicar --accion assets.cartera
    python -m scripts.agente_aplicar --accion assets.cartera --aplicar
    python -m scripts.agente_aplicar --accion assets.cartera --aplicar --tope 5

⚠️ **DRY-RUN POR DEFAULT. Sin `--aplicar` no escribe una sola fila.**

PARA QUÉ EXISTE
===============

El user (2026-08-22): *«no quiero más nada en ENCONTRÓ de acá al lunes»*. Y
medido, las pilas más grandes **ya tienen su acción escrita**: 133
`patas_dolar_sin_pedir`, 24 `assets_sin_cartera`, 8 `fci_incompletos`… Lo que
faltaba no era el arreglo — era poder aplicarlo sin apretar un botón por caso.

**No es un camino nuevo de escritura.** Llama exactamente a `hacer.proponer()` y
`hacer.aplicar()`, las mismas que usa el modal: misma propuesta, misma
verificación releyendo, mismo libro de acciones. Un segundo camino terminaría
con dos criterios para la misma escritura, que es como se llega a que la mitad
de las carteras tengan un espacio al final.

LO QUE HAY QUE MIRAR ANTES DE APRETAR
=====================================

Las acciones **no son todas igual de reversibles**, y eso se declara:

    pedir la pata      no cambia ninguna valuación — siembra y suscribe
    cartera / FCI      **decide el divisor del AuM**: escribe plata
    apuntar el master  cambia el símbolo del que sale el precio
    avisar             manda un mensaje a una persona

Por eso el dry-run imprime, por propuesta, **de qué a qué** — y por eso `--tope`
existe: probar con 5 y mirar el resultado antes de soltar 133 es el orden
correcto, no una precaución opcional.
"""
from __future__ import annotations

import sys

# El riesgo de cada acción, DECLARADO. No se infiere del nombre: adivinar acá
# significaría escribir 133 valuaciones creyendo que no se toca nada.
_RIESGO = {
    "mercado.pedir_pata": "no toca valuaciones: siembra la especie y la suscribe",
    "mercado.pata_dolar": "no toca valuaciones: siembra la especie y la suscribe",
    "mercado.apuntar_pata": "CAMBIA de qué símbolo sale el precio del bono",
    "assets.cartera": "⚠️ ESCRIBE PLATA: la cartera decide el divisor del AuM",
    "assets.fci": "completa ticker/emisor del FCI — no toca la valuación",
    "assets.ticker": "arregla el join con la curva — no toca la valuación",
    "avisar.responsable": "manda un MENSAJE a una persona",
    "contrapartes.alta": "da de alta una contraparte",
    "sistema.rehacer_dia": "relanza un job para un día",
}


def _corto(v) -> str:
    s = str(v if v is not None else "—")
    return s if len(s) <= 46 else s[:44] + "…"


def main() -> None:
    from api.services import av_agent_hacer as hacer

    args = sys.argv[1:]
    aplicar = "--aplicar" in args
    accion_id = ""
    if "--accion" in args:
        accion_id = args[args.index("--accion") + 1]
    tope = 0
    if "--tope" in args:
        tope = int(args[args.index("--tope") + 1])

    acciones = [accion_id] if accion_id else sorted(hacer.ACCIONES)
    if accion_id and accion_id not in hacer.ACCIONES:
        print(f"«{accion_id}» no existe. Disponibles: {sorted(hacer.ACCIONES)}")
        return

    if not aplicar:
        print("\n*** DRY-RUN — no se escribe nada. Agregá --aplicar para hacerlo. ***")

    for aid in acciones:
        a = hacer.ACCIONES[aid]
        print(f"\n{'=' * 74}\n{aid}  —  {a.titulo}\n{'=' * 74}")
        print(f"  riesgo : {_RIESGO.get(aid, '⚠️ SIN DECLARAR — mirá el código')}")
        print(f"  escribe: {a.campo} en {a.donde}")

        # Vuelve a correr el control: proponer sobre la foto vieja es aprobar un
        # cambio sobre un caso que ya se resolvió.
        r = hacer.proponer(aid, con_ia=False)
        if not r.get("ok"):
            print(f"  ✘ no pude proponer: {r.get('error')}")
            continue
        props = r.get("pendientes") or []
        print(f"  casos  : {r.get('casos')} · propuestas: {len(props)}")
        if not props:
            print("  (nada para hacer)")
            continue

        elegidas = [p for p in props if str(p.get("propuesto") or "").strip()]
        sin_valor = len(props) - len(elegidas)
        if sin_valor:
            # `avisar.responsable` nace sin destinatario a propósito: a quién le
            # toca no lo puede adivinar el nombre del caso.
            print(f"  ⚠️ {sin_valor} propuesta/s SIN valor — esas se eligen a mano "
                  f"en la pantalla, no acá")
        if tope:
            elegidas = elegidas[:tope]

        for p in elegidas[:12]:
            print(f"    · {_corto(p.get('sujeto')):<46} "
                  f"{_corto(p.get('antes')):>16} → {_corto(p.get('propuesto'))}")
        if len(elegidas) > 12:
            print(f"    … y {len(elegidas) - 12} más")

        if not aplicar:
            continue
        if not elegidas:
            continue
        res = hacer.aplicar([int(p["id"]) for p in elegidas], por="script:agente_aplicar")
        ok = sum(1 for x in (res.get("resultados") or []) if x.get("ok"))
        mal = [x for x in (res.get("resultados") or []) if not x.get("ok")]
        print(f"  ⇒ aplicadas {ok}/{len(elegidas)}")
        for x in mal[:8]:
            print(f"    ✘ {x.get('sujeto')}: {x.get('error')}")

    print("\nDespués de aplicar, correr `python -m scripts.diag_encontro` "
          "para ver qué quedó.")


if __name__ == "__main__":
    main()
