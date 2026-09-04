"""`jobs/agente.py` — EL AGENTE, como daemon. Doc: `docs/AGENT.md` §2.

**Reemplaza a los CUATRO relojes del agente viejo**: el daemon del centinela
(30 s), `av_agent_sistema` (cron cada 10 min), `av_agent` (cron 4×/día) y el
detector de seguridad que vivía adentro de `jobs/db_tamano` —que ni siquiera era
un job del agente—. Los cuatro están borrados; si ves alguno nombrado en un
comentario, es residuo.

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

# El ritmo vive en `agente/motor.py` porque lo necesita el LATIDO para decir
# cuándo vuelve, y `agente/` no puede importar a `jobs/`.
from agente.motor import CICLO_QUIETO_S, CICLO_RUEDA_S  # noqa: E402

_seguir = True


# Quién figura como autor de lo que dispara el triage. **Es una constante y no
# un literal suelto**: el tope diario se cuenta filtrando por este mismo string,
# así que dos grafías distintas harían que el tope no cuente lo que gastó.
POR_EL_TRIAGE = "agente/triage"


def _parar(signum, _frame):
    """SIGTERM/SIGINT: termina la pasada en curso y sale limpio. Sin esto, un
    `systemctl restart` mata el proceso a mitad de una escritura."""
    global _seguir
    logger.info("agente: recibí %s — termino la pasada y salgo", signum)
    _seguir = False


def _atender_investigaciones() -> None:
    """El INVESTIGADOR (`lab/langgraph/`) atiende su cola en un hilo aparte.

    ⚠️ **NADA DE ESTO PUEDE TIRAR ABAJO AL AGENTE.** El agente es el que
    detecta: si el laboratorio no está instalado, o se rompe, o le falta una
    dependencia, la pasada tiene que seguir igual. Por eso el import va adentro
    del try y no arriba del archivo — un `ImportError` al cargar el módulo
    mataría el daemon entero al arrancar, que es exactamente lo contrario de
    lo que este subsistema tiene que garantizar.
    """
    try:
        from lab.langgraph import servicio
        servicio.atender()
    except Exception as e:
        logger.debug("agente: el investigador no atendió (%s)", e)


def _disparar_investigaciones() -> None:
    """**EL TRIAGE**: lo que sigue caído se manda a investigar SOLO. §6.9.

    Vive acá y no en `agente/` a propósito: es el único lugar del sistema que
    conoce las dos mitades. `agente/triage.py` ELIGE (y no sabe que existe un
    investigador); el laboratorio INVESTIGA (y no sabe que existe un agente).
    Si mañana el lab no está, el agente detecta exactamente igual — que es la
    garantía que no se negocia.

    Tres guardas, y ninguna es opcional:

      · el TOPE DIARIO, que es un techo de plata declarado
      · `no_repetir_h`, para no pagar dos veces por la misma pregunta
      · y la de arriba de todo, que la pone `triage.candidatos()`: **el problema
        tiene que haber SOBREVIVIDO su espera**. Nadie está mirando, así que lo
        que se dispara solo tiene que estar más confirmado que lo que se pide a
        mano.

    ⚠️ **NADA DE ESTO PUEDE TIRAR ABAJO AL AGENTE**, igual que `_atender_
    investigaciones`: el import va adentro del try.
    """
    try:
        from agente import triage
        from lab.langgraph import cola
        from lab.langgraph.investigaciones import tipo_de

        candidatos = triage.candidatos()
        if not candidatos:
            return
        gastadas = cola.gastadas_hoy(POR_EL_TRIAGE)
        if gastadas is None:
            logger.warning("triage: no pude contar lo gastado hoy — no disparo. "
                           "Un tope que no se puede contar no es un tope")
            return
        for c in candidatos:
            if gastadas >= triage.TOPE_DIARIO:
                logger.info("triage: llegué al tope de %d por hoy — quedan %d sin "
                            "investigar. Si esto se repite, o sobra presupuesto o "
                            "sobra una regla declarada", triage.TOPE_DIARIO,
                            len(candidatos) - triage.TOPE_DIARIO)
                return
            if not (tipo := tipo_de(c["habilidad"])):
                continue          # el lab no sabe investigar esa habilidad
            r = cola.encolar(tipo, c["sujeto"], POR_EL_TRIAGE,
                             hallazgo_id=c["id"],
                             no_repetir_h=triage.NO_REPETIR_H)
            if r.get("ok") and not r.get("ya_estaba"):
                gastadas += 1
                logger.info("triage: mando a investigar %s/%s «%s» — lleva %s min "
                            "abierto (pedido %s)", c["habilidad"], c["regla"],
                            c["sujeto"], c["min_abierto"], r.get("id"))
    except Exception as e:
        logger.debug("agente: el triage no corrió (%s)", e)


def _una_pasada() -> dict:
    from agente import motor
    r = motor.tick()
    motor.latir(r)
    # Va DESPUÉS del tick: primero el trabajo del agente, después lo demás. Y el
    # triage ANTES de atender la cola, para que lo que se encola en esta pasada
    # se levante en esta pasada y no en la próxima.
    _disparar_investigaciones()
    _atender_investigaciones()
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


def _imprimir_estado() -> None:
    """El catálogo, legible. Contesta la pregunta que ninguna pantalla contestaba
    en el agente viejo: **cuándo miró cada cosa** — que no se puede derivar de
    los hallazgos, porque una corrida que no encontró nada no deja rastro."""
    from agente import catalogo

    filas = catalogo.estado()
    print(f"{'HABILIDAD':22} {'CLASE':8} {'ULT.':6} {'HOY':>4} "
          f"{'ABIERT':>6} {'TOTAL':>6} {'VOLV':>5}  ÚLTIMA CORRIDA")
    for f in sorted(filas, key=lambda x: (x["dominio"], x["nombre"])):
        ult = f["ultima_corrida_at"]
        print(f"{f['nombre']:22} {f['clase']:8} "
              f"{(f['ultimo_resultado'] or '—'):6} "
              f"{f['corridas_hoy']:>4} {f['hallazgos_abiertos']:>6} "
              f"{f['hallazgos_total']:>6} {f['reincidencias']:>5}  "
              f"{str(ult)[:19] if ult else 'NUNCA'}"
              + (f"  ⚠ {f['ultimo_error'][:70]}" if f["ultimo_error"] else ""))
    malas = [f for f in filas if f["ultimo_resultado"] in ("error", "sin_datos")]
    if malas:
        print(f"\n⚠ {len(malas)} habilidad(es) no pudieron mirar — NO cerraron "
              f"nada, que es lo correcto:")
        for f in malas:
            print(f"    {f['nombre']}: {f['ultimo_error'][:120]}")


def main() -> int:
    # Late en operaciones.latidos como cualquier motor (core/latido.py, §0.da):
    # es un daemon de systemd y el agente lo espera por su unit.
    from core import latido
    latido.arrancar()
    ap = argparse.ArgumentParser(description="El AV Agent")
    ap.add_argument("--una", action="store_true", help="una pasada y salgo")
    ap.add_argument("--forzar", action="store_true",
                    help="corre TODAS ahora, sin mirar el ritmo (para probar)")
    ap.add_argument("--skill", default="", help="corre UNA habilidad")
    ap.add_argument("--sync", action="store_true",
                    help="solo sincroniza el catálogo con el código")
    ap.add_argument("--estado", action="store_true",
                    help="qué sabe hacer el agente y cuándo miró cada cosa")
    a = ap.parse_args()

    from agente import catalogo, fuentes, motor, reloj

    # El catálogo se sincroniza SIEMPRE al arrancar: el código manda sobre qué
    # sabe hacer el agente, la base manda sobre `activa` y los umbrales.
    r = catalogo.sincronizar()
    logger.info("agente: catálogo sincronizado — %d habilidades", r["habilidades"])
    if a.sync:
        return 0

    if a.skill:
        fuentes.refrescar()
        out = motor.correr_una(a.skill)
        print(out)
        return 0 if out.get("ok", True) else 1

    if a.estado:
        _imprimir_estado()
        return 0

    # ⚠️ **`--forzar` existe porque `--una` no sirve para PROBAR.** El daemon
    # está corriendo y se lleva las habilidades apenas vencen, así que una
    # pasada a mano casi siempre encuentra cero pendientes y muestra
    # `corridas: []` — que se lee como «el agente no anda» cuando es lo
    # contrario: anda tan bien que no dejó nada.
    if a.forzar:
        fuentes.refrescar()
        for nombre in catalogo.HABILIDADES:
            r = motor.correr_una(nombre)
            estado = r.get("resultado") or ("error" if not r.get("ok") else "?")
            print(f"  {estado:9} {nombre:22} "
                  f"{r.get('abiertos', 0):>4} vistos · "
                  f"{r.get('nuevos', 0):>3} nuevos · "
                  f"{r.get('cerrados', 0):>3} cerrados · "
                  f"{r.get('ms', 0):>6} ms"
                  + (f"  ⚠ {r.get('error', '')[:90]}" if not r.get("ok", True) else ""))
        print()
        _imprimir_estado()
        return 0

    if a.una:
        r = _una_pasada()
        if not r["corridas"]:
            # Decir POR QUÉ no corrió nada. «corridas: []» a secas se lee como
            # una falla, y casi siempre es el daemon haciendo su trabajo.
            print("A ninguna le tocaba todavía (el daemon ya se las llevó, o "
                  "no están en su ventana).\n"
                  "Para probar de verdad: `python -m jobs.agente --forzar`\n")
            _imprimir_estado()
        else:
            print(r)
        return 0

    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)

    # Los pedidos de investigación que quedaron «corriendo» de una vida
    # anterior: el hilo murió con el proceso y en la pantalla se ven como un
    # spinner eterno, que es peor que un error porque no se distingue de «está
    # tardando». Se cierran con el motivo y NO se reencolan solos.
    try:
        from lab.langgraph import servicio
        servicio.recuperar_colgados()
    except Exception as e:
        logger.debug("agente: sin investigador (%s)", e)

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
