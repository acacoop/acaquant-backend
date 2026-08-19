"""api/services/av_agent_relevar.py — LA RELEVADA, a pedido.

Doc madre: **`docs/AV_AGENT.md`** §0.m.

Pedido del user (2026-08-18): *«¿no podemos poner ahí un EJECUTAR DIAGNÓSTICO?
… ¿esto de hace 22 hs para que te pedí recién que se haga? Además, de un sistema
esto debe ejecutar análisis más frecuente. Justamente hay créditos disponibles.
Al menos cada 2 hs»*.

**Y tenía razón por partida doble.** Medido: `jobs.av_agent` **no estaba en el
crontab** — la relevada se venía corriendo a mano, y por eso la pantalla decía
«hace 22 h». No era una decisión de frecuencia: no había frecuencia.

Este módulo resuelve las dos mitades con el MISMO código:

  · el BOTÓN del modal (`POST /av-agent/relevar`), para no tener que entrar al
    Droplet a pedir lo que la pantalla ya está mostrando desactualizado;
  · el CRON cada 2 horas en rueda, que es lo que hace que el botón casi nunca
    haga falta.

**Corre en background.** El censo son ~29 llamadas a 1816 y el plan permite 1 por
segundo: 35 segundos de piso, más los detectores. Ningún request HTTP debería
esperar eso, y el propio agente aborta a los 45s por `_PRESUPUESTO_S`.

**Un LOCK en proceso** evita que dos clicks (o el botón y el cron a la vez)
gasten 58 créditos para escribir lo mismo dos veces. No es un lock distribuido:
la API corre en un solo proceso, y para el caso cron-vs-botón alcanza con que la
corrida sea idempotente — lo es, `persistir` escribe una corrida nueva.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_corriendo: dict = {"si": False, "desde": None, "por": ""}


def corriendo() -> dict:
    """¿Hay una relevada en curso? Lo lee el modal para no ofrecer el botón dos
    veces — y para poder decir «ya está corriendo, esperá» en vez de no hacer
    nada, que se lee como que el botón está roto."""
    return dict(_corriendo)


def _correr(alcance: str, por: str) -> None:
    """El trabajo. **Es el MISMO camino que `jobs/av_agent.py`**: releva,
    persiste y registra las preguntas. Nunca levanta hacia afuera."""
    from api.services import av_agent
    from api.services import av_agent_preguntas as preg
    from core import mercado_1816
    from core.job_runs import JobRunLogger
    from jobs.av_agent import persistir

    saldo_ini = None
    try:
        saldo_ini = (mercado_1816.balance() or {}).get("daily", {}).get("used")
    except Exception:
        pass
    try:
        # Queda en `manager.job_runs` igual que la corrida por cron: una relevada
        # pedida a mano tiene que verse en SALUD como cualquier otra, o el
        # historial de «¿cuándo se miró?» tendría agujeros sin explicación.
        with JobRunLogger("av_agent") as jr:
            jr.log(f"relevada a pedido de {por or 'la vista'} (alcance={alcance})")
            res = av_agent.relevar(alcance=alcance)
            n = persistir(res)
            jr.set_stat("hallazgos", len(res["hallazgos"]))
            jr.set_stat("filas_persistidas", n)
            jr.set_stat("origen", "vista")
            jr.set_stat("universo_fuente", res["universo"].get("fuente", "1816"))
            if not res["universo"]["1816"]:
                jr.error("el censo de 1816 volvió VACÍO — los faltantes de esta "
                         "corrida no son concluyentes")
            try:
                preg.registrar(preg.preguntas_de_hallazgos(res["hallazgos"]))
            except Exception as e:
                jr.error(f"no se pudieron registrar las preguntas: {e}")
            if saldo_ini is not None:
                try:
                    fin = (mercado_1816.balance() or {}).get("daily", {})
                    if (u := fin.get("used")) is not None:
                        jr.set_stat("creditos_1816", u - saldo_ini)
                except Exception:
                    pass
    except Exception:
        logger.exception("relevar: la corrida a pedido murió")
    finally:
        _corriendo.update({"si": False, "desde": None, "por": ""})


def arrancar(*, alcance: str = "soberanos", por: str = "") -> dict:
    """Dispara la relevada y vuelve al instante."""
    from datetime import UTC, datetime

    from core import mercado_1816
    if not mercado_1816.disponible():
        return {"ok": False, "error": "1816 no está configurado — no se puede censar"}
    with _lock:
        if _corriendo["si"]:
            return {"ok": False, "ya_corriendo": True,
                    "error": f"ya hay una relevada en curso (la pidió "
                             f"{_corriendo['por'] or 'el cron'})"}
        _corriendo.update({"si": True, "desde": datetime.now(UTC).isoformat(),
                           "por": por})
    threading.Thread(target=_correr, args=(alcance, por), daemon=True,
                     name="av-relevar").start()
    return {"ok": True, "corriendo": True, "alcance": alcance,
            # La espera, dicha de entrada: son ~29 llamadas a 1 por segundo.
            "segundos_estimados": 60,
            "aviso": "~29 créditos de 1816. La pantalla se actualiza sola cuando "
                     "termina."}
