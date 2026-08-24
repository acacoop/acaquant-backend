"""`jobs/agente.py` — EL AGENTE, como daemon. Doc: `docs/AGENT_2.0.md` §2.

**Reemplaza a los CUATRO relojes del agente viejo**: el daemon del centinela
(30 s), `av_agent_sistema` (cron cada 10 min), `av_agent` (cron 4×/día) y el
detector de seguridad que vivía adentro de `jobs/db_tamano`, que ni siquiera era
un job del agente.

Acá hay uno. La agenda vive en el catálogo: cada habilidad declara su ritmo y su
ventana, y el motor pregunta a quién le toca.

**Por qué daemon y no cron.** El círculo verde dice «el agente está mirando
AHORA». Un cron no puede sostener esa afirmación: entre corrida y corrida no hay
nadie y «prendido» sería una frase sobre el pasado. Un proceso que late sí — y
el círculo se apaga solo cuando el latido envejece, sin que nadie tenga que
acordarse de apagarlo.

    python -m jobs.agente            # daemon
    python -m jobs.agente --una      # una pasada y sale (para probar)
    python -m jobs.agente --skill X  # corre UNA habilidad y muestra qué vio
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agente")

# En rueda mira seguido (los precios cambian); fuera de rueda afloja, porque lo
# único que sigue teniendo sentido de noche son los jobs y las tablas. Bajar el
# ritmo no es ahorro: es no llenar el log de nada 2.880 veces por noche.
CICLO_RUEDA_S, CICLO_QUIETO_S = 30, 300

_seguir = True


def _parar(signum, _frame):
    """SIGTERM/SIGINT: termina la pasada en curso y sale limpio. Sin esto, un
    `systemctl restart` mata el proceso a mitad de una escritura."""
    global _seguir
    logger.info("agente: recibí %s — termino la pasada y salgo", signum)
    _seguir = False


def _una_pasada() -> dict:
    from agente import motor
    r = motor.tick()
    motor.latir(r)
    if r["corridas"]:
        logger.info("agente: %d habilidad(es) · %d nuevos · %d reincidencias · %dms",
                    len(r["corridas"]), r["nuevos"], r["reincidencias"], r["ms"])
    for c in r["corridas"]:
        if c.get("resultado") in ("error", "sin_datos"):
            logger.warning("agente/%s: %s", c.get("habilidad"), c.get("resultado"))
    if r["reincidencias"]:
        logger.error("agente: ⚠ %d REINCIDENCIA(S) — algo que dimos por "
                     "arreglado volvió", r["reincidencias"])
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description="El AV Agent")
    ap.add_argument("--una", action="store_true", help="una pasada y salgo")
    ap.add_argument("--skill", default="", help="corre UNA habilidad")
    ap.add_argument("--sync", action="store_true",
                    help="solo sincroniza el catálogo con el código")
    a = ap.parse_args()

    from agente import catalogo, motor, reloj

    # El catálogo se sincroniza SIEMPRE al arrancar: el código manda sobre qué
    # sabe hacer el agente, la base manda sobre `activa` y los umbrales.
    r = catalogo.sincronizar()
    logger.info("agente: catálogo sincronizado — %d habilidades", r["habilidades"])
    if a.sync:
        return 0

    if a.skill:
        from agente import fuentes
        fuentes.refrescar()
        out = motor.correr_una(a.skill)
        print(out)
        return 0 if out.get("ok", True) else 1

    if a.una:
        print(_una_pasada())
        return 0

    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)
    logger.info("agente: arrancado")
    while _seguir:
        t0 = time.monotonic()
        try:
            _una_pasada()
        except Exception:
            # Una pasada que revienta no puede matar al daemon: el agente existe
            # justo para los momentos en que algo está roto.
            logger.exception("agente: la pasada murió — sigo")
        ciclo = CICLO_RUEDA_S if reloj.en_rueda() else CICLO_QUIETO_S
        dormir = max(1.0, ciclo - (time.monotonic() - t0))
        fin = time.monotonic() + dormir
        while _seguir and time.monotonic() < fin:
            time.sleep(min(1.0, fin - time.monotonic()))
    logger.info("agente: salí limpio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
