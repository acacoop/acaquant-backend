"""scripts/agente_reset.py — BORRAR LO QUE EL AGENTE ENCONTRÓ Y EMPEZAR DE CERO.

Pedido del user (2026-08-24): *«lo de ENCONTRÓ me da a que hay demasiados bugs
en todo el proceso, por eso quiero borrar todo y que vaya apareciendo lo nuevo…
no es que quiero borrar texto, quiero que desaparezca de verdad y se vaya
repoblando con cosas nuevas»*.

Y es una decisión defendible: el estado acumulado lo produjeron detectores con
bugs, así que no es una base confiable para nada. Lo que se detecta se vuelve a
detectar; arrastrar una lista sucia solo esconde si el arreglo funcionó.

⚠️⚠️ **BORRAR NO ARREGLA. Si corrés esto ANTES de arreglar los detectores, en
diez minutos tenés la misma lista** — los mismos duplicados, las mismas fechas
congeladas— pero sin la antigüedad. El borrado rinde DESPUÉS del fix.

LOS TRES GRUPOS, Y POR QUÉ NO SON LO MISMO
==========================================

**No todo lo que hay en esas tablas «se repuebla».** Ésa es la única distinción
que importa acá, y mezclarla es la diferencia entre empezar de cero y perder
información que nadie va a poder reconstruir:

    hallado    lo que un detector VIO. Se repuebla solo en la próxima pasada.
               Borrarlo no pierde nada.
    decidido   lo que VOS decidiste: los votos del eval set, lo que ignoraste,
               el libro de lo que el agente aplicó. NO se repuebla — nadie lo
               puede volver a deducir. Borrarlo es olvidar, no reiniciar.
    dicho      lo que el agente comunicó: avisos, preguntas, informes.

⚠️ **Los VOTOS son la compuerta de autonomía.** `av_agent_evals` es lo que
habilita cada paso de «el agente puede hacer esto solo». Están indexados por
(sujeto, causa), así que **cuando el hallazgo reaparezca el voto se reengancha
solo** y no te lo vuelve a preguntar. Borrarlos atrasa ese programa semanas y
no limpia ni una fila de ENCONTRÓ. Por eso `--todo` NO los toca: hay que pedirlos
por nombre con `--votos`.

    python -m scripts.agente_reset                 # DRY-RUN: cuenta y no borra
    python -m scripts.agente_reset --aplicar       # borra lo HALLADO
    python -m scripts.agente_reset --aplicar --todo        # + lo decidido y lo dicho
    python -m scripts.agente_reset --aplicar --votos       # + el eval set (pensalo)
"""
from __future__ import annotations

import sys

from core.postgres import get_pool

# ── QUÉ SE BORRA, Y QUÉ SIGNIFICA BORRARLO ──────────────────────────────────
#
# Cada fila declara su MOTIVO en castellano. No es decoración: es lo que se
# imprime antes de ejecutar, y es la única forma de que el que aprieta enter
# sepa qué está perdiendo. Una lista de tablas sin esa columna es un `DELETE`
# a ciegas con buena presentación.

HALLADO: tuple[tuple[str, str], ...] = (
    ("agente.av_agent_items",
     "⭐ los OBJETOS — lo que ves en ENCONTRÓ, AHORA y VIGILANCIA. "
     "Se pierde la antigüedad («lleva 11 días»), el «volvió» y el «ya lo "
     "atendiste». Todo lo que siga pasando nace hoy, nuevo."),
    ("agente.av_agent_hallazgos",
     "la FOTO por corrida. Se rehace en la próxima pasada (5 min en rueda)."),
    ("agente.av_agent_centinela",
     "la tabla que la Fase 3 retiró: nadie la lee ni la escribe. Se limpia "
     "para que no quede confundiendo al que mire la base."),
    ("agente.av_agent_seguimiento",
     "el medidor viejo de «¿aguantó?», retirado en la Fase 2."),
    ("agente.av_agent_evaluado",
     "cuándo se pudo mirar cada tipo. Se reescribe en la primera pasada."),
    ("manager.controles_datos",
     "el estado de los controles de SALUD — de acá salen el «×599» y el «7d» "
     "que ves en las filas. Se recalcula en la próxima corrida horaria."),
    ("manager.salud_eventos",
     "las transiciones de SALUD (cuándo cada chequeo cambió de estado)."),
    ("manager.salud_diagnosticos",
     "cache de diagnósticos. Se rehace al pedirlos."),
    ("manager.superficie_dia",
     "la foto de endpoints de hoy y ayer. La rehace el job nocturno — hasta "
     "entonces el chequeo de permisos no puede comparar contra ayer."),
    ("manager.tabla_perfil",
     "el ritmo medido de cada tabla. Lo rehace el barrido de las 23:30; hasta "
     "entonces `tabla_quieta` no tiene contra qué comparar."),
)

DECIDIDO: tuple[tuple[str, str], ...] = (
    ("agente.av_agent_ignorados",
     "lo que mandaste a callar. Borrarlo hace que TODO lo silenciado vuelva."),
    ("agente.av_agent_acciones",
     "⚠️ el LIBRO de lo que el agente aplicó — qué tocó, dónde y cuándo. Es "
     "auditoría: no se puede reconstruir de ningún lado."),
    ("agente.av_agent_propuestas",
     "las propuestas y su resultado."),
    ("agente.av_agent_lecciones",
     "lo que el agente aprendió de los votos."),
    ("manager.salud_config",
     "qué chequeos silenciaste para que no abran el modal."),
    ("manager.salud_vistos",
     "qué transiciones ya viste."),
)

DICHO: tuple[tuple[str, str], ...] = (
    ("agente.av_agent_avisos", "los avisos dirigidos a personas."),
    ("agente.av_agent_aviso_items",
     "los renglones de cada aviso: el detalle de qué se le pidió a quién."),
    ("agente.av_agent_preguntas", "lo que el agente preguntó y no se respondió."),
    ("agente.av_agent_runs", "las corridas del diagnóstico masivo."),
    ("agente.av_agent_trazas", "las trazas de razonamiento."),
    ("agente.av_agent_errores",
     "las traducciones de log a castellano. Se rehacen, pero cada una cuesta "
     "una llamada al modelo."),
)

VOTOS: tuple[tuple[str, str], ...] = (
    ("agente.av_agent_evals",
     "⚠️⚠️ EL EVAL SET — los votos que habilitan cada paso de autonomía. Están "
     "por (sujeto, causa), así que si el hallazgo reaparece el voto se "
     "reengancha SOLO y no te lo vuelve a preguntar. Borrarlos NO limpia una "
     "sola fila de ENCONTRÓ y atrasa el programa de autonomía semanas."),
)

# ⚠️ **LO QUE ESTE SCRIPT NO PUEDE TOCAR, NUNCA.** No es una lista de cortesía:
# `av_agent_control` guarda la PARADA DE EMERGENCIA. Borrarla dejaría al agente
# habilitado para aplicar cambios sin que nadie lo haya decidido — o sea, lo
# contrario de lo que un reset debería garantizar.
JAMAS = ("agente.av_agent_control", "agente.av_agent_latido")


def _contar(tablas) -> list[tuple[str, str, int]]:
    out = []
    with get_pool().connection() as conn, conn.cursor() as cur:
        for tabla, motivo in tablas:
            try:
                cur.execute(f"SELECT count(*) FROM {tabla}")
                out.append((tabla, motivo, int(cur.fetchone()[0])))
            except Exception as e:
                # Una tabla que no existe no es un error: el schema del repo va
                # adelante de la base a veces. Se dice y se sigue.
                out.append((tabla, motivo, -1))
                print(f"  (no pude leer {tabla}: {str(e)[:60]})")
    return out


def _mostrar(titulo: str, filas: list[tuple[str, str, int]]) -> int:
    print(f"\n{'═' * 74}\n{titulo}\n{'═' * 74}")
    total = 0
    for tabla, motivo, n in filas:
        if n < 0:
            print(f"  {'—':>9}  {tabla}   (no existe en la base)")
            continue
        total += n
        print(f"  {n:>9,}  {tabla}")
        for linea in _envolver(motivo, 62):
            print(f"             {linea}")
    print(f"  {'─' * 9}\n  {total:>9,}  filas en total")
    return total


def _envolver(txt: str, ancho: int) -> list[str]:
    import textwrap
    return textwrap.wrap(txt, ancho) or [""]


def _borrar(filas: list[tuple[str, str, int]]) -> int:
    """`DELETE` liso, sin lotes: son tablas del agente, la más grande está en
    el orden de los miles. El batcheo de la REGLA #4 es para los backfills que
    escanean tablas de producción — acá sería ceremonia."""
    n = 0
    with get_pool().connection() as conn, conn.cursor() as cur:
        for tabla, _, cuantas in filas:
            if cuantas <= 0:
                continue
            cur.execute(f"DELETE FROM {tabla}")
            print(f"  borradas {cur.rowcount:>8,} de {tabla}")
            n += cur.rowcount
        conn.commit()
    return n


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    grupos: list[tuple[str, tuple]] = [("LO HALLADO — se repuebla solo", HALLADO)]
    if "--todo" in sys.argv:
        grupos += [("LO DECIDIDO — no se repuebla, se pierde", DECIDIDO),
                   ("LO DICHO — comunicaciones del agente", DICHO)]
    if "--votos" in sys.argv:
        grupos.append(("EL EVAL SET — pensalo dos veces", VOTOS))

    print("RESET DEL AGENTE" + ("" if aplicar else "  ·  DRY-RUN (no borra nada)"))
    print("lo que NO se toca: " + " · ".join(JAMAS)
          + "  (ahí vive la parada de emergencia)")

    contados, total = [], 0
    for titulo, tablas in grupos:
        filas = _contar(tablas)
        total += _mostrar(titulo, filas)
        contados.append(filas)

    if not aplicar:
        print(f"\n→ {total:,} filas se borrarían. Nada se tocó.")
        print("  Para hacerlo:  python -m scripts.agente_reset --aplicar"
              + ("" if "--todo" in sys.argv else "   (sumá --todo para los otros grupos)"))
        return 0

    print(f"\n{'═' * 74}\nBORRANDO {total:,} filas\n{'═' * 74}")
    borradas = sum(_borrar(f) for f in contados)
    print(f"\n✔ {borradas:,} filas borradas.")
    print("\nQué esperar ahora:")
    print("  · el centinela repuebla AHORA y VIGILANCIA en ≤5 min (si hay rueda)")
    print("  · `jobs.av_agent_sistema` repuebla lo del sistema en ≤10 min")
    print("  · la relevada de bonos entra en su próxima corrida (13/15/17/19 UTC)")
    print("  · `manager.tabla_perfil` y la superficie se rehacen a las 23:30 UTC")
    print("  · TODO lo que reaparezca nace con fecha de HOY: la antigüedad "
          "arranca de cero y eso es lo que pediste")
    print("\n  Para ver cómo quedó:  python -m scripts.diag_ahora")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
