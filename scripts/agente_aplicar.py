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
    "mercado.alta_flujos": "⚠️ ESCRIBE PLATA: da de alta el bono en "
                           "mercado.curvas (cuadro 1816, cadena E2 con "
                           "simulación y cotejo antes de escribir)",
}


def _corto(v) -> str:
    s = str(v if v is not None else "—")
    return s if len(s) <= 46 else s[:44] + "…"


# ── EL MODO `arreglo`, QUE NO ES UNA `Accion` Y SE PODÍA APLICAR IGUAL ───────
#
# ⚠️⚠️ **POR QUÉ ESTO EXISTE.** El censo mostró **53 hallazgos «uno por uno»**
# —21 `moneda_flujo_contradice`, 9 `sin_ejes`, 8 `paridad_fuera_de_rango`…—
# diciendo que había que abrirlos en la pantalla de a uno porque no hay una
# `Accion` registrada que los cubra. Y era verdad a medias: **`Accion` no es el
# único mecanismo de arreglo**. El modo `arreglo` tiene su propio par
# simular/aplicar (`av_agent_alta.simular_arreglo` / `aplicar_arreglo`), que es
# lo que aprieta el botón de la pantalla, y encadenarlo en lote es legítimo.
#
# Lo que hacía falta para que fuera SEGURO ya estaba escrito: `aplicar_arreglo`
# **vuelve a simular adentro** y se niega si `puede_aplicar` es falso, con la
# guarda en un solo lado. Acá no se re-decide nada — si este script tuviera su
# propio criterio, sería el cuarto gate contradiciéndose con los otros tres.
#
# ⚠️ Es la única puerta del agente que **PISA un dato existente**, y toca ejes y
# moneda: errarle a `moneda_eje` es plata mal contada. Por eso el dry-run no
# muestra solo «se puede», muestra **de qué a qué** cambia cada eje.
_MODOS = {"arreglo": "⚠️⚠️ PISA ejes/escala de un bono que YA existe"}


def _motivo_corto(txt: str) -> str:
    """El motivo, sin los números del caso — para poder AGRUPAR.

    Dos bonos trabados por lo mismo tienen detalles distintos («TEA 41,2%» vs
    «TEA 38,9%») y contados de a uno parecen 38 problemas cuando son cuatro.
    """
    import re
    t = re.sub(r"[-+]?\d[\d.,]*%?", "#", str(txt or ""))
    t = " ".join(t.split())
    return t if len(t) <= 96 else t[:94] + "…"


def _que_cambia(sim: dict) -> str:
    """QUÉ va a escribir este arreglo, en una línea.

    ⚠️ La primera versión mostraba solo los ejes y los cuatro aplicables salían
    con `→ —`: sus ejes ya estaban bien y lo que el arreglo corrige es **otra
    cosa** (la escala del cuadro, el CER de emisión). Un dry-run que muestra
    vacío justo en las filas que SÍ se van a escribir es peor que no mostrar
    nada — invita a aplicar a ciegas.
    """
    partes = []
    hoy, prop = sim.get("ejes_hoy"), sim.get("ejes_propuestos")
    if prop and prop != hoy:
        def _ejes(d):
            return "/".join(str(v or "—") for v in (d or {}).values()) if d else "SIN EJES"
        partes.append(f"ejes {_ejes(hoy)} → {_ejes(prop)}")
    if sim.get("escala"):
        partes.append(f"escala {sim['escala']}")
    if sim.get("cer_emision") and sim.get("cer_manual"):
        partes.append(f"cer_emision {sim['cer_emision']}")
    if sim.get("cupones"):
        partes.append(f"{sim['cupones']} cupones")
    return " · ".join(partes) or "(el simulador no declaró qué cambia — NO aplicar)"


def _casos_del_modo(modo: str) -> list[str]:
    """Los sujetos que hoy declaran ese modo, **leídos de la vista**.

    De la misma lista que dibuja el modal y con la `accion` ya resuelta por
    `accion_de()` — no de un conjunto de reglas escrito acá, que es justo el
    error que hizo que el censo contara 408 donde la pantalla mostraba 98.
    """
    from api.services import av_agent_vista
    vistos: list[str] = []
    for h in (av_agent_vista.vista().get("hallazgos") or []):
        if (h.get("accion") or "") != modo:
            continue
        tk = (h.get("ticker") or "").strip()
        if tk and not tk.startswith(("control:", "job:")) and tk not in vistos:
            vistos.append(tk)
    return vistos


def _callar_motores() -> None:
    """Bajar el ruido de los motores durante el lote.

    ⚠️ `simular_arreglo` levanta un `MotorCurvas` por bono, y cada uno anuncia
    «Días hábiles cargados: 243» y «TC dolar-linked». Con 42 bonos son ~60
    líneas que **tapan la salida entera** — el dry-run existe para poder leerlo
    y quedaba sepultado bajo su propio log.

    Se sube el nivel de esos loggers, no se toca el código del motor: es ruido
    solo EN ESTE CONTEXTO. Corriendo de verdad, esas líneas son útiles.
    """
    import logging
    for nombre in ("MotorCurvas", "core.mercado_1816"):
        logging.getLogger(nombre).setLevel(logging.WARNING)


def _correr_modo(modo: str, *, aplicar: bool, tope: int) -> None:
    from api.services import av_agent_alta

    _callar_motores()

    print(f"\n{'=' * 74}\nmodo «{modo}»  —  arreglar el INSUMO de un bono ya cargado"
          f"\n{'=' * 74}")
    print(f"  riesgo : {_MODOS.get(modo, '⚠️ SIN DECLARAR')}")
    sujetos = _casos_del_modo(modo)
    print(f"  casos  : {len(sujetos)}")
    if tope:
        sujetos = sujetos[:tope]

    listos, trabados = [], []
    for tk in sujetos:
        try:
            sim = av_agent_alta.simular_arreglo(tk)
        except Exception as e:
            trabados.append((tk, f"{type(e).__name__}: {e}"))
            continue
        if not sim.get("ok"):
            trabados.append((tk, str(sim.get("error") or "no se pudo simular")))
            continue
        ver = sim.get("veredicto") or {}
        if not ver.get("puede_aplicar"):
            # La constante, no el string: `BLOQUEA` vale «bloquea» hoy y si
            # mañana cambia, una copia acá dejaría de encontrar los bloqueos y
            # el lote diría «trabado, motivo desconocido» sin fallar.
            #
            # ⚠️ **EL `detalle`, NO EL `titulo`.** La primera versión imprimía
            # el título y salía «BVCVO: La métrica vuelve al rango» — que se
            # LEE COMO QUE PASÓ. El título de un chequeo dice qué se exige; el
            # `detalle` dice qué encontró. Mostrar el requisito en el lugar del
            # motivo deja al que mira sin poder decidir nada, que es justo lo
            # que este script vino a resolver.
            bloqueos = [c for c in (sim.get("chequeos") or [])
                        if c.get("estado") == av_agent_alta.BLOQUEA]
            porque = "; ".join(
                f"{c.get('titulo')}: {c.get('detalle') or '(sin detalle)'}"
                for c in bloqueos) or "el pre-flight no pasa"
            trabados.append((tk, _motivo_corto(porque)))
            continue
        listos.append((tk, sim))

    print(f"  listos : {len(listos)} · trabados: {len(trabados)}")
    for tk, sim in listos[:12]:
        print(f"    · {tk:<10} {_que_cambia(sim)}")
    if len(listos) > 12:
        print(f"    … y {len(listos) - 12} más")
    # **Los trabados se MUESTRAN, y AGRUPADOS.** Un lote que dice «apliqué 4» y
    # calla los otros 38 es el mismo silencio que hizo perder tres rondas; y una
    # lista de 38 líneas distintas tampoco dice qué hacer. Lo accionable es
    # CUÁNTOS comparten la misma traba: 20 esperando un precio es un problema,
    # 20 problemas distintos son veinte.
    from collections import Counter
    print("  por qué se traban:")
    for motivo, n in Counter(m for _, m in trabados).most_common(8):
        ejemplos = [tk for tk, m in trabados if m == motivo][:4]
        print(f"    {n:>4}  {motivo}")
        print(f"          {' · '.join(ejemplos)}" + (" …" if n > 4 else ""))

    if not aplicar or not listos:
        return
    ok = 0
    for tk, _, _ in listos:
        try:
            r = av_agent_alta.aplicar_arreglo(tk, actor="script:agente_aplicar")
        except Exception as e:
            print(f"    ✘ {tk}: {type(e).__name__}: {e}")
            continue
        if r.get("aplicado"):
            ok += 1
        else:
            print(f"    ✘ {tk}: {_corto(r.get('error'))}")
    print(f"  ⇒ aplicados {ok}/{len(listos)}")


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

    if not aplicar:
        print("\n*** DRY-RUN — no se escribe nada. Agregá --aplicar para hacerlo. ***")

    if accion_id in _MODOS:
        _correr_modo(accion_id, aplicar=aplicar, tope=tope)
        return

    acciones = [accion_id] if accion_id else sorted(hacer.ACCIONES)
    if accion_id and accion_id not in hacer.ACCIONES:
        print(f"«{accion_id}» no existe. Disponibles: "
              f"{sorted(hacer.ACCIONES) + sorted(_MODOS)}")
        return

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
        # ⚠️ **EL RE-CHEQUEO SE IMPRIME, y no imprimirlo era la mitad del
        # problema.** `hacer.aplicar()` vuelve a correr el control de cada
        # acción y devuelve el resultado en `recontrol` — este script lo tiraba
        # a la basura. Así que la única respuesta que el user veía era
        # «aplicadas 5/5», y para saber si eso movió algo tenía que esperar a
        # la corrida de la noche. Es EL número que contesta «¿sirvió?».
        for cid, txt in (res.get("recontrol") or {}).items():
            print(f"    ↻ {cid}: {txt}")

    if not accion_id:
        # Sin `--accion` se recorre TODO, y los modos son parte de «todo». Que
        # el barrido completo se saltee 45 casos sin decirlo es exactamente el
        # agujero que este cambio vino a tapar.
        for m in sorted(_MODOS):
            _correr_modo(m, aplicar=aplicar, tope=tope)

    print("\nDespués de aplicar, correr `python -m scripts.diag_encontro` "
          "para ver qué quedó.")


if __name__ == "__main__":
    main()
