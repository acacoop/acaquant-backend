"""gen_evals_desde_flags — cierra el loop de calidad: DETECTAR → PREVENIR.

El loop (`jobs/ia_calidad.py`) marca las conversaciones malas en
`ia.calidad_flags`. Este script las convierte en BORRADORES de casos de eval,
para que cada falla real quede como test de regresión y no vuelva. Así el
asistente mejora de forma ACUMULATIVA: cada bug que encontramos queda blindado.

Por qué SEMI-automático (no full): un flag es un juicio difuso ("el ranking
salió desordenado"); el test es determinista (regex que la respuesta DEBE / NO
debe matchear). Generar el regex a ciegas produciría tests basura. Entonces el
script hace lo tedioso —traer las fallas reales, pre-armar el caso con la
pregunta REAL y un hint por modo de falla— y VOS afinás el patrón antes de
moverlo al set vivo. Máquina en lo mecánico, humano en el juicio.

Flujo:
    python -m scripts.gen_evals_desde_flags            # borrador de las flags sin revisar
    python -m scripts.gen_evals_desde_flags --dias 7   # ventana
    python -m scripts.gen_evals_desde_flags --marcar-revisadas   # marca las incluidas

Salida: evals/_borrador_desde_flags.json. Revisás cada caso, afinás
esperado_any/prohibido, y lo movés a evals/asistente.json (asistente) o
evals/copiloto_vista.json (copiloto). El borrador NO se commitea — es scratch.
"""
from __future__ import annotations

import argparse
import json
import os

from psycopg.rows import dict_row

from core.postgres import get_pool

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALIDA = os.path.join(RAIZ, "evals", "_borrador_desde_flags.json")

# Hint por modo de falla: qué patrón determinista suele cerrar ese modo. NO es
# el test final — es el punto de partida que el humano afina.
_HINT = {
    "ranking_a_mano":
        {"esperado_any": ["<el ranking, ya ordenado por código>"],
         "prohibido": [],
         "hint": "Mejor un TEST DE CÓDIGO sobre la tool de ranking (que ordene "
                 "bien), no un eval del modelo — el orden lo hace el código."},
    "deflexion":
        {"esperado_any": ["<la lectura concreta que el usuario pidió>"],
         "prohibido": [r"\?\s*$"],
         "hint": "El usuario ya dio el objetivo: la respuesta ENTREGA, no cierra "
                 "con otra pregunta. Ajustá 'esperado_any' al dato que debía dar."},
    "causalidad":
        {"esperado_any": [],
         "prohibido": ["así que", "por eso", "lo que (?:indica|sugiere) que"],
         "hint": "No encadenar métricas independientes. Revisá que el 'prohibido' "
                 "no pise un uso legítimo."},
    "tool_muda":
        {"esperado_any": ["<el dato que SÍ debería poder dar>"],
         "prohibido": ["sin datos", "no lo tengo", "lo dejo planteado"],
         "hint": "Si el dato existe, la respuesta lo da. Si de verdad no está, "
                 "esto NO es un caso de eval — descartalo."},
    "dato_equivocado":
        {"esperado_any": ["<el dato CORRECTO que se pidió>"],
         "prohibido": ["<el dato que dio de más, el equivocado>"],
         "hint": "Cada pregunta tiene SU dato. Poné en 'esperado' el correcto y "
                 "en 'prohibido' el que confundió."},
    "voto_negativo":
        {"esperado_any": [], "prohibido": [],
         "hint": "👎 del usuario — leé la traza entera para entender qué falló y "
                 "armá el patrón a mano."},
    "otro":
        {"esperado_any": [], "prohibido": [],
         "hint": "Modo no clasificado — leé la traza y definí el patrón."},
}


def _leer_flags(dias: int) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT f.traza_id, f.tarea, f.modo, f.severidad, f.nota, f.ts, "
            "       t.detalle AS pregunta, t.respuesta "
            "FROM ia.calidad_flags f JOIN ia.trazas t ON t.id = f.traza_id "
            "WHERE f.revisado = false AND f.ts > now() - make_interval(days => %s) "
            "ORDER BY f.severidad, f.ts DESC",
            (dias,))
        return cur.fetchall()


def _caso(f: dict) -> dict:
    h = _HINT.get(f["modo"], _HINT["otro"])
    return {
        "id": f"flag_{f['traza_id']}_{f['modo']}",
        "origen": f"calidad {str(f['ts'])[:10]} · {f['modo']}/{f['severidad']} · {f['nota']}",
        "pregunta": (f.get("pregunta") or "").strip(),
        "esperado_any": h["esperado_any"],
        "prohibido": h["prohibido"],
        "_REVISAR": h["hint"],
        "_respuesta_original": (f.get("respuesta") or "").strip()[:400],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=14, help="ventana de flags (default 14)")
    ap.add_argument("--marcar-revisadas", action="store_true",
                    help="marca las flags incluidas como revisadas (no se re-proponen)")
    args = ap.parse_args()

    flags = _leer_flags(args.dias)
    if not flags:
        print("no hay flags sin revisar en la ventana. Nada que hacer.")
        return

    # separar por destino: asistente_negocio → asistente.json; copiloto_* → copiloto
    negocio = [f for f in flags if f["tarea"] == "asistente_negocio"]
    copiloto = [f for f in flags if f["tarea"] != "asistente_negocio"]
    borrador = {
        "_doc": ("BORRADOR auto-generado desde ia.calidad_flags — NO commitear. "
                 "Revisá cada caso, afiná esperado_any/prohibido (mirá _REVISAR y "
                 "_respuesta_original), borrá los que no apliquen, y movelos al set "
                 "vivo: los de 'asistente' a evals/asistente.json, los de 'copiloto' "
                 "a evals/copiloto_vista.json. Después: --marcar-revisadas."),
        "asistente": [_caso(f) for f in negocio],
        "copiloto": [_caso(f) for f in copiloto],
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as fh:
        json.dump(borrador, fh, ensure_ascii=False, indent=2)

    print(f"{len(flags)} flags → {len(negocio)} asistente · {len(copiloto)} copiloto")
    print(f"borrador: {os.path.relpath(SALIDA, RAIZ)}")
    print("Revisá los casos (cada uno trae _REVISAR con el hint y la respuesta "
          "original), afiná los patrones, y movelos al set vivo.")

    if args.marcar_revisadas:
        ids = [f["traza_id"] for f in flags]
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE ia.calidad_flags SET revisado = true WHERE traza_id = ANY(%s)",
                        (ids,))
        print(f"marcadas {len(ids)} flags como revisadas.")


if __name__ == "__main__":
    main()
