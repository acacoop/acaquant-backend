"""Tests del motor de SALUD.

El caso que tiene que agarrar, y que motivó todo: el 2026-08-07 la card de AuM
estaba en VERDE con el job muerto hacía 48 h, porque el tablero miraba "cómo salió
la última corrida" y nunca "¿tenía que correr y no corrió?".
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services import salud

# ── ¿Cuándo tendría que haber corrido? ───────────────────────────────────────

def test_cron_diario_de_lunes_a_viernes():
    # '0 11 * * 1-5' = 11:00 UTC de lunes a viernes.
    vie = datetime(2026, 8, 7, 15, 0, tzinfo=UTC)
    assert salud.ultima_ejecucion_esperada("0 11 * * 1-5", vie) == \
        datetime(2026, 8, 7, 11, 0, tzinfo=UTC)


def test_el_finde_la_ultima_esperada_es_del_viernes():
    """Clave para no cantar falsos atrasos: el domingo, un job L-V no está atrasado
    por no haber corrido el sábado."""
    dom = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)
    assert salud.ultima_ejecucion_esperada("0 11 * * 1-5", dom) == \
        datetime(2026, 8, 7, 11, 0, tzinfo=UTC)


def test_cron_cada_n_horas():
    t = datetime(2026, 8, 7, 13, 30, tzinfo=UTC)
    assert salud.ultima_ejecucion_esperada("0 */4 * * *", t) == \
        datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


# ── El chequeo de JOB ────────────────────────────────────────────────────────

def _cron(schedule="0 11 * * 1-5", status="ok", ultimo=None, instrumentado=True):
    return {"label": "aum", "schedule": schedule, "modules": ["jobs.portafolio_backfill"],
            "instrumentado": instrumentado,
            "runs": [{"modulo": "jobs.portafolio_backfill", "tipo": "aum",
                      "ultimo": ({"status": status, "started_at": ultimo,
                                  "resumen": "filas=6036"} if ultimo else None)}]}


def test_EL_CASO_DEL_07_08_job_en_ok_pero_sin_correr_hace_dos_dias():
    """El agujero exacto: la última corrida dice `ok`, así que el tablero viejo lo
    pintaba de verde. Pero debía correr el viernes 11:00 y la última fue el jueves."""
    ahora = datetime(2026, 8, 7, 15, 0, tzinfo=UTC)          # viernes, 15:00 UTC
    c = _cron(status="ok", ultimo="2026-08-06T11:00:02+00:00")  # última: jueves

    r = salud._chequeo_job(c, ahora)

    assert r["estado"] == salud.ERROR
    assert "debía correr" in r["motivo"]


def test_job_que_corrio_en_hora_esta_ok():
    ahora = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    r = salud._chequeo_job(_cron(ultimo="2026-08-07T11:00:02+00:00"), ahora)
    assert r["estado"] == salud.OK


def test_dentro_del_margen_no_se_canta_atraso():
    """Un job que arranca 11:00 y tarda 22 minutos no está atrasado."""
    ahora = datetime(2026, 8, 7, 11, 30, tzinfo=UTC)
    r = salud._chequeo_job(_cron(ultimo="2026-08-06T11:00:02+00:00"), ahora)
    assert r["estado"] == salud.OK


def test_el_finde_un_job_L_V_no_esta_atrasado():
    ahora = datetime(2026, 8, 9, 15, 0, tzinfo=UTC)          # domingo
    r = salud._chequeo_job(_cron(ultimo="2026-08-07T11:00:02+00:00"), ahora)
    assert r["estado"] == salud.OK


def test_una_corrida_fallida_es_error():
    ahora = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    r = salud._chequeo_job(_cron(status="error", ultimo="2026-08-07T11:00:02+00:00"), ahora)
    assert r["estado"] == salud.ERROR


def test_partial_es_warn_no_error():
    ahora = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    r = salud._chequeo_job(_cron(status="partial", ultimo="2026-08-07T11:00:02+00:00"), ahora)
    assert r["estado"] == salud.WARN


def test_un_job_sin_instrumentar_es_punto_ciego_no_verde():
    """No se puede afirmar que está bien algo de lo que no se sabe nada."""
    ahora = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
    r = salud._chequeo_job(_cron(ultimo=None, instrumentado=False), ahora)
    assert r["estado"] == salud.WARN
    assert "sin instrumentar" in r["motivo"]


# ── El chequeo de DATO ───────────────────────────────────────────────────────

def test_dato_atrasado_es_error(monkeypatch):
    """La red que agarra el fallo aunque el job mienta: mira el RESULTADO."""
    from datetime import date
    monkeypatch.setattr(salud, "_q", lambda sql, params=None: [{"ultima": date(2026, 8, 5)}])
    c = {"id": "dato:x", "titulo": "T", "tabla": "portafolio.tenencia",
         "columna": "fecha", "max_dias_habiles": 1}

    r = salud._chequeo_dato(c, datetime(2026, 8, 10, 15, 0, tzinfo=UTC))

    assert r["estado"] == salud.ERROR
    assert "días hábiles" in r["motivo"]


def test_dato_al_dia_es_ok(monkeypatch):
    from datetime import date
    monkeypatch.setattr(salud, "_q", lambda sql, params=None: [{"ultima": date(2026, 8, 7)}])
    c = {"id": "dato:x", "titulo": "T", "tabla": "t", "columna": "fecha",
         "max_dias_habiles": 2}
    r = salud._chequeo_dato(c, datetime(2026, 8, 7, 15, 0, tzinfo=UTC))
    assert r["estado"] == salud.OK


def test_tabla_vacia_es_error(monkeypatch):
    monkeypatch.setattr(salud, "_q", lambda sql, params=None: [{"ultima": None}])
    c = {"id": "dato:x", "titulo": "T", "tabla": "t", "columna": "fecha"}
    r = salud._chequeo_dato(c, datetime(2026, 8, 7, tzinfo=UTC))
    assert r["estado"] == salud.ERROR


# ── El orden de la pantalla ──────────────────────────────────────────────────

def test_lo_roto_va_primero(monkeypatch):
    monkeypatch.setattr(salud, "catalogo_jobs", lambda: {"jobs": [
        _cron(ultimo="2026-08-07T11:00:02+00:00"),                       # ok
        {**_cron(status="error", ultimo="2026-08-07T11:00:02+00:00"), "label": "zzz"},
    ]})
    monkeypatch.setattr(salud, "CONTRATOS", [])
    monkeypatch.setattr(salud, "_ahora", lambda: datetime(2026, 8, 7, 12, 0, tzinfo=UTC))

    r = salud.resumen()

    assert r["chequeos"][0]["estado"] == salud.ERROR, "lo roto arriba"
    assert r["veredicto"] == salud.ERROR
    assert r["conteo"][salud.ERROR] == 1
