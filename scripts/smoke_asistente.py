"""smoke_asistente — la primera pregunta real al ASISTENTE DE NEGOCIO (P7).

Reemplaza el curl (REGLA #0: nada de headers/quotes copy-paste en la consola
del Droplet): llama al service EN PROCESO, con la DB y la credencial reales
del .env. Gasta tokens de UNA pregunta.

Correr en el Droplet:
    python -m scripts.smoke_asistente --ruteo          # a dónde va cada tarea (0 tokens)
    python -m scripts.smoke_asistente
    python -m scripts.smoke_asistente --pregunta "¿Cuál es el AuM total hoy?"
    python -m scripts.smoke_asistente --chat-id <id>   # continuar la conversación

Imprime la respuesta + el chat_id (para el turno siguiente) + la traza_id
(para mirarla en OBSERVABILIDAD → IA). Si algo se niega (aduana sin catálogo,
presupuesto, credencial), lo dice claro.
"""
from __future__ import annotations

import argparse
import os


def _ruteo() -> None:
    """Qué proveedor/modelo usa cada tarea y si su credencial está puesta.
    Cero tokens — es la verificación de que el ruteo de privacidad quedó bien."""
    from core import ai, llm

    print(f"{'tarea':<24} {'proveedor':<10} {'modelo':<22} {'no entrena':<11} credencial")
    print("-" * 84)
    for tarea in sorted(ai._TAREAS):
        cfg = ai._config(tarea)
        prov = ai._proveedor(cfg)
        print(f"{tarea:<24} {prov:<10} {ai._modelo(cfg):<22} "
              f"{('sí' if llm.no_entrena(prov) else 'NO'):<11} "
              f"{'OK' if llm.configurado(prov) else 'FALTA'}")
    print("\nLa tarea del asistente de negocio debe ir a un proveedor con "
          "'no entrena = sí'.\nSi su credencial dice FALTA, el asistente se "
          "apaga (NO cae al otro proveedor).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ruteo", action="store_true",
                    help="mostrar a qué proveedor va cada tarea (no gasta tokens)")
    ap.add_argument("--pregunta", default="¿Cuál es el AuM total administrado hoy?")
    ap.add_argument("--chat-id", default=None)
    ap.add_argument("--email", default=os.getenv("EVAL_EMAIL", "smoke@acaquant.local"))
    args = ap.parse_args()

    if args.ruteo:
        _ruteo()
        return

    from api.services import asistente

    r = asistente.responder(mensaje=args.pregunta, email=args.email, chat_id=args.chat_id)
    if not r.get("ok"):
        print(f"NO respondió — motivo: {r.get('motivo')} · {r.get('mensaje')}")
        raise SystemExit(1)
    print(f"chat_id : {r['chat_id']}")
    print(f"traza_id: {r.get('traza_id')}")
    print(f"\n{r['respuesta']}")


if __name__ == "__main__":
    main()
