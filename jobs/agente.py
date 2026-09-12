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


def _parar(signum, _frame):
    """SIGTERM/SIGINT: termina la pasada en curso y sale limpio. Sin esto, un
    `systemctl restart` mata el proceso a mitad de una escritura."""
    global _seguir
    logger.info("agente: recibí %s — termino la pasada y salgo", signum)
    _seguir = False


def _aplicar_solo() -> None:
    """**EL EJECUTOR**: lo que el agente aplica SOLO, sin que nadie apriete.
    `agente/autonomo.py`.

    ⚠️ **NADA DE ESTO PUEDE TIRAR ABAJO AL AGENTE**, igual que los avisos: el import va adentro del try.
    """
    try:
        from agente import autonomo
        r = autonomo.correr()
        if r["aplicados"] or r["fallidos"]:
            logger.info("agente: SOLO · %d aplicado(s) · %d no cerraron",
                        len(r["aplicados"]), len(r["fallidos"]))
    except Exception as e:
        logger.debug("agente: no apliqué SOLO (%s)", e)


def _redactar_avisos() -> None:
    """**EL TEXTO DE LOS AVISOS**, escrito con la evidencia adelante. §0.dn.

    Vive acá porque es el único lugar que conoce
    las dos mitades. `agente/redactar.py` REDACTA y no sabe que existe una base
    ni un daemon; `agente/registro.py` ESCRIBE y no sabe que existe un modelo.

    Las tres guardas, y ninguna es opcional:

      · el ALCANCE lo pone la query (`arreglo = ''`): sólo avisos
      · `TOPE_POR_PASADA`, techo de plata declarado
      · `MAX_INTENTOS` por hallazgo, para que uno que la validación rechaza
        siempre no se pague eternamente

    ⚠️ **NADA DE ESTO PUEDE TIRAR ABAJO AL AGENTE**, igual que las dos de
    arriba. Y si falla, no hay hueco: el aviso muestra su texto determinista,
    que nunca se borró.
    """
    try:
        from agente import redactar, registro
        if not redactar.encendido():
            return
        filas = registro.pendientes_de_texto(redactar.TOPE_POR_PASADA)
        for f in filas:
            r = redactar.redactar_uno(f)
            registro.guardar_texto_ia(f["id"], texto=r["texto"],
                                      rechazo=r["rechazo"], traza=r["traza"])
            if r["texto"]:
                logger.info("redacté %s/%s «%s» → %s", f["habilidad"], f["regla"],
                            f["sujeto"], r["texto"][:100])
    except Exception as e:
        logger.debug("agente: no redacté avisos (%s)", e)


def _una_pasada() -> dict:
    from agente import motor
    r = motor.tick()
    motor.latir(r)
    # Va DESPUÉS del tick, porque los detectores de esta pasada ya guardaron
    # sus hallazgos.
    _aplicar_solo()
    # Último: el texto es lo que se LEE de un hallazgo, así que se escribe
    # cuando el hallazgo ya está guardado y con su evidencia de esta pasada.
    _redactar_avisos()
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
        # ⚠️ **LA BAJA GANA SOBRE EL ÚLTIMO RESULTADO** (§0.ey). Una habilidad
        # que salió del código conserva su `ok` congelado de la última vez que
        # corrió de verdad: imprimirlo tal cual la mostraba sana, con una fecha
        # de hace una semana, en una lista donde todo lo demás decía hoy. Es el
        # mismo criterio que `saludDe()` en el front — el color no puede salir
        # de un resultado que ya no se actualiza.
        de_baja = not f.get("activa", True)
        print(f"{f['nombre']:22} {f['clase']:8} "
              f"{('BAJA' if de_baja else (f['ultimo_resultado'] or '—')):6} "
              f"{f['corridas_hoy']:>4} {f['hallazgos_abiertos']:>6} "
              f"{f['hallazgos_total']:>6} {f['reincidencias']:>5}  "
              f"{str(ult)[:19] if ult else 'NUNCA'}"
              + ("  ← fuera del catálogo, NO corre (queda por su historia)"
                 if de_baja else "")
              + (f"  ⚠ {f['ultimo_error'][:70]}" if f["ultimo_error"] else ""))
    malas = [f for f in filas
             if f.get("activa", True)
             and f["ultimo_resultado"] in ("error", "sin_datos")]
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
    ap.add_argument("--reredactar", action="store_true",
                    help="borra el texto de IA de los avisos abiertos para que "
                         "se vuelva a escribir (usar al cambiar el prompt o un "
                         "validador; el texto fijo nunca se toca)")
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

    if a.reredactar:
        from agente import registro
        n = registro.borrar_textos_ia(a.skill)
        print(f"listo: {n} aviso(s) vuelven a redactarse (mientras tanto "
              f"muestran su texto fijo)")
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
