"""scripts/probar_mensaje.py — mandarte un mensaje a vos mismo, para ver que llega.

Existe por el bug del 2026-08-19: el aviso se creaba «bien», la acción decía
listo, y al destinatario no le llegaba nada. **No había forma barata de probarlo
punta a punta** — había que ir a un control real, elegir a alguien y pedirle que
mirara. Con eso, un bug de entrega tarda días en descubrirse.

Uso:
    python -m scripts.probar_mensaje --para vos@acavalores.com.ar
    python -m scripts.probar_mensaje --para vos@… --ver     # qué tenés abierto
    python -m scripts.probar_mensaje --para vos@… --limpiar # cerrar los de prueba
"""
from __future__ import annotations

import argparse

from api.services import av_agent_mensajes as msg
from api.services import av_agent_vista


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--para", required=True)
    ap.add_argument("--ver", action="store_true")
    ap.add_argument("--limpiar", action="store_true")
    a = ap.parse_args()
    email = a.para.strip().lower()

    if a.limpiar:
        n = 0
        for x in av_agent_vista.avisos_de(email):
            if str(x.get("clave", "")).startswith("prueba"):
                av_agent_vista.resolver_aviso_propio(int(x["id"]), quien=email)
                n += 1
        print(f"\n  {n} mensaje(s) de prueba cerrado(s)\n")
        return 0

    if not a.ver:
        # El tema lleva la hora: así se puede mandar varias veces seguidas y ver
        # cada una. Sin eso, el segundo intento diría «ya estaba» y parecería el
        # mismo bug que vinimos a arreglar.
        from datetime import datetime
        t = datetime.now().strftime("%H:%M:%S")
        r = msg.enviar(
            para=email, tema=f"prueba:{t}",
            asunto=f"Prueba de mensaje del agente ({t})",
            detalle="Si estás viendo esto, la entrega funciona. Cerralo con el "
                    "tilde.\nEste mensaje no significa que haya un problema.",
            donde="—", por="prueba manual")
        print(f"\n  {'✔ mandado' if r.get('creado') else '✖ ' + str(r)}\n")

    abiertos = av_agent_vista.avisos_de(email)
    print(f"  {email} tiene {len(abiertos)} mensaje(s) abierto(s):")
    for x in abiertos[:15]:
        print(f"    · [{x['clave']}] {x['que_hacer'][:70]}")
    print("\n  En la app: la campanita de la barra (MIS AVISOS).\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
