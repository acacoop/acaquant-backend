"""GET /api/manager/logs — últimos N logs de un servicio systemd.

Ejecuta `journalctl -u <servicio>.service -n N --output=json --no-pager`
como subprocess y parsea el output. Usado por la tab LOGS del Manager
en acaquant-web para dashboards operativos.

Seguridad: whitelist estricta de servicios (no es un comando RCE). Lines
bounded a [1, 500].
"""
from __future__ import annotations

import json
import subprocess

from fastapi import APIRouter, HTTPException, Query

from api.cache import cached
from api.services.diagnostico_registry import unidades_motores

router = APIRouter()

# Whitelist de servicios. El handler rechaza cualquier otro nombre para evitar
# que alguien pase "motor_rofex; rm -rf /" o similar.
#
# Los motores se DERIVAN del registro del Diagnóstico (`unidades_motores`) → si
# se agrega/saca un motor, esta whitelist se sincroniza sola (anti-drift). Solo
# los servicios de infra (no-motor) van listados a mano.
_INFRA_SERVICES: set[str] = {"api", "partner_api", "cloudflared"}
_ALLOWED_SERVICES: set[str] = unidades_motores() | _INFRA_SERVICES

# PRIORITY de syslog → label human-friendly
_PRIO_LABEL: dict[int, str] = {
    0: "emerg", 1: "alert", 2: "crit", 3: "error",
    4: "warn",  5: "notice", 6: "info", 7: "debug",
}


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
    """Parsea el output JSON de journalctl. TTL 2s para absorber refresh agresivo.
    servicio=__todos__ → un journalctl con -u por cada servicio permitido
    (mezcla cronológica; _SYSTEMD_UNIT identifica de quién es cada línea)."""
    unidades = sorted(_ALLOWED_SERVICES) if servicio == _TODOS else [servicio]
    cmd = ["journalctl"]
    for u in unidades:
        cmd += ["-u", f"{u}.service"]
    cmd += ["-n", str(lines), "--output=json", "--no-pager"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "journalctl error")

    out: list[dict] = []
    for raw in proc.stdout.strip().splitlines():
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # __REALTIME_TIMESTAMP viene en microsegundos desde epoch (string).
        try:
            ts_us = int(doc.get("__REALTIME_TIMESTAMP", "0"))
        except (TypeError, ValueError):
            ts_us = 0
        try:
            prio = int(doc.get("PRIORITY", 6))
        except (TypeError, ValueError):
            prio = 6
        unit = str(doc.get("_SYSTEMD_UNIT", "")).removesuffix(".service")
        out.append({
            "ts_epoch":  ts_us / 1_000_000,
            "priority":  _PRIO_LABEL.get(prio, "info"),
            "servicio":  unit or servicio,
            "message":   doc.get("MESSAGE", ""),
        })
    return out


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
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail="journalctl timeout (10s)") from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail="journalctl no disponible en el host") from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "servicio": servicio,
        "lines": lines,
        "logs": logs,
    }
