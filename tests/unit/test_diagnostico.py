"""EL DIAGNÓSTICO (asistente/diagnostico.py + asistente/agentes/diagnostico.py).

Lo que se prueba sin proveedor: que el validador aplique las reglas de la casa
aunque el modelo diga otra cosa, que las herramientas no lean secretos ni
salgan del repo, que el disparo respete los topes, que el worker despache por
tipo, y que el agente esté conectado (registro, tarea, ruteo)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from asistente import diagnostico as DG
from asistente.agentes import diagnostico as HERR

DOSIER = {
    "hallazgo": {"id": 7, "habilidad": "motor_latido", "sujeto": "asistente-worker",
                 "regla": "nunca_latio", "estado": "nuevo", "arreglo": ""},
    "detector": {"arreglo_declarado": "", "automatico": False, "naturaleza": "incidente"},
    "historia": {"episodios_30d": 1, "cronico": False, "acciones": [], "precedentes": []},
    "reloj": {"en_rueda": False},
}
CITA = "[E:a1b2c3d4e5f6:cuantos]"


def _conclusion(**kw) -> dict:
    base = {"causa": "bug_codigo", "resumen": "El worker no llama a latido.",
            "verificado": [{"afirmacion": "worker.py no importa core.latido", "cita": CITA,
                            "estado": "verificado"}],
            "accion": "cambiar_codigo", "accion_detalle": "sumar el latido al worker",
            "no_hacer": ["no reiniciar: no cambia nada"], "archivo": "asistente/worker.py",
            "motivo_codigo": "falta core.latido", "escalar_a": None}
    return {**base, **kw}


# ── [4] VALIDAR: las reglas de la casa mandan sobre el modelo ───────────────


def test_una_conclusion_bien_formada_pasa_entera():
    c = DG.validar(_conclusion(), DOSIER, en_rueda=False)
    assert (c["causa"], c["accion"], c["archivo"]) == ("bug_codigo", "cambiar_codigo", "asistente/worker.py")
    assert c["validado"]["cambios"] == []


def test_sin_conclusion_o_fuera_de_lista_queda_en_no_se():
    assert DG.validar(None, DOSIER, en_rueda=False)["accion"] == "no_se"
    c = DG.validar(_conclusion(causa="magia", accion="volar"), DOSIER, en_rueda=False)
    assert (c["causa"], c["accion"]) == ("sin_verificar", "no_se")


def test_una_causa_sin_afirmacion_verificada_con_cita_no_vale():
    """«No lo verifiqué pero es un bug» no es un diagnóstico: es una opinión."""
    sin = _conclusion(verificado=[{"afirmacion": "creo que falta latido", "cita": "", "estado": "hipotesis"}])
    c = DG.validar(sin, DOSIER, en_rueda=False)
    assert (c["causa"], c["accion"]) == ("sin_verificar", "no_se")
    # `transitorio` sí puede ir sin cita: no afirma nada sobre el sistema.
    c = DG.validar(_conclusion(causa="transitorio", accion="esperar_hasta", verificado=[]), DOSIER,
                   en_rueda=False)
    assert c["causa"] == "transitorio"


def test_un_bug_de_codigo_no_se_arregla_apretando_y_una_config_no_se_parchea():
    c = DG.validar(_conclusion(accion="aplicar_arreglo"), DOSIER, en_rueda=False)
    assert c["accion"] == "cambiar_codigo"
    c = DG.validar(_conclusion(causa="configuracion", accion="aplicar_arreglo"), DOSIER, en_rueda=False)
    assert c["accion"] == "escalar_a"


def test_solo_se_aplica_un_arreglo_que_la_regla_declara():
    inc = _conclusion(causa="incidente", accion="aplicar_arreglo")
    assert DG.validar(inc, DOSIER, en_rueda=False)["accion"] == "escalar_a"
    con_arreglo = {**DOSIER, "detector": {**DOSIER["detector"], "arreglo_declarado": "rehacer_job"}}
    assert DG.validar(inc, con_arreglo, en_rueda=False)["accion"] == "aplicar_arreglo"


def test_lo_cronico_no_se_parchea_por_cuarta_vez():
    cronico = {**DOSIER,
               "detector": {**DOSIER["detector"], "arreglo_declarado": "rehacer_job"},
               "historia": {"episodios_30d": 4, "cronico": True,
                            "acciones": [{"arreglo": "rehacer_job", "ok": True}] * 3, "precedentes": []}}
    c = DG.validar(_conclusion(causa="incidente", accion="aplicar_arreglo"), cronico, en_rueda=False)
    assert (c["causa"], c["accion"]) == ("configuracion", "escalar_a")


def test_en_rueda_no_se_reinicia_ni_relanza():
    con_arreglo = {**DOSIER, "detector": {**DOSIER["detector"], "arreglo_declarado": "rehacer_job"}}
    inc = _conclusion(causa="incidente", accion="aplicar_arreglo",
                      accion_detalle="systemctl restart motor_curvas.service")
    c = DG.validar(inc, con_arreglo, en_rueda=True)
    assert c["accion"] == "esperar_hasta" and c["accion_detalle"].startswith("Hasta el cierre")
    assert DG.validar(inc, con_arreglo, en_rueda=False)["accion"] == "aplicar_arreglo"


def test_sin_verificar_transitorio_y_fuera_de_alcance_no_aplican_nada():
    con_arreglo = {**DOSIER, "detector": {**DOSIER["detector"], "arreglo_declarado": "rehacer_job"}}
    c = DG.validar(_conclusion(causa="sin_verificar", accion="aplicar_arreglo"), con_arreglo, en_rueda=False)
    assert c["accion"] == "no_se"
    c = DG.validar(_conclusion(causa="transitorio", accion="aplicar_arreglo"), con_arreglo, en_rueda=False)
    assert c["accion"] == "esperar_hasta"
    c = DG.validar(_conclusion(causa="fuera_de_alcance", accion="aplicar_arreglo"), con_arreglo, en_rueda=False)
    assert c["accion"] == "escalar_a"
    # La cita vale con el ref solo: el formato no es lo que importa.
    c = DG.validar(_conclusion(verificado=[{"afirmacion": "x", "cita": "a1b2c3d4e5f6", "estado": "verificado"}]),
                   DOSIER, en_rueda=False)
    assert c["causa"] == "bug_codigo"


def test_una_escalacion_que_dice_no_relanzar_no_se_convierte_en_espera():
    con_arreglo = {**DOSIER, "detector": {**DOSIER["detector"], "arreglo_declarado": "rehacer_job"}}
    esc = _conclusion(causa="incidente", accion="escalar_a",
                      accion_detalle="decirle a la mesa que NO relance el job hasta revisar el cron")
    assert DG.validar(esc, con_arreglo, en_rueda=True)["accion"] == "escalar_a"
    apl = _conclusion(causa="incidente", accion="aplicar_arreglo", accion_detalle="relanzar el job de hoy")
    assert DG.validar(apl, con_arreglo, en_rueda=True)["accion"] == "esperar_hasta"
    assert DG.validar(apl, con_arreglo, en_rueda=False)["accion"] == "aplicar_arreglo"


def test_un_cambio_de_codigo_apunta_a_un_archivo_que_existe():
    c = DG.validar(_conclusion(archivo="no/existe.py"), DOSIER, en_rueda=False)
    assert c["archivo"] is None and any("no existe" in x for x in c["validado"]["cambios"])
    c = DG.validar(_conclusion(archivo=".env"), DOSIER, en_rueda=False)
    assert c["archivo"] is None, "un secreto no es un archivo de código"


# ── [3] leer la conclusión, venga con esquema o en prosa ────────────────────


def test_la_conclusion_se_lee_como_json_o_adentro_de_prosa():
    j = '{"causa": "incidente", "accion": "escalar_a"}'
    assert DG.leer_conclusion(j)["causa"] == "incidente"
    assert DG.leer_conclusion("Acá va mi conclusión:\n" + j + "\nGracias.")["accion"] == "escalar_a"
    assert DG.leer_conclusion("```json\n" + j + "\n```")["causa"] == "incidente", "con cerco de código"
    assert DG.leer_conclusion("no hay json") is None and DG.leer_conclusion(None) is None
    assert DG.leer_conclusion('{"otra": 1}') is None


# ── el disparo: topes y no repetir ──────────────────────────────────────────


def test_el_disparo_elige_lo_sin_diagnostico_o_cambiado_y_respeta_topes():
    ahora = datetime(2026, 9, 17, 14, tzinfo=UTC)
    hace = lambda h: ahora - timedelta(hours=h)  # noqa: E731
    abiertos = [
        {"id": 1, "estado": "nuevo", "severidad": "baja", "detectado_at": hace(1), "diagnosticado_at": None},
        {"id": 2, "estado": "reincidio", "severidad": "alta", "detectado_at": hace(5),
         "diagnosticado_at": hace(2), "diagnostico": {"estado_hallazgo": "nuevo"}},
        {"id": 3, "estado": "nuevo", "severidad": "alta", "detectado_at": hace(3),
         "diagnosticado_at": hace(2), "diagnostico": {"estado_hallazgo": "nuevo"}},
        {"id": 4, "estado": "nuevo", "severidad": "media", "detectado_at": hace(30),
         "diagnosticado_at": hace(30), "diagnostico": {"estado_hallazgo": "nuevo"}},
        {"id": 5, "estado": "ignorado", "severidad": "alta", "detectado_at": hace(1), "diagnosticado_at": None},
        {"id": 6, "estado": "nuevo", "severidad": "alta", "detectado_at": hace(1), "diagnosticado_at": None},
    ]
    ids = DG._elegir(abiertos, en_cola={6}, hoy=0, ahora=ahora, tope_pasada=10, tope_dia=40, refresco_h=24)
    # 2 cambió de estado, 4 está viejo, 1 no tiene; 3 está vigente; 5 ignorado; 6 en cola.
    assert ids == [2, 4, 1], "alta primero, después media, después baja"
    assert DG._elegir(abiertos, {6}, 0, ahora, tope_pasada=1, tope_dia=40, refresco_h=24) == [2]
    assert DG._elegir(abiertos, set(), 0, ahora, tope_pasada=1, tope_dia=40, refresco_h=24) == [6], \
        "misma severidad: el más reciente primero"
    assert DG._elegir(abiertos, set(), 40, ahora, tope_pasada=3, tope_dia=40, refresco_h=24) == []


def test_un_run_de_diagnostico_se_entiende_por_su_pregunta():
    with patch.object(DG, "correr", return_value={"respuesta": "ok"}) as c:
        assert DG.correr_run({"run_id": "r", "pregunta": "diagnosticar hallazgo #42", "usuario": "u"})["respuesta"] == "ok"
        assert c.call_args.args == (42,) and c.call_args.kwargs == {"run_id": "r", "usuario": "u"}
    assert "error" in DG.correr_run({"run_id": "r", "pregunta": "hola"})


def test_el_worker_despacha_por_tipo():
    from asistente import worker

    run = {"run_id": "r1", "tipo": "diagnostico", "pregunta": "diagnosticar hallazgo #1",
           "usuario": "av-agent", "sesion": "s", "rol": "admin", "portal": "trading"}
    with patch.object(DG, "correr_run", return_value={"respuesta": "x", "error": None}) as c, \
         patch.object(worker.ejecuciones, "obtener", return_value={"estado": "running"}), \
         patch.object(worker.ejecuciones, "terminar") as t, \
         patch.object(worker.ejecuciones, "emitir"), \
         patch.object(worker.sesiones, "preguntar", side_effect=AssertionError("no es una conversación")):
        worker.procesar(run, grafo_compilado=None)
    assert c.called and t.call_args.args[:2] == ("r1", "succeeded")


# ── las herramientas: solo lectura, solo el repo, nunca secretos ────────────


def test_las_herramientas_no_leen_secretos_ni_salen_del_repo():
    assert HERR._para_tests_ruta_segura(".env") is None
    assert HERR._para_tests_ruta_segura("deploy/.env.prod") is None
    assert HERR._para_tests_ruta_segura("../../etc/passwd") is None
    assert HERR._para_tests_ruta_segura("/etc/passwd") is None
    assert HERR._para_tests_ruta_segura("certs/cliente.pem") is None
    assert HERR._para_tests_ruta_segura("venv/lib/x.py") is None
    assert HERR._para_tests_ruta_segura("asistente/worker.py") == "asistente/worker.py"
    assert "error" in HERR.leer_codigo(".env")
    assert "error" in HERR.leer_codigo("../fuera.py")
    assert "error" in HERR.buscar_codigo("x", en="../")
    assert "error" in HERR.journal("no-existe") and "unidades" in HERR.journal("no-existe")


def test_buscar_y_leer_codigo_contestan_sobre_el_repo():
    r = HERR.buscar_codigo("def procesar", en="asistente/worker.py")
    assert r["cuantos"] == 1 and r["coincidencias"][0]["archivo"] == "asistente/worker.py"
    lect = HERR.leer_codigo("asistente/worker.py", 1, 3)
    assert lect["desde"] == 1 and lect["hasta"] == 3 and lect["contenido"].startswith("1: ")
    lect = HERR.leer_codigo("asistente/worker.py", 1, 5000)
    assert lect["hasta"] - lect["desde"] + 1 <= HERR.MAX_LINEAS_CODIGO, "tope de líneas por llamada"
    assert HERR.leer_doc("AGENT", "8. Invariantes")["titulo"].startswith("8. Invariantes")
    assert "secciones" in HERR.leer_doc("AGENT", "no existe esta sección")


def test_lo_largo_pasa_por_el_lector_y_sin_lector_se_recorta():
    largo = "x" * (HERR.LECTOR_UMBRAL + 100)
    with patch("core.modelos.completar", return_value="lo que importa") as c:
        assert HERR._condensar(largo, "prueba").endswith("lo que importa") and c.called
    with patch("core.modelos.completar", return_value=None):
        assert "recortado" in HERR._condensar(largo, "prueba")
    with patch("core.modelos.completar", side_effect=AssertionError("no hacía falta")):
        assert HERR._condensar("corto", "prueba") == "corto"


def test_el_journal_lee_un_unit_declarado_y_filtra():
    with patch.object(HERR, "_correr", return_value=(0, "a INFO ok\nb Traceback x\nc ERROR y\n")):
        r = HERR.journal("asistente-worker", lineas=50, filtro="Traceback|ERROR")
        assert r["lineas"] == 2 and "INFO" not in r["extracto"]
    with patch.object(HERR, "_correr", return_value=(1, "journalctl: no permission")):
        assert "error" in HERR.journal("api")


# ── conectado: registro, tarea, acceso, ruteo ───────────────────────────────


def test_el_agente_esta_conectado_y_sus_herramientas_son_de_sistema():
    from asistente import ejecutor as EXE
    from asistente import grafo
    from asistente import ruteo as R
    from asistente.agentes import AGENTES
    from core import modelos

    a = AGENTES["diagnostico"]
    assert a.tarea in modelos.tareas() and "diagnostico" in grafo._SUBGRAFOS
    for t in ("asistente_diagnostico_lector", "asistente_diagnostico_investigar",
              "asistente_diagnostico_concluir"):
        assert t in modelos.tareas()
    for f in a.herramientas:
        assert EXE.acceso_de(f.__name__) == EXE.Acceso.READ_SISTEMA, f.__name__
    # Solo admin mira el sistema por dentro.
    ctx = EXE.RunContext(run_id="r", usuario="u", rol="trader", portal="trading", cuentas=())
    assert "error" in EXE.ejecutar(a, "reloj", {}, ctx).resultado
    d = R.por_reglas("diagnosticá el hallazgo #12")
    assert d.tipo == "van" and d.agentes == ("diagnostico",)


def test_el_diagnostico_se_guarda_por_el_registro_y_no_sobre_lo_ignorado():
    from agente import registro

    cur = MagicMock()
    cur.rowcount = 1
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    with patch("agente.registro.get_pool", return_value=pool):
        assert registro.anotar_diagnostico(7, {"causa": "x"})
    assert "UPDATE agente.hallazgos SET diagnostico" in cur.execute.call_args.args[0]
    ignorado = {**DOSIER, "hallazgo": {**DOSIER["hallazgo"], "estado": "ignorado"}}
    with patch.object(DG, "dosier", return_value=ignorado), \
         patch.object(DG, "investigar", side_effect=AssertionError("no tenía que investigar")):
        assert "no me interesa" in DG.correr(7, run_id="r")["error"]


def test_correr_junta_las_cinco_etapas_y_guarda(permiso=None):
    inv = {"notas": "leí worker.py: no importa latido " + CITA, "error": None,
           "evidencias": [{"ref": "a1b2c3d4e5f6", "sujeto": "general", "campos": {"cuantos": 0}}],
           "datos": [], "vueltas": 2, "tokens_in": 100, "tokens_out": 10, "llamadas": [1],
           "contexto": "cuantos 0"}
    con = {"conclusion": _conclusion(), "crudo": "{}", "error": None, "tokens_in": 50,
           "tokens_out": 5, "llamadas": [2]}
    with patch.object(DG, "dosier", return_value=DOSIER), \
         patch.object(DG, "investigar", return_value=inv), \
         patch.object(DG, "concluir", return_value=con), \
         patch.object(DG.registro, "anotar_diagnostico", return_value=True) as guardar:
        r = DG.correr(7, run_id="r9")
    d = guardar.call_args.args[1]
    assert guardar.call_args.args[0] == 7 and d["causa"] == "bug_codigo" and d["run_id"] == "r9"
    assert d["estado_hallazgo"] == "nuevo" and d["tokens_in"] == 150 and d["control"]["ok"] is True
    assert r["respuesta"].startswith("bug_codigo → cambiar_codigo") and r["llamadas"] == [1, 2]


@pytest.mark.parametrize("texto", ["hola", "diagnosticar hallazgo #x"])
def test_una_pregunta_que_no_es_un_pedido_de_diagnostico_no_corre(texto):
    assert "error" in DG.correr_run({"run_id": "r", "pregunta": texto})


# ── verlo desde el LAB: la traza y el pedido a mano ─────────────────────────


def _pool_con(cur):
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    return pool


def test_pedir_a_mano_usa_la_misma_puerta_y_no_encola_dos_veces():
    from asistente import ejecuciones

    cur = MagicMock()
    # existe, no ignorado, y NO hay uno activo → encola.
    cur.fetchone.side_effect = [{"estado": "nuevo"}, None]
    with patch("asistente.diagnostico.get_pool", return_value=_pool_con(cur)), \
         patch.object(ejecuciones, "crear", return_value={"run_id": "nuevo1"}) as c:
        r = DG.pedir(42)
    assert r == {"ok": True, "run_id": "nuevo1", "ya_estaba": False}
    assert c.call_args.args == ("diagnosticar hallazgo #42",) and c.call_args.kwargs["tipo"] == "diagnostico"
    # ya hay uno en cola → devuelve ese, sin crear.
    cur.fetchone.side_effect = [{"estado": "nuevo"}, {"run_id": "viejo1"}]
    with patch("asistente.diagnostico.get_pool", return_value=_pool_con(cur)), \
         patch.object(ejecuciones, "crear", side_effect=AssertionError("no tenía que crear")):
        assert DG.pedir(42) == {"ok": True, "run_id": "viejo1", "ya_estaba": True}
    # ignorado o inexistente → no.
    cur.fetchone.side_effect = [{"estado": "ignorado"}]
    with patch("asistente.diagnostico.get_pool", return_value=_pool_con(cur)):
        assert DG.pedir(42)["ok"] is False
    cur.fetchone.side_effect = [None]
    with patch("asistente.diagnostico.get_pool", return_value=_pool_con(cur)):
        assert "no existe" in DG.pedir(42)["error"]


def test_la_traza_muestra_el_ultimo_run_o_el_pedido_con_sus_eventos():
    from asistente import ejecuciones

    runs = [{"run_id": "b", "estado": "running"}, {"run_id": "a", "estado": "succeeded"}]
    with patch.object(ejecuciones, "runs_de", return_value=runs) as rd, \
         patch.object(ejecuciones, "eventos_de", side_effect=lambda rid: [{"tipo": "vuelta", "run": rid}]):
        t = DG.traza(42)
        assert rd.call_args.args == ("diagnosticar hallazgo #42",)
        assert t["run"]["run_id"] == "b" and t["eventos"] == [{"tipo": "vuelta", "run": "b"}]
        assert DG.traza(42, run_id="a")["run"]["run_id"] == "a"
        assert DG.traza(42, run_id="zzz")["run"] is None, "un run que no es de este hallazgo no se muestra"
    with patch.object(ejecuciones, "runs_de", return_value=[]), \
         patch.object(ejecuciones, "eventos_de", side_effect=AssertionError("sin run no hay eventos")):
        assert DG.traza(42) == {"hallazgo_id": 42, "runs": [], "run": None, "eventos": []}


def test_concluir_deja_su_rastro_en_el_ciclo():
    """La etapa 3 emite qué devolvió y cuánto costó, parseara o no."""
    from asistente import eventos as EV

    msg = MagicMock(content='{"causa": "transitorio", "accion": "nada_porque"}',
                    usage_metadata={"input_tokens": 5, "output_tokens": 2})
    m = MagicMock()
    m.invoke.return_value = msg
    vistos = []
    with patch.object(DG.modelos, "resolver", return_value=MagicMock(nombre="t", modelo="m", traza_sin_texto=False)), \
         patch.object(DG.modelos, "modelo", return_value=m), \
         patch.object(DG, "Traza", return_value=MagicMock(ids=[9])), \
         EV.capturar(vistos.append):
        con = DG.concluir(DOSIER, {"notas": "", "evidencias": []}, run_id="r", usuario="u")
    assert con["conclusion"] == {"causa": "transitorio", "accion": "nada_porque"} and con["llamadas"] == [9]
    assert vistos == [{"tipo": "diagnostico_concluir", "agente": DG.AGENTE_NOMBRE,
                       "texto": '{"causa": "transitorio", "accion": "nada_porque"}', "parseo": True,
                       "tokens_in": 5, "tokens_out": 2}]
