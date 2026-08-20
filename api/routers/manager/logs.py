"""GET /api/manager/logs — últimos N logs de un servicio systemd.

Ejecuta `journalctl -u <servicio>.service -n N --output=json --no-pager`
como subprocess y parsea el output. Usado por la tab LOGS del Manager
en acaquant-web para dashboards operativos.

Seguridad: whitelist estricta de servicios (no es un comando RCE). Lines
bounded a [1, 500].
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.cache import cached
from api.services import logs_sistema
from api.services.diagnostico_registry import unidades_motores

router = APIRouter()

# Whitelist de servicios. El handler rechaza cualquier otro nombre para evitar
# que alguien pase "motor_rofex; rm -rf /" o similar.
#
# Los motores se DERIVAN del registro del Diagnóstico (`unidades_motores`) → si
# se agrega/saca un motor, esta whitelist se sincroniza sola (anti-drift). Solo
# los servicios de infra (no-motor) van listados a mano.
_INFRA_SERVICES: set[str] = {"api", "cloudflared"}
_ALLOWED_SERVICES: set[str] = unidades_motores() | _INFRA_SERVICES



@router.get("/logs/services")
def list_services() -> list[str]:
    """Servicios disponibles para ver logs (motores derivados del registro + infra).

    El front lo consume para armar el dropdown → no se desfasa al agregar un motor.
    """
    return sorted(_ALLOWED_SERVICES)


# Valor especial: logs de TODOS los servicios, mezclados cronológicamente en
# UNA sola llamada a journalctl (varios -u). Cada línea sale con su `servicio`.
_TODOS = "__todos__"


@cached(ttl=2)
def _fetch_logs_cached(servicio: str, lines: int) -> list[dict]:
    """Las últimas N líneas, con TTL 2s para absorber el refresh de la pantalla.

    ⚠️ **La lectura NO vive acá**: la hace `api/services/logs_sistema`, que
    también usa el AV AGENT para vigilar los motores. Cuando estaba adentro de
    este router el agente no podía llegar (un service no importa un router) y la
    única alternativa era copiar el `subprocess` — dos formas de leer lo mismo
    que se separan solas (REGLA #9).

    `desde=None` y `prioridad=7` mantienen EXACTAMENTE lo que hacía antes: el
    final del archivo, sin filtro de tiempo ni de nivel. La pantalla muestra
    todo; el que filtra es el agente.
    """
    unidades = sorted(_ALLOWED_SERVICES) if servicio == _TODOS else [servicio]
    r = logs_sistema.leer(unidades, desde=None, prioridad=7, lineas=lines)
    if not r["disponible"]:
        raise RuntimeError(r["motivo"] or "journalctl error")
    # Se mantienen los nombres de campo que ya consume el front.
    return [{"ts_epoch": x["ts"], "priority": x["nivel"],
             "servicio": x["unidad"] or servicio, "message": x["mensaje"]}
            for x in r["lineas"]]


@router.get("/logs")
def get_service_logs(
    servicio: str = Query(..., description=f"Servicio (o {_TODOS}). Válidos: {sorted(_ALLOWED_SERVICES)}"),
    lines: int = Query(20, ge=1, le=500, description="Últimas N líneas"),
):
    """Devuelve los últimos N logs del servicio indicado, o de TODOS mezclados."""
    if servicio != _TODOS and servicio not in _ALLOWED_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"servicio '{servicio}' no permitido",
        )
    try:
        # kwargs explícitos: el decorador @cached (api/cache.py) solo acepta
        # kwargs — si se invoca posicionalmente tira TypeError y el handler
        # responde 500 genérico.
        logs = _fetch_logs_cached(servicio=servicio, lines=lines)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "servicio": servicio,
        "lines": lines,
        "logs": logs,
    }
