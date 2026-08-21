"""diag_encontro — TODO lo abierto en ENCONTRÓ, clasificado por QUÉ HACE FALTA.

    python -m scripts.diag_encontro           # el censo completo
    python -m scripts.diag_encontro --regla moneda_flujo_contradice

Read-only. Cero escrituras, cero llamadas a 1816.

⚠️ **PARA QUÉ EXISTE.** El user (2026-08-22): *«no quiero más nada en ENCONTRÓ
de acá al lunes, quiero solucionar de una vez por todas esto»*. Para eso hay que
contestar primero una pregunta que hoy no se puede contestar mirando la
pantalla: **de los N abiertos, ¿cuántos son de cada clase?** Sin eso, «vaciar
ENCONTRÓ» es una lista de 98 filas y no un plan.

Las cinco clases NO son severidad — son **qué trabajo hace falta**, que es lo
único que decide en qué orden se atacan:

    AUTO_CERRABLE   el agente ya PROBÓ que está bien / que no aplica.
                    No hay trabajo: hay que dejar de mostrarlo.
    TIENE_PUERTA    existe una acción que lo arregla. Si igual aparece como
                    «sin puerta» en algún lado, es un bug de RUTEO, no deuda.
    RUIDO_ESTRUCTURAL  no puede dejar de aparecer nunca por cómo está definido
                    el detector (una tabla que por diseño no se escribe).
                    Se arregla en el DETECTOR, no caso por caso.
    FALTA_ACCION    problema real, repetido, y nadie escribió el arreglo.
                    **Ordenado por VOLUMEN**: es la deuda que más ruido hace.
    HUMANO          necesita criterio de la mesa. Es el único resto legítimo.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

from core.postgres import get_pool

# Tablas cuyo `sin_escribir` NO es un problema: por diseño no se escriben solas.
# Se listan para MEDIR cuántas son, no para esconderlas — el arreglo va en el
# detector, y este número dice si vale la pena.
_QUIETAS_ESPERADAS = (
    "realtime.schema_migrations",     # interna de Supabase, nunca la tocamos
    "portafolio.assets",              # catálogo: cambia cuando la mesa edita
    "manager.salud_eventos",          # solo escribe en una TRANSICIÓN
    "operaciones.ordenes_live",       # solo si hay órdenes ese día
    "operaciones.ordenes_audit",
    "operaciones.tesoreria_cheques",
    "mercado.camara_cereales_audit",
)


def _clasificar(h: dict, con_accion: set[str]) -> tuple[str, str]:
    """(clase, por_qué). El orden de los `if` ES la prioridad."""
    tipo, regla = h["tipo"], h["regla"]
    sujeto = (h["sujeto"] or "").strip()

    if h["estado"] in ("resuelto", "ignorado"):
        return "AUTO_CERRABLE", f"ya está en estado «{h['estado']}»"
    if h.get("es_ruido"):
        return "AUTO_CERRABLE", "una persona lo marcó «es ruido» y sigue abierto"
    if regla == "tabla_nueva":
        return "RUIDO_ESTRUCTURAL", ("«apareció una tabla» informa UNA vez; "
                                     "abierto para siempre es ruido")
    if regla in ("sin_escribir", "tabla_quieta") and sujeto in _QUIETAS_ESPERADAS:
        return "RUIDO_ESTRUCTURAL", "por diseño esa tabla no se escribe sola"
    if regla in con_accion:
        return "TIENE_PUERTA", f"la acción «{con_accion and regla}» ya existe"
    if tipo in ("motor_ruidoso", "motor_caido", "proveedor_caido"):
        return "HUMANO", "infraestructura: se mira, no se arregla desde acá"
    return "FALTA_ACCION", "nadie escribió el arreglo todavía"


def main() -> None:
    filtro = ""
    if "--regla" in sys.argv:
        filtro = sys.argv[sys.argv.index("--regla") + 1]

    from api.services import av_agent
    from api.services.av_agent_hacer import ACCIONES

    # Qué reglas TIENEN una acción, derivado de los registros reales.
    con_accion = {r for r in av_agent.ACCION_POR_REGLA}
    con_accion |= {a.causa for a in ACCIONES.values()
                   if getattr(a, "causa", None)}

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT clave, tipo, origen, sujeto, regla, estado, severidad, "
            "       veces, abierto_at::text, titulo "
            "  FROM mercado.av_agent_items "
            " WHERE estado NOT IN ('resuelto','ignorado') "
            " ORDER BY regla, sujeto")
        cols = [d[0] for d in cur.description]
        items = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT caso, causa FROM mercado.av_agent_evals "
                    "WHERE NOT acierta AND origen = 'utilidad'")
        ruido = {(a, b) for a, b in cur.fetchall()}

    for h in items:
        h["es_ruido"] = ((h["sujeto"] or "").strip(), (h["regla"] or "").strip()) in ruido

    if filtro:
        items = [h for h in items if h["regla"] == filtro]

    por_clase: dict[str, list] = defaultdict(list)
    for h in items:
        clase, porque = _clasificar(h, con_accion)
        por_clase[clase].append((h, porque))

    print(f"\n{'=' * 74}\nABIERTOS EN ENCONTRÓ: {len(items)}\n{'=' * 74}")
    for clase in ("AUTO_CERRABLE", "RUIDO_ESTRUCTURAL", "TIENE_PUERTA",
                  "FALTA_ACCION", "HUMANO"):
        filas = por_clase.get(clase) or []
        print(f"\n── {clase}  ({len(filas)}) " + "─" * (50 - len(clase)))
        if not filas:
            continue
        # Por REGLA y por VOLUMEN: arreglar la que sale 21 veces vale más que
        # la que sale una, y eso es un número y no una corazonada.
        cuenta = Counter(h["regla"] for h, _ in filas)
        for regla, n in cuenta.most_common():
            ejemplos = [h["sujeto"] for h, _ in filas if h["regla"] == regla][:6]
            porque = next(p for h, p in filas if h["regla"] == regla)
            print(f"  {n:>4}  {regla:<28} {porque}")
            print(f"        {' · '.join(str(e)[:22] for e in ejemplos)}"
                  + (" …" if n > 6 else ""))

    # ── EL PLAN, en una línea por clase ─────────────────────────────────────
    print(f"\n{'=' * 74}\nQUÉ HACER CON CADA PILA\n{'=' * 74}")
    print(f"  {len(por_clase['AUTO_CERRABLE']):>4}  cerrarlos: el agente ya probó "
          f"que no hay nada que hacer")
    print(f"  {len(por_clase['RUIDO_ESTRUCTURAL']):>4}  arreglar el DETECTOR "
          f"(no el caso): no pueden dejar de aparecer")
    print(f"  {len(por_clase['TIENE_PUERTA']):>4}  ya tienen acción → si salen "
          f"«sin puerta» es un bug de RUTEO")
    print(f"  {len(por_clase['FALTA_ACCION']):>4}  DEUDA: escribir el arreglo, "
          f"empezando por la regla más repetida")
    print(f"  {len(por_clase['HUMANO']):>4}  criterio de la mesa — el único resto "
          f"legítimo")


if __name__ == "__main__":
    main()
