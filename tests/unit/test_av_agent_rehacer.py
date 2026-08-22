"""REHACER EL DÍA — pero solo si el dato falta DE VERDAD.

El 2026-08-20 Aunesa devolvió HTTP 500 a las 11:00, `jobs/aum` murió y
`portafolio.tenencia` se quedó sin el día. El AuM, la Tenencia Valorizada y
Títulos en Alquiler mostraron el día anterior **sin ningún cartel**.

La condición que hace seguro al botón la puso el user:

    *«ejecutar fecha de hoy por haber detectado un error Y haber verificado
    100% en la base que no hay fecha realmente»*

    el job falló     → una señal del PROCESO. Puede fallar y haber escrito.
    el dato no está  → un hecho sobre el RESULTADO. Es lo único que importa.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

from api.services import av_agent_rehacer as reh

_JOB = "portafolio_diario"


# ── el catálogo: sin tabla y sin columna no se puede exigir nada ─────────────

def test_todo_job_rehacible_declara_COMO_se_comprueba():
    for job, cfg in reh.REHACIBLES.items():
        for k in ("titulo", "label", "timeout", "comando", "tabla", "columna",
                  "rompe", "dia"):
            assert cfg.get(k), f"«{job}» no declara «{k}»"


def test_ningun_rehacible_es_un_MOTOR():
    """Un motor en rueda le corta el feed de precios a la mesa (regla del user,
    2026-08-18) y eso no se decide desde un botón."""
    for job, cfg in reh.REHACIBLES.items():
        assert "engines." not in cfg["comando"], job
        assert "systemctl" not in cfg["comando"], job


def test_el_comando_va_por_run_job_sh():
    """Lock + timeout, el mismo que usa el cron (REGLA #4): si la corrida
    anterior sigue viva, esta se saltea sola en vez de apilarse."""
    assert reh.RUN_JOB.endswith("run_job.sh")


# ── qué DÍA le toca: no es hoy ───────────────────────────────────────────────

def test_el_dia_objetivo_es_el_habil_ANTERIOR_no_hoy():
    """`--diario` snapshotea el cierre de AYER. Exigirle el día de hoy lo daría
    por faltante todas las noches, y un detector que grita siempre enseña a
    ignorar la lista entera."""
    f = date.fromisoformat(reh.fecha_objetivo(_JOB))
    from core.calendario import es_habil
    hoy = date.today()
    assert f < hoy
    assert es_habil(f), "el objetivo tiene que ser un día hábil"
    assert (hoy - f).days <= 5, "no puede irse cinco días para atrás"


def test_la_fecha_esperada_depende_de_CUANDO_corrio_el_job_no_de_hoy():
    """§0.cq — la alerta falsa del sábado. El cron es L-V a las 11 UTC y cada
    corrida escribe el hábil ANTERIOR a sí misma. Medido en prod
    (`diag_tenencia_fechas`): el máximo un sábado es el JUEVES — el viernes
    recién se escribe el lunes, y exigirlo el sábado era gritar en falso."""
    from datetime import datetime

    # Sábado 22/08/2026 → última corrida: viernes 21 → escribió el jueves 20.
    assert reh.fecha_objetivo(_JOB, datetime(2026, 8, 22, 15, 0)) == "2026-08-20"
    # Lunes 24/08 a las 9 UTC (el cron de hoy TODAVÍA no corrió) → ídem.
    assert reh.fecha_objetivo(_JOB, datetime(2026, 8, 24, 9, 0)) == "2026-08-20"
    # Lunes 24/08 a las 13 UTC (ya corrió) → escribió el viernes 21.
    assert reh.fecha_objetivo(_JOB, datetime(2026, 8, 24, 13, 0)) == "2026-08-21"
    # Martes hábil de noche (el caso de siempre): el hábil anterior a hoy.
    assert reh.fecha_objetivo(_JOB, datetime(2026, 8, 25, 23, 0)) == "2026-08-24"
    # Feriado entre semana (9 de julio 2026, jueves): última corrida fue el
    # miércoles 8 → escribió el martes 7.
    assert reh.fecha_objetivo(_JOB, datetime(2026, 7, 9, 15, 0)) == "2026-07-07"


def test_un_job_que_no_existe_no_tiene_dia():
    assert reh.fecha_objetivo("rm_-rf") == ""


# ── GUARDA 3: solo jobs declarados ───────────────────────────────────────────

def test_un_job_que_no_esta_declarado_NO_corre():
    r = reh.rehacer("rm_-rf", "2026-08-20")
    assert r["ok"] is False and "no es un job" in r["error"]


def test_no_se_corre_nada_para_un_job_desconocido(monkeypatch):
    monkeypatch.setattr(reh, "_correr", lambda cfg: pytest.fail("ejecutó"))
    reh.rehacer("cualquier_cosa", "2026-08-20")


# ── GUARDA 1: sin evidencia no corre ─────────────────────────────────────────

def test_si_la_fecha_YA_esta_NO_se_ejecuta_nada(monkeypatch):
    """La guarda principal. Un job puede salir con error DESPUÉS de escribir
    todo — ahí no hay nada que rehacer."""
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: True)
    monkeypatch.setattr(reh, "_correr", lambda cfg: pytest.fail("ejecutó igual"))
    r = reh.rehacer(_JOB, "2026-08-20")
    assert r["ok"] is True and r["corrio"] is False and r["ya_estaba"] is True


def test_NO_PUDE_MIRAR_no_es_FALTA(monkeypatch):
    """`None` ≠ `False`. Relanzar porque la consulta falló sería ejecutar a
    ciegas, que es justo lo que este módulo existe para no hacer."""
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: None)
    monkeypatch.setattr(reh, "_correr", lambda cfg: pytest.fail("ejecutó a ciegas"))
    r = reh.rehacer(_JOB, "2026-08-20")
    assert r["ok"] is False and r["corrio"] is False
    assert "ciegas" in r["error"]


# ── GUARDA 4: la prueba es la TABLA, no el exit code ─────────────────────────

def test_salir_en_VERDE_sin_escribir_NO_es_un_exito(monkeypatch):
    """El otro lado de la moneda: un job puede salir 0 y no haber escrito una
    fila (la API devolvió vacío, el filtro dejó todo afuera)."""
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: False)
    monkeypatch.setattr(reh, "_correr", lambda cfg: {"ok": True, "salida": ""})
    r = reh.rehacer(_JOB, "2026-08-20")
    assert r["ok"] is False and r["escribio"] is False
    assert "SIGUE sin" in r["error"]


def test_el_exito_es_que_la_fecha_APAREZCA(monkeypatch):
    respuestas = iter([False, True])          # antes falta, después está
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: next(respuestas))
    monkeypatch.setattr(reh, "_correr", lambda cfg: {"ok": True, "salida": "listo"})
    r = reh.rehacer(_JOB, "2026-08-20", por="test")
    assert r["ok"] is True and r["escribio"] is True and r["por"] == "test"


def test_si_el_job_FALLA_no_se_afirma_que_escribio(monkeypatch):
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: False)
    monkeypatch.setattr(reh, "_correr",
                        lambda cfg: {"ok": False, "error": "salió con código 1"})
    r = reh.rehacer(_JOB, "2026-08-20")
    assert r["ok"] is False and r["corrio"] is True


def test_fuera_del_droplet_lo_DICE_en_vez_de_reventar():
    with patch("pathlib.Path.exists", lambda self: False):
        r = reh._correr(reh.REHACIBLES[_JOB])
    assert r["ok"] is False and "Droplet" in r["error"]


# ── `faltantes()`: es la EVIDENCIA, no el error ──────────────────────────────

def test_faltantes_solo_publica_lo_que_se_pudo_COMPROBAR(monkeypatch):
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: None)
    assert reh.faltantes() == [], "«no pude mirar» nunca se publica como «falta»"


def test_faltantes_nombra_el_job_y_el_dia(monkeypatch):
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: False)
    x = reh.faltantes()
    assert len(x) == len(reh.REHACIBLES)
    uno = x[0]
    assert uno["job"] in reh.REHACIBLES and uno["fecha"] and uno["tabla"]
    assert uno["key"] == f"{uno['job']}|{uno['fecha']}"


def test_si_el_dia_esta_no_hay_faltante(monkeypatch):
    monkeypatch.setattr(reh, "hay_dato", lambda j, f: True)
    assert reh.faltantes() == []


# ── enchufado al agente ──────────────────────────────────────────────────────

def test_el_control_existe_y_dice_QUE_ROMPE():
    from api.services.av_agent_salud import CONTROLES as EXPLICADOS
    from jobs.controles_datos import CONTROLES
    assert "dia_sin_dato" in {c.id for c in CONTROLES}
    assert "dia_sin_dato" in EXPLICADOS


def test_el_control_lee_la_MISMA_lista_que_el_arreglo():
    """Si el control tuviera su propia lista de jobs, podría cantar un faltante
    que la acción no sabe rehacer — y el botón no aparecería nunca."""
    import inspect

    from jobs.controles_datos import _chk_dia_sin_dato
    assert "av_agent_rehacer" in inspect.getsource(_chk_dia_sin_dato)


def test_la_accion_resuelve_ese_control():
    from api.services.av_agent_hacer import POR_CONTROL
    assert POR_CONTROL["dia_sin_dato"] == "sistema.rehacer_dia"


def test_la_accion_es_SEGUIBLE():
    """El dato tarda: el job pega a Aunesa cuenta por cuenta."""
    from api.services.av_agent_hacer import ACCIONES, Seguible
    a = ACCIONES["sistema.rehacer_dia"]
    assert isinstance(a, Seguible) and a.espera_s >= 30 * 60


def test_la_propuesta_dice_QUE_dia_falta_y_QUE_rompe():
    from api.services.av_agent_hacer import ACCIONES
    p = ACCIONES["sistema.rehacer_dia"].proponer(
        [{"job": _JOB, "fecha": "2026-08-19"}])[0]
    assert p.sujeto == _JOB and p.propuesto == "2026-08-19"
    assert "portafolio.tenencia" in p.porque and "2026-08-19" in p.porque


def test_sin_fecha_no_hay_propuesta():
    """Sin el día no se puede exigir la precondición ni verificar el resultado."""
    from api.services.av_agent_hacer import ACCIONES
    a = ACCIONES["sistema.rehacer_dia"]
    assert a.proponer([{"job": _JOB, "fecha": ""}]) == []
    assert a.proponer([{"job": "rm_-rf", "fecha": "2026-08-19"}]) == []
