"""Anti-drift del Diagnóstico: el registro DEBE matchear el sistema real.

Cruza `api/services/diagnostico_registry.py` contra el inventario de verdad
(`deploy/systemd/*.service` + `deploy/crontab.txt`). Si alguien agrega/saca un
motor o un cron y no toca el registro, ESTO FALLA en CI. Es el mismo contrato
que `gen_sistema --check`, aplicado al árbol del Diagnóstico.
"""
from __future__ import annotations

import re
from pathlib import Path

from api.services.diagnostico_registry import unidades_jobs, unidades_motores

ROOT = Path(__file__).resolve().parent.parent
SYSTEMD = ROOT / "deploy" / "systemd"
CRONTAB = ROOT / "deploy" / "crontab.txt"

# Crons que NO representan una pieza de dato de una vista (infra / copias /
# sub-pasos de enriquecimiento). Se excluyen a propósito del árbol del Diagnóstico.
_CRONS_IGNORADOS = {
    "jobs.watchdog",            # watchdog del propio sistema
    "jobs.cleanup_curvas",      # limpieza
    "jobs.cleanup_futuros_dlr", # limpieza
    "jobs.descubrir_cuentas",   # discovery interno
    "jobs.volatilidad_ggal",    # cálculo secundario GGAL
    "jobs.sync_api_copies",     # copia derivada *API
    "jobs.aranceles",           # sub-paso del chain de negocio
    "jobs.fci_bilateral",       # sub-paso del chain de negocio
    "jobs.comercial_warm",      # cache-warming
    "jobs.partner_export",      # proveedor externo (no es vista acaquant)
    "jobs.options_rollup",      # rollup histórico opciones
}


def _motores_reales() -> set[str]:
    return {p.stem for p in SYSTEMD.glob("motor_*.service")}


def _crons_reales() -> set[str]:
    txt = CRONTAB.read_text(encoding="utf-8")
    # Captura cada `python -m jobs.x` / `-m engines.x` del crontab.
    return set(re.findall(r"-m\s+((?:jobs|engines)\.[A-Za-z0-9_]+)", txt))


def test_motores_registro_matchea_systemd():
    reales = _motores_reales()
    registro = unidades_motores()
    faltan_en_registro = reales - registro
    sobran_en_registro = registro - reales
    assert not faltan_en_registro, (
        f"Motores que corren pero NO están en el registro del Diagnóstico: "
        f"{sorted(faltan_en_registro)}. Agregalos a diagnostico_registry.py."
    )
    assert not sobran_en_registro, (
        f"Motores en el registro que ya NO existen como .service: "
        f"{sorted(sobran_en_registro)}. Sacalos de diagnostico_registry.py."
    )


def test_jobs_registro_matchea_crontab():
    reales = _crons_reales()
    registro = unidades_jobs()
    # 1) El registro no referencia crons que no existen.
    fantasma = registro - reales
    assert not fantasma, (
        f"Jobs en el registro que NO están en el crontab: {sorted(fantasma)}."
    )
    # 2) Todo cron real (salvo los ignorados) está cubierto por el árbol.
    sin_cubrir = reales - registro - _CRONS_IGNORADOS
    assert not sin_cubrir, (
        f"Crons que corren pero NO están en el árbol del Diagnóstico: "
        f"{sorted(sin_cubrir)}. Agregalos al registro o a _CRONS_IGNORADOS."
    )
