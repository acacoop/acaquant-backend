"""smoke_asistente — la primera pregunta real al ASISTENTE DE NEGOCIO (P7).

Reemplaza el curl (REGLA #0: nada de headers/quotes copy-paste en la consola
del Droplet): llama al service EN PROCESO, con la DB y la credencial reales
del .env. Gasta tokens de UNA pregunta.

Correr en el Droplet:
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pregunta", default="¿Cuál es el AuM total administrado hoy?")
    ap.add_argument("--chat-id", default=None)
    ap.add_argument("--email", default=os.getenv("EVAL_EMAIL", "smoke@acaquant.local"))
    args = ap.parse_args()

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
