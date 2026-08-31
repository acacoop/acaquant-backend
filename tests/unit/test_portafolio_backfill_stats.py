"""El backfill de tenencias tiene que DELATARSE cuando no escribe nada.

Incidente 2026-08-07: Aunesa devolvió HTTP 500 a las 1868 cuentas del día. El job
las registró una por una en `portafolio.backfill_log` con `error_http_500`… y la
corrida quedó en `manager.job_runs` como **ok**, con `stats: {}`. Nadie se enteró
hasta que alguien miró la vista dos días después y notó que faltaba el 06/08.

La causa no era Aunesa (eso pasa y va a volver a pasar), era que el job calculaba
los contadores y solo los imprimía por stdout: nunca se los reportaba al
JobRunLogger. Una corrida que devolvió cero se veía igual que una que cargó 6036
filas.
"""
from __future__ import annotations

import pytest

from core.job_runs import JobRunLogger


def test_el_logger_pasa_a_partial_cuando_se_le_reportan_errores():
    """La mitad del arreglo: el job ahora SÍ llama logger.error() por cada día con
    fallos. `JobRunLogger` decide el status con eso — sin `errors` cargados devuelve
    `ok`, que es como el 2026-08-07 quedó en verde con 1868 cuentas caídas.

    Se mira `errors` (la entrada de esa decisión) en vez del INSERT, para no atarse
    a cómo se persiste.
    """
    log = JobRunLogger("test")
    assert log.errors == [], "arranca limpio → sería 'ok'"

    log.error("2026-08-06: 1868 errores y 0 timeouts sobre 1868 cuentas")

    assert log.errors, "con errores cargados el status pasa a 'partial'"


def test_un_run_sin_filas_y_con_errores_levanta(monkeypatch):
    """La otra mitad: que la corrida termine en `error`, no en `partial`.

    El logger solo marca `error` si sale una excepción, así que el job levanta
    RuntimeError cuando pidió días y no escribió NI UNA fila teniendo fallos. Es
    exactamente el caso del 2026-08-07.
    """
    import jobs.portafolio_backfill as job

    # El run no llega a la base: se corta antes, en el arranque.
    monkeypatch.setattr(job.sys, "argv", ["x", "--diario"])
    monkeypatch.setattr(job, "_ensure_schema", lambda: None)
    monkeypatch.setattr(job, "cargar_contrapartes", lambda: None)
    monkeypatch.setattr(job, "_load_assets_map", lambda: {})
    monkeypatch.setattr(job, "autenticar", lambda: {})
    monkeypatch.setattr(job, "_ya_hechas", lambda iso: set())
    monkeypatch.setattr(job, "_write_date", lambda *a, **k: None)
    monkeypatch.setattr(job, "_alta_assets_nuevos", lambda regs: 0)

    class _DF:
        def iterrows(self):
            yield 0, {"id": "10", "denominacion": "CUENTA 10"}

    monkeypatch.setattr(job, "obtener_cuentas", lambda h: _DF())
    # Aunesa contesta 500 a todo, igual que el 2026-08-07.
    monkeypatch.setattr(job, "_fetch_parse",
                        lambda idc, dn, desde, iso, amap: (idc, "error_http_500", []))

    log = JobRunLogger("test")
    with pytest.raises(RuntimeError, match="NO escribió ninguna fila"):
        job._run_backfill(logger=log)

    # Y además dejó los contadores, que era lo que faltaba para poder verlo.
    assert log.stats["errores"] == 1
    assert log.stats["filas"] == 0
    assert log.errors, "el día fallido tiene que quedar registrado como error del run"
