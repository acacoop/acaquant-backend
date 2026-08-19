"""jobs/av_agent_centinela.py — EL CENTINELA, como daemon.

Doc madre: **`docs/AV_AGENT.md`** §0.k. La lógica vive en
`api/services/av_agent_centinela`; esto es solo el reloj.

**Por qué daemon y no cron.** El user pidió *«un círculo verde de que está
prendido»*. Un cron cada 5 minutos no puede sostener esa afirmación: entre
corrida y corrida no hay nadie, y «prendido» sería una frase sobre el pasado. Un
proceso que late cada 30 segundos sí — y el círculo se apaga solo cuando el
latido envejece, sin que nadie tenga que acordarse de apagarlo.

**Ritmo doble.** En rueda mira cada 30s (los precios cambian); fuera de rueda
cada 5 minutos, porque lo único que sigue teniendo sentido de noche es SALUD —
un cron que falla a las 22. Bajar el ritmo fuera de rueda no es ahorro: es que
mirar precios quietos 2.880 veces por noche llena el log de nada.

**No termina al cierre**, a diferencia de `control_saldos`: el sistema puede
romperse a cualquier hora y el centinela es lo que se entera.

    python -m jobs.av_agent_centinela          # daemon
    python -m jobs.av_agent_centinela --una    # un ciclo y sale (para probar)
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("centinela")

_seguir = True


def _parar(signum, _frame):
    """SIGTERM/SIGINT: termina el ciclo en curso y sale limpio. Sin esto,
    `systemctl restart` mata el proceso a mitad de una escritura."""
    global _seguir
    logger.info("señal %s — saliendo al terminar el ciclo", signum)
    _seguir = False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--una", action="store_true", help="un solo ciclo y salir")
    args = ap.parse_args()

    from api.services import av_agent_centinela as c

    if args.una:
        r = c.ciclo()
        print(f"{'✅' if r['ok'] else '❌'} ciclo · "
              f"{'EN RUEDA' if r['en_rueda'] else 'cerrado'} · "
              f"{r['abiertos']} abiertos · {r['nuevos']} nuevos · {r['ms']} ms"
              + (f"\n   {r['error']}" if r["error"] else ""))
        est = c.estado(limite=15)
        print(f"\n{est['sin_ver']} sin ver de {len(est['abiertos'])} abiertos:")
        for f in est["abiertos"][:15]:
            marca = "•" if not f["visto_at"] else " "
            print(f"  {marca} [{f['severidad']:<5}] {f['sujeto']:<10} "
                  f"{f['regla']:<26} ×{f['veces']}")
        sys.exit(0 if r["ok"] else 1)

    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)
    logger.info("centinela arriba — %ss en rueda, %ss cerrado",
                c.INTERVALO_RUEDA_S, c.INTERVALO_CERRADO_S)
    while _seguir:
        r = c.ciclo()
        if r["nuevos"]:
            logger.info("%s nuevo/s · %s abiertos · %s ms",
                        r["nuevos"], r["abiertos"], r["ms"])
        elif r["error"]:
            logger.error("ciclo con error: %s", r["error"])
        espera = c.INTERVALO_RUEDA_S if r["en_rueda"] else c.INTERVALO_CERRADO_S
        # Se duerme en tramos cortos para que una señal no espere 5 minutos.
        for _ in range(espera):
            if not _seguir:
                break
            time.sleep(1)
    logger.info("centinela abajo")


if __name__ == "__main__":
    main()
