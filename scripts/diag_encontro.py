"""diag_encontro — TODO lo de ENCONTRÓ, clasificado por QUÉ TRABAJO HACE FALTA.

    python -m scripts.diag_encontro            # el censo
    python -m scripts.diag_encontro --regla moneda_flujo_contradice

Read-only. Cero escrituras, cero llamadas a 1816.

⚠️ **PARA QUÉ EXISTE.** El user (2026-08-22): *«no quiero más nada en ENCONTRÓ
de acá al lunes»*. Para eso hay que contestar algo que la pantalla no contesta:
**de los N abiertos, ¿cuántos son de cada clase?** Sin eso, «vaciar ENCONTRÓ» es
una lista de 98 filas y no un plan.

⚠️⚠️ **LEE LA MISMA FUENTE QUE LA PANTALLA, y la primera versión no.**
Consultaba `mercado.av_agent_items` directo —el objeto canónico— y contó **408
donde la pantalla mostraba 98**: esa tabla guarda TAMBIÉN los avisos dirigidos
(142 `saldos_comitentes`, que son mensajes a operadores y no problemas) y los
sensores. Un diag que contradice a la pantalla que viene a explicar no sirve
para decidir nada.

Ahora llama a `av_agent_vista.vista()`, que es literalmente lo que el modal
dibuja. De yapa cada fila llega con `accion` YA resuelta por `accion_de()`, así
que «¿tiene puerta?» tampoco se reimplementa acá — que era el otro error: la
primera versión armaba su propio conjunto de reglas-con-acción y se le
escapaban las que se resuelven por CONTROL (133 `patas_dolar_sin_pedir` salían
como deuda teniendo `mercado.pedir_pata` escrita).

Las cinco clases NO son severidad — son **qué trabajo hace falta**, que es lo
único que decide el orden en que se atacan.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

# Tablas cuyo «no se escribe» NO es un problema: por diseño no se escriben
# solas. Se listan para MEDIR cuántas son — el arreglo va en el DETECTOR, y este
# número dice si vale la pena escribirlo.
_QUIETAS_ESPERADAS = (
    "realtime.schema_migrations",     # interna de Supabase, nunca la tocamos
    "portafolio.assets",              # catálogo: cambia cuando la mesa edita
    "manager.salud_eventos",          # solo escribe en una TRANSICIÓN
    "operaciones.ordenes_live",       # solo si hubo órdenes ese día
    "operaciones.ordenes_audit",
    "operaciones.tesoreria_cheques",
    "mercado.camara_cereales_audit",
)

_CLASES = ("AUTO_CERRABLE", "RUIDO_ESTRUCTURAL", "APLICABLE_EN_LOTE",
           "UNO_POR_UNO", "FALTA_ACCION", "HUMANO")


def _accion_registrada(h: dict) -> str:
    """El id de la ACCIÓN DETERMINISTA que cubre este hallazgo, o `""`.

    ⚠️⚠️ **ACÁ ESTABA LA MENTIRA MÁS CARA DEL CENSO** (2026-08-22). La primera
    versión decía «TIENE_PUERTA → apretar el botón» para **79 hallazgos** cuando
    solo 30 tenían un botón que se pudiera apretar en lote. El resto declaraba
    `accion="arreglo"`, y `arreglo` **no es una acción**: es el MODO del panel
    con IA, que se usa caso por caso mirando el bono.

    O sea que el censo mandaba a correr `agente_aplicar` sobre 47 casos que ese
    script no puede tocar —porque solo conoce las 9 de `ACCIONES`— y el usuario
    corría el comando, no bajaba nada y no había forma de saber por qué.

    Es el mismo defecto de siempre con una cara nueva: **tres vocabularios para
    la misma pregunta.** `accion_de()` devuelve un MODO de pantalla, `ACCIONES`
    tiene ARREGLOS ejecutables, y este script trataba al primero como si fuera
    el segundo. Ahora se deriva del registro real — si mañana alguien escribe
    la acción de `sin_ejes`, esos 9 se mueven de pila solos.
    """
    from api.services.av_agent_hacer import ACCIONES, POR_CONTROL

    sujeto = (h.get("ticker") or "").strip()
    if sujeto.startswith("control:"):
        return POR_CONTROL.get(sujeto.split(":", 1)[1], "")
    regla = (h.get("regla") or "").strip()
    for aid, a in ACCIONES.items():
        if getattr(a, "causa", "") == regla:
            return aid
    return ""


def _etiqueta(h: dict) -> str:
    """El sujeto + cuántos casos tiene adentro, si son más de uno.

    Un `control:patas_dolar_sin_pedir` con 133 casos y uno con 1 se veían
    idénticos en esta lista, y son trabajos de tamaño muy distinto.
    """
    base = str(h.get("nombre") or h.get("ticker") or "")[:20]
    n = h.get("n_casos")
    return f"{base}({n})" if isinstance(n, int) and n > 1 else base


def _clasificar(h: dict) -> tuple[str, str]:
    """(clase, por qué). El orden de los `if` ES la prioridad."""
    regla = (h.get("regla") or "").strip()
    sujeto = (h.get("ticker") or "").strip()

    if h.get("es_ruido"):
        return "AUTO_CERRABLE", "una persona lo marcó «es ruido» y sigue en la lista"
    if h.get("atendido") == "aplicado":
        return "AUTO_CERRABLE", "el arreglo ya se aplicó y la fila sigue"
    if regla == "tabla_nueva":
        return "RUIDO_ESTRUCTURAL", ("«apareció una tabla» informa UNA vez; "
                                     "abierto para siempre es ruido")
    if regla in ("sin_escribir", "tabla_quieta") and sujeto in _QUIETAS_ESPERADAS:
        return "RUIDO_ESTRUCTURAL", "por diseño esa tabla no se escribe sola"
    # ⚠️ **DOS PREGUNTAS DISTINTAS, y confundirlas es lo que hizo que el censo
    # mintiera durante tres rondas.** «¿Tiene modo de pantalla?» la contesta
    # `accion_de()`; «¿se puede aplicar en lote?» la contesta el registro
    # `ACCIONES`. Solo la segunda habilita el `agente_aplicar`.
    aid = _accion_registrada(h)
    if aid:
        return "APLICABLE_EN_LOTE", f"`agente_aplicar --accion {aid}`"
    if h.get("accion"):
        return "UNO_POR_UNO", (f"modo «{h['accion']}» — se resuelve en la PANTALLA, "
                               f"caso por caso. NO hay arreglo de lote escrito")
    if (h.get("tipo") or "") in ("motor_ruidoso", "motor_caido", "proveedor_caido"):
        return "HUMANO", "infraestructura: se mira, no se arregla desde acá"
    if h.get("puerta"):          # el backend explica POR QUÉ no hay botón
        return "HUMANO", str(h["puerta"])[:60]
    return "FALTA_ACCION", "nadie escribió el arreglo todavía"


def main() -> None:
    filtro = ""
    if "--regla" in sys.argv:
        filtro = sys.argv[sys.argv.index("--regla") + 1]

    from api.services import av_agent_vista
    data = av_agent_vista.vista()
    hallazgos = [h for h in (data.get("hallazgos") or [])
                 if not filtro or h.get("regla") == filtro]

    print(f"\n{'=' * 74}")
    print(f"ENCONTRÓ: {len(hallazgos)} hallazgos "
          f"(la MISMA lista que dibuja el modal)")
    print(f"{'=' * 74}")

    por_clase: dict[str, list] = defaultdict(list)
    for h in hallazgos:
        clase, porque = _clasificar(h)
        por_clase[clase].append((h, porque))

    for clase in _CLASES:
        filas = por_clase.get(clase) or []
        print(f"\n── {clase}  ({len(filas)}) " + "─" * max(2, 48 - len(clase)))
        if not filas:
            continue
        # Por REGLA y por VOLUMEN: arreglar la que sale 21 veces vale más que la
        # que sale una, y eso es un número y no una corazonada.
        for regla, n in Counter(h["regla"] for h, _ in filas).most_common():
            del_grupo = [(h, p) for h, p in filas if h["regla"] == regla]
            porque = del_grupo[0][1]
            ejemplos = [_etiqueta(h) for h, _ in del_grupo][:6]
            # **CASOS, no filas.** Un control es UNA fila y puede tener 133
            # casos adentro; contar filas dice que arreglar 5 de 8 no movió
            # nada. Solo se muestra si difiere del número de filas, para no
            # ensuciar las reglas donde una fila ES un caso.
            casos = sum(int(h.get("n_casos") or 1) for h, _ in del_grupo)
            cola = f"  [{casos} casos]" if casos != n else ""
            print(f"  {n:>4}  {regla:<26}{cola} {porque}")
            print(f"        {' · '.join(ejemplos)}" + (" …" if n > 6 else ""))

    print(f"\n{'=' * 74}\nQUÉ HACER CON CADA PILA\n{'=' * 74}")
    for clase, que in (
            ("AUTO_CERRABLE", "cerrarlos: el agente ya probó que no hay nada que hacer"),
            ("RUIDO_ESTRUCTURAL", "arreglar el DETECTOR, no el caso"),
            ("APLICABLE_EN_LOTE", "`agente_aplicar` los resuelve HOY, sin escribir código"),
            ("UNO_POR_UNO", "hay que abrirlos en la PANTALLA de a uno — o escribir "
                            "su acción de lote, que es lo que los volvería masivos"),
            ("FALTA_ACCION", "DEUDA: escribir el arreglo, por volumen"),
            ("HUMANO", "criterio de la mesa — el único resto legítimo")):
        filas_c = por_clase.get(clase) or []
        casos_c = sum(int(h.get("n_casos") or 1) for h, _ in filas_c)
        cola = f" ({casos_c} casos)" if casos_c != len(filas_c) else ""
        print(f"  {len(filas_c):>4}{cola:<14}  {que}")

    # ⚠️ **LO QUE LA PANTALLA NO MUESTRA, dicho igual.** `av_agent_items` guarda
    # además avisos y sensores. No son deuda de ENCONTRÓ, pero si el número no
    # se dice, el día que alguien consulte esa tabla va a ver 400 y va a pensar
    # que la pantalla esconde cosas.
    try:
        from core.postgres import get_pool
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT tipo, count(*) FROM mercado.av_agent_items "
                        "WHERE estado NOT IN ('resuelto','ignorado') "
                        "GROUP BY tipo ORDER BY 2 DESC LIMIT 8")
            filas = cur.fetchall()
        total = sum(n for _, n in filas)
        print(f"\n── contexto: `av_agent_items` tiene {total}+ abiertos, y NO todos "
              f"son de ENCONTRÓ ──")
        for tipo, n in filas:
            print(f"  {n:>4}  {tipo}")
        print("  (los avisos dirigidos y los sensores viven en la misma tabla; "
              "la pantalla muestra solo problemas)")
    except Exception as e:
        print(f"\n(no pude leer el contexto de items: {e})")


if __name__ == "__main__":
    main()
