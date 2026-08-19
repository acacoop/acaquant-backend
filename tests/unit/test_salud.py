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


# ── Persistencia: transiciones, silenciado y vistos ──────────────────────────

def _fake_sql(monkeypatch, *, previos=None, escrituras=None):
    """Corta la capa SQL: interesa la LÓGICA de transición, no el INSERT."""
    monkeypatch.setattr(salud, "_estados_previos", lambda: previos or {})
    monkeypatch.setattr(salud, "_exec_salud",
                        lambda sql, params: (escrituras.append(params)
                                             if escrituras is not None else 0) or 1)


def test_solo_se_registra_lo_que_CAMBIO(monkeypatch):
    """Idempotencia: el panel se pide en cada carga de la vista. Si escribiera un
    evento por vez, el historial sería basura en un día."""
    escrituras: list = []
    monkeypatch.setattr(salud, "evaluar", lambda: [
        {"id": "job:a", "estado": salud.OK, "titulo": "A", "familia": "job"},
        {"id": "job:b", "estado": salud.ERROR, "titulo": "B", "familia": "job"},
    ])
    _fake_sql(monkeypatch, previos={"job:a": salud.OK}, escrituras=escrituras)

    nuevas = salud.sincronizar()

    assert [n["id"] for n in nuevas] == ["job:b"], "job:a no cambió → no se registra"
    assert len(escrituras) == 1


def test_la_transicion_guarda_de_donde_venia(monkeypatch):
    """Sin el estado anterior no se puede leer el historial: 'pasó de ok a error'
    es la información, no 'está en error'."""
    monkeypatch.setattr(salud, "evaluar", lambda: [
        {"id": "job:a", "estado": salud.ERROR, "titulo": "A", "familia": "job",
         "motivo": "debía correr", "evidencia": "x"}])
    _fake_sql(monkeypatch, previos={"job:a": salud.OK})

    nuevas = salud.sincronizar()

    assert nuevas[0]["de"] == salud.OK
    assert nuevas[0]["estado"] == salud.ERROR


def test_un_chequeo_NUEVO_alerta_sin_darlo_de_alta(monkeypatch):
    """Un job recién agregado al crontab tiene que avisar solo. Si hubiera que
    registrarlo a mano, el sistema dejaría de mantenerse solo."""
    monkeypatch.setattr(salud, "_q", lambda sql, params=None: [])   # config vacía
    assert salud.config() == {}
    monkeypatch.setattr(salud, "evaluar", lambda: [
        {"id": "job:nuevo", "estado": salud.ERROR, "titulo": "N", "familia": "job"}])
    monkeypatch.setattr(salud, "_estados_previos", lambda: {})
    monkeypatch.setattr(salud, "_exec_salud", lambda sql, params: 1)
    monkeypatch.setattr(salud, "pendientes", lambda email, limite=20: [])

    r = salud.panel(email="a@b.com")

    # Sin fila en salud_config, `alertar` cae al default true.
    assert r["chequeos"][0]["alertar"] is True


def test_todos_los_contratos_apuntan_a_columnas_que_EXISTEN():
    """Un contrato mal escrito se ve casi igual que un dato atrasado.

    Pasó con `operaciones.operaciones`: la fecha del boleto es `concertacion`, no
    `fecha`, y el chequeo salía "no pude consultar la tabla" — que manda a buscar el
    problema al lugar equivocado. Este test lee sql/schema.sql y verifica que cada
    (tabla, columna) declarada exista de verdad, así el error no puede repetirse en
    silencio cuando alguien sume un contrato nuevo.
    """
    from pathlib import Path

    schema = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"
    sql = schema.read_text(encoding="utf-8")

    for c in salud.CONTRATOS:
        i = sql.find(f"CREATE TABLE IF NOT EXISTS {c['tabla']} (")
        assert i != -1, f"{c['tabla']} no está en schema.sql"
        cuerpo = sql[i:sql.index(");", i)]
        columnas = {ln.strip().split()[0] for ln in cuerpo.splitlines()[1:]
                    if ln.strip() and not ln.strip().startswith("--")}
        assert c["columna"] in columnas, (
            f"{c['id']}: la columna {c['columna']!r} no existe en {c['tabla']} "
            f"(tiene: {sorted(columnas)})")


# ── Reactivo pero no ruidoso: solo interrumpe lo que PERSISTE ─────────────────

def test_el_umbral_de_persistencia_es_explicito():
    """El pedido fue "que aparezca cuando ya demostremos que no se soluciona".

    Avisar al primer error no sirve: los jobs fallan y se recuperan en la corrida
    siguiente, y un modal que salta por cada hipo se cierra sin leer. El umbral es
    una constante y no un número perdido en una query, para poder discutirlo.
    """
    assert salud.PERSISTENCIA_MIN >= 15, "por debajo de esto entra ruido transitorio"



def test_el_contrato_de_series_macro_MIRA_CADA_SERIE_POR_SEPARADO():
    """El bug que el user cazó el 2026-08-17: *«la serie del CER tiene datos viejos,
    o sea que es cualquiera que no lo detecte»*.

    El contrato hacía `SELECT MAX(fecha) FROM macro.series_macro` **sin filtrar por
    `serie`**, y esa tabla tiene DOLAR, CER, BADLAR, TAMAR, RiesgoPais e Inflación
    mezcladas. Un MAX sobre N series independientes **responde por la más fresca**:
    con el dólar actualizándose todos los días, el CER podía estar congelado hace
    meses y el tablero seguía verde.

    Es el mismo anti-patrón que ya apareció cinco veces en este proyecto: **un
    agregado que tapa el detalle**."""
    ids = [c["id"] for c in salud.CONTRATOS]
    # El contrato ÚNICO de la tabla entera ya no existe: mentía por diseño.
    assert "dato:macro.series_macro" not in ids
    # Y en su lugar hay uno por serie crítica, cada uno con su filtro.
    por_serie = [c for c in salud.CONTRATOS
                 if c["tabla"] == "macro.series_macro"]
    assert {c["filtro"]["valor"] for c in por_serie} >= {"CER", "DOLAR"}
    for c in por_serie:
        assert c["filtro"]["columna"] == "serie"
        # Cada uno se silencia por separado — si compartieran id, apagar el ruido
        # de una serie apagaría la alerta de las otras.
        assert ids.count(c["id"]) == 1
