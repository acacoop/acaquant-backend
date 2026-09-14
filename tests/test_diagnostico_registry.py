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
    # EL AGENTE Y LO QUE MIRA AL SISTEMA. No son una pieza de dato de una vista:
    # son el que avisa cuando una pieza se rompe. Meterlos al árbol sería pedirle
    # al árbol que se vigile a sí mismo.
    "jobs.agente_tasa",         # la lista de prioridad del agente contra 1816
    "jobs.eikon_cierres",       # cierres diarios de los feeds Eikon (anchors OFF, sin vista propia)
    "jobs.research_mail",       # ingesta del research diario → ia.research (P6 QuantAI)
    "jobs.mercado_1816_series", # series históricas de 1816 → research.mkt_1816_* (vista RESEARCH)
    "jobs.bcra_research",       # series BCRA v4 → research.bcra_* (tab BCRA de RESEARCH)
    "jobs.fred_research",       # series FRED → research.fred_* (tab DATOS INTERNACIONALES de RESEARCH)
    "jobs.cleanup_curvas",      # limpieza
    "jobs.cleanup_futuros_dlr", # limpieza
    "jobs.cleanup_retencion",   # limpieza (retención/TTL de tablas de log y auditoría)
    "jobs.archive_options_data",  # limpieza (prune intradía de mercado.options_data)
    "jobs.descubrir_cuentas",   # discovery interno
    "jobs.volatilidad_ggal",    # cálculo secundario GGAL
    "jobs.aranceles",           # sub-paso del chain de negocio
    "jobs.fci_bilateral",       # sub-paso del chain de negocio
    "jobs.comercial_warm",      # cache-warming
    "jobs.options_rollup",      # rollup histórico opciones
    "jobs.cleanup_cedears_timesales",  # limpieza (vacía el tape al cierre)
    # MANTENIMIENTO DE CATÁLOGO. Escriben metadata de `portafolio.assets` /
    # `mercado.curvas` (ticker, emisor, vigencia, símbolos), no el dato que la
    # vista muestra: si un día no corren, la pantalla sigue mostrando lo mismo.
    "jobs.assets_autofill",     # completa lo derivable del catálogo de assets
    "jobs.validar_instrumentos",  # vigencia de títulos + símbolos contra Primary
    "jobs.ficha_1816",          # estandariza el EMISOR desde 1816
}


def _motores_reales() -> set[str]:
    return {p.stem for p in SYSTEMD.glob("motor_*.service")}


def _crons_reales() -> set[str]:
    """Los jobs que CORREN de verdad. Una línea comentada no corre.

    ⚠️ Antes se leía el archivo entero de una: un cron COMENTADO seguía contando
    como activo y este test exigía tenerlo en el árbol del Diagnóstico. Se
    descubrió al apagar `custodia_cvsa` (el Droplet no alcanza a BYMA, la
    tenencia entra por la PC de oficina) — nunca había habido un `-m jobs.x`
    comentado, así que el bug estaba latente. Un job apagado no tiene que pedir
    frescura: si la pidiera, el Diagnóstico mostraría en rojo algo que nadie
    espera que corra, y ese rojo permanente entrena a ignorar la pantalla.
    """
    lineas = [ln for ln in CRONTAB.read_text(encoding="utf-8").splitlines()
              if not ln.lstrip().startswith("#")]
    # Captura cada `python -m jobs.x` / `-m engines.x` del crontab.
    return set(re.findall(r"-m\s+((?:jobs|engines)\.[A-Za-z0-9_]+)", "\n".join(lineas)))


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
