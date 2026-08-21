"""LOS LOGS DE LOS MOTORES — leer no alcanza, hay que poder RESUMIR.

Un motor que se reconecta mil veces deja mil líneas casi iguales, y las últimas
20 son la misma. La pregunta útil no es «¿qué dijo recién?» sino «¿qué viene
diciendo y cuántas veces?». Estos tests fijan las dos cosas de las que depende
esa respuesta: que la normalización borre SOLO lo que cambia entre repeticiones,
y que un fallo de lectura NO se pueda confundir con silencio.
"""
from __future__ import annotations

import subprocess

from api.services import logs_sistema as ls


def _l(unidad, mensaje, ts=100.0, prio=4):
    return {"unidad": unidad, "mensaje": mensaje, "ts": ts,
            "prioridad": prio, "nivel": ls.NIVEL[prio]}


# ── la normalización ─────────────────────────────────────────────────────────

def test_dos_lineas_que_solo_cambian_en_el_simbolo_son_EL_MISMO_problema():
    """`AL30` y `GD30` no son dos problemas distintos: es el mismo error sobre
    dos símbolos. Sin agrupar, un motor con 200 suscripciones da 200 hallazgos."""
    a = ls.patron("no pude suscribir MERV - XMEV - AL30 - 24hs (intento 3)")
    b = ls.patron("no pude suscribir MERV - XMEV - GD30 - 48hs (intento 17)")
    assert a == b


def test_la_FECHA_y_la_HORA_no_hacen_dos_problemas():
    """⚠️ Este test PEDÍA que la fecha quedara en el patrón (`"<fecha>" in a`) y
    esa era justo la parte que hacía ilegible el título: los primeros 38
    caracteres eran andamiaje y el mensaje real se cortaba con «…». Lo que el
    test tenía que congelar es que dos horas distintas sean UN problema — eso no
    cambió; lo otro era la implementación."""
    a = ls.patron("2026-08-20 11:02:03 conexión caída")
    b = ls.patron("2026-08-19 23:59:00 conexión caída")
    assert a == b
    assert a == "conexión caída", "la fecha se saca, no se muestra"


def test_un_TRACEBACK_se_agrupa_por_la_EXCEPCION_y_no_por_el_archivo():
    """Un traceback son 20 renglones para UN error. Si se agrupara por el
    primero, cada uno sería su propio grupo y el resumen tendría el mismo largo
    que el log."""
    t1 = "Traceback (most recent call last):\n  File a.py, line 10\nValueError: precio 12,5 inválido"
    t2 = "Traceback (most recent call last):\n  File b.py, line 99\nValueError: precio 88,1 inválido"
    assert ls.patron(t1) == ls.patron(t2)
    assert "ValueError" in ls.patron(t1)


def test_dos_errores_DISTINTOS_no_se_mezclan():
    """El agrupador tiene que juntar lo repetido, no aplastar todo: si dos causas
    distintas cayeran en el mismo grupo, se perdería justo la que importa."""
    assert ls.patron("timeout contra Primary") != ls.patron("ValueError: precio")


# ── el agrupador ─────────────────────────────────────────────────────────────

def test_cuenta_las_repeticiones_y_guarda_LA_VENTANA():
    """Cuántas veces no alcanza: 300 en dos minutos es un motor peleando contra
    algo y 300 en un día es ruido de fondo. Sin `primera`/`última` no se puede
    distinguir, y no se responden igual."""
    g = ls.agrupar([_l("motor_rofex", "cayó la conexión", ts=100),
                    _l("motor_rofex", "cayó la conexión", ts=160),
                    _l("motor_rofex", "cayó la conexión", ts=220)])
    assert len(g) == 1
    assert g[0]["veces"] == 3 and g[0]["primera"] == 100 and g[0]["ultima"] == 220


def test_el_grupo_se_queda_con_lo_MAS_GRAVE_que_le_pasó():
    """Si el mismo texto salió como warn 50 veces y como error una, el grupo es
    un ERROR: quedarse con el último visto escondería la vez que se rompió."""
    g = ls.agrupar([_l("motor_curvas", "algo", prio=4),
                    _l("motor_curvas", "algo", prio=3),
                    _l("motor_curvas", "algo", prio=4)])
    assert g[0]["nivel"] == "error" and g[0]["veces"] == 3


def test_no_se_mezclan_dos_MOTORES_aunque_digan_lo_mismo():
    """El mismo error en dos motores son dos problemas: uno puede estar sano."""
    g = ls.agrupar([_l("motor_rofex", "timeout"), _l("motor_curvas", "timeout")])
    assert len(g) == 2


def test_primero_lo_mas_GRAVE_y_despues_lo_mas_REPETIDO():
    g = ls.agrupar([_l("m", "warn a", prio=4), _l("m", "warn a", prio=4),
                    _l("m", "warn a", prio=4), _l("m", "el error", prio=3)])
    assert g[0]["nivel"] == "error", "un error tapado por un warn repetido no se ve"


# ── «no pude leer» NO es «no hay errores» ────────────────────────────────────

def test_si_NO_SE_PUEDE_LEER_lo_dice_en_vez_de_devolver_vacio(monkeypatch):
    """El modo de falla más caro de todo esto. Una lista vacía se lee igual que
    «no hay errores», así que en un host sin systemd —o sin permiso sobre el
    journal— la vigilancia diría verde para siempre. Ya costó un incidente
    (AV_AGENT.md §0.s): el silencio se lee igual que un verde."""
    def _boom(*a, **k):
        raise FileNotFoundError("journalctl")
    monkeypatch.setattr(subprocess, "run", _boom)
    r = ls.leer(["motor_rofex"])
    assert r["disponible"] is False and r["motivo"]
    assert r["lineas"] == []


def test_el_TIMEOUT_tampoco_pasa_por_silencio(monkeypatch):
    def _lento(*a, **k):
        raise subprocess.TimeoutExpired("journalctl", 15)
    monkeypatch.setattr(subprocess, "run", _lento)
    assert ls.leer(["motor_rofex"])["disponible"] is False


def test_sin_unidades_no_se_llama_a_journalctl(monkeypatch):
    """Un `journalctl` sin `-u` devuelve el journal ENTERO de la máquina. Con la
    lista vacía eso serían millones de líneas por un descuido."""
    def _no(*a, **k):
        raise AssertionError("no se puede llamar a journalctl sin unidades")
    monkeypatch.setattr(subprocess, "run", _no)
    assert ls.leer([])["lineas"] == []


# ── el comando ───────────────────────────────────────────────────────────────

def test_cada_unidad_va_con_su_propio_menos_u():
    cmd = ls._cmd(["motor_rofex", "motor_curvas"], "-24h", 4, 100)
    assert cmd[:1] == ["journalctl"]
    assert "motor_rofex.service" in cmd and "motor_curvas.service" in cmd
    assert cmd.count("-u") == 2


def test_sin_DESDE_no_se_acota_por_tiempo():
    """La pantalla de LOGS muestra el final del archivo, no las últimas N horas.
    Si `desde` colara siempre un `--since`, esa vista cambiaría de significado."""
    assert "--since" not in ls._cmd(["x"], None, 7, 20)
    assert "--since" in ls._cmd(["x"], "-6h", 7, 20)


def test_la_PANTALLA_de_logs_sigue_devolviendo_los_MISMOS_campos():
    """El front de Manager → LOGS lee `ts_epoch`/`priority`/`servicio`/`message`.
    Al mover la lectura a un service los nombres internos cambiaron, y renombrar
    la respuesta habría dejado la tab en blanco sin que fallara nada."""
    import inspect

    from api.routers.manager import logs as router
    src = inspect.getsource(router._fetch_logs_cached)
    for campo in ("ts_epoch", "priority", "servicio", "message"):
        assert f'"{campo}"' in src


# ── el nivel sale del TEXTO (el bug de la primera corrida) ───────────────────

def test_el_nivel_se_lee_del_TEXTO_porque_journald_dice_info_a_todo():
    """**El bug que devolvió CERO en 24 horas con 14 motores andando.** Ninguna
    unit declara `SyslogLevelPrefix`, así que journald marca TODAS las líneas
    como `info` — también las de `logger.error`. Pedirle `-p warning` devuelve
    vacío siempre, y ese vacío se lee igual que «no hay errores»."""
    assert ls.nivel_de_texto("2026-08-20 11:02 ERROR no pude suscribir") == 3
    assert ls.nivel_de_texto("Traceback (most recent call last):") == 3
    assert ls.nivel_de_texto("WARNING reconectando") == 4
    assert ls.nivel_de_texto("CRITICAL se cayó todo") == 2


def test_una_linea_que_HABLA_de_errores_no_es_un_error():
    """En MAYÚSCULAS a propósito: es lo que imprime `%(levelname)s` de Python.
    Si se buscara sin distinguir, «0 errores» y «sin warnings» —que son
    justamente las líneas que dicen que todo salió bien— entrarían como
    problemas, y el detector nacería gritando."""
    assert ls.nivel_de_texto("0 errores encontrados") is None
    assert ls.nivel_de_texto("sin warnings hoy") is None
    assert ls.nivel_de_texto("proceso terminado ok") is None


def test_gana_LO_PEOR_entre_journald_y_el_texto(monkeypatch):
    """Hay servicios que sí mandan el nivel de verdad (cloudflared, systemd). Si
    se le creyera solo al texto, se cambiaría un punto ciego por otro."""
    import json
    salida = "\n".join(json.dumps(d) for d in [
        # journald dice info, el texto dice ERROR → gana ERROR
        {"__REALTIME_TIMESTAMP": "1000000", "PRIORITY": "6",
         "_SYSTEMD_UNIT": "motor_rofex.service", "MESSAGE": "ERROR feo"},
        # journald dice crit, el texto no dice nada → gana crit
        {"__REALTIME_TIMESTAMP": "2000000", "PRIORITY": "2",
         "_SYSTEMD_UNIT": "cloudflared.service", "MESSAGE": "se cayó"},
    ])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: type(
        "P", (), {"returncode": 0, "stdout": salida, "stderr": ""})())
    lineas = ls.leer(["x"])["lineas"]
    assert lineas[0]["nivel"] == "error" and lineas[0]["lo_dice_el_texto"]
    assert lineas[1]["nivel"] == "crit" and not lineas[1]["lo_dice_el_texto"]


def test_se_filtra_EN_EL_SERVIDOR_con_grep():
    """Sin `--grep` habría que traerse 24 h de logs de 14 motores para descartar
    el 99%. El patrón es el mismo criterio que la clasificación, escrito una vez."""
    assert "--grep" in ls._cmd(["x"], "-24h", 7, 100, ls.PATRON_NIVEL)
    for palabra in ("ERROR", "WARNING", "CRITICAL", "Traceback"):
        assert palabra in ls.PATRON_NIVEL


def test_si_journalctl_no_soporta_grep_se_reintenta_SIN_el(monkeypatch):
    """`--grep` no existe en journalctl viejo. Traer de más y filtrar en Python
    es peor, pero es mucho mejor que no mirar — y que la única señal sea un
    vacío indistinguible de «no hay errores»."""
    intentos = []

    def _run(cmd, **k):
        intentos.append(cmd)
        ok = "--grep" not in cmd
        return type("P", (), {"returncode": 0 if ok else 1,
                              "stdout": "", "stderr": "unknown option"})()
    monkeypatch.setattr(subprocess, "run", _run)
    r = ls.leer(["x"], grep=ls.PATRON_NIVEL)
    assert r["disponible"] is True
    assert len(intentos) == 2 and "--grep" not in intentos[1]


# ── el formato de log ────────────────────────────────────────────────────────

def test_TODOS_los_motores_escriben_el_nivel():
    """El invariante que hace encontrable un error. 9 de 13 motores formateaban
    con `"%(asctime)s %(message)s"` —sin el nombre del nivel— y `engines/valores`
    (motor_rofex, el feed de precios de la mesa) no configuraba logging en
    absoluto: sus `logger.info` se descartaban y sus `logger.error` salían a
    stderr sin fecha. Un log donde no se puede encontrar un error no es un log."""
    import pathlib

    from core.logs import FORMATO
    assert "%(levelname)s" in FORMATO

    malos = []
    for f in sorted(pathlib.Path("engines").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        if "logging.getLogger" not in src and "configurar(" not in src:
            continue            # no loguea: no aplica
        if f.name.startswith("_") or f.name == "__init__.py":
            continue            # librerías: las configura su entrypoint
        if "configurar(" in src:
            continue            # usa el formato único
        if "basicConfig" in src and "%(levelname)s" not in src:
            malos.append(f.name)
    assert not malos, (f"estos motores loguean sin el NIVEL: {malos} — un error "
                       "suyo es indistinguible de una línea normal")


# ── EL DETECTOR, calibrado con la corrida real del 2026-08-20 ────────────────
#
# Los casos NO son inventados: son los seis patrones que aparecieron en 24 h
# sobre 14 motores. Un detector calibrado a ojo es un detector que grita todos
# los días hasta que alguien lo silencia, y con él se van los avisos que sí
# importan.

def _grupo(unidad, patron, veces, dur_s, prio=4, muestra=""):
    return {"unidad": unidad, "patron": patron, "veces": veces,
            "primera": 0.0, "ultima": float(dur_s), "peor": prio,
            "nivel": ls.NIVEL[prio], "muestra": muestra or patron}


def test_una_RAFAGA_y_algo_que_MACHACA_no_son_lo_mismo():
    """**Lo que la cuenta sola no separa.** En la medición había 76 repeticiones
    en 3 minutos y 91 en 6.7 horas: números parecidos, problemas distintos. La
    primera es algo rompiéndose AHORA en loop; la segunda es una configuración
    rota desde hace días que nadie mira. Un solo umbral los mezcla y entonces o
    se pierde la urgencia de una, o se ignora la otra."""
    from api.services.av_agent_motores import _hallazgo_log

    rafaga = _hallazgo_log(_grupo("motor_cedears", "REST exception JSONDecodeError",
                                  76, 3 * 60))
    machaca = _hallazgo_log(_grupo("motor_options", "Expiries ya vencidas",
                                   91, int(6.7 * 3600)))
    assert rafaga["regla"] == "rafaga" and rafaga["severidad"] == "alta"
    assert machaca["regla"] == "machaca" and machaca["severidad"] == "media"


def test_un_WARN_suelto_NO_es_un_hallazgo():
    """Los tres warn sueltos de la medición se anunciaban resolviéndose solos
    («reconectando (intento 1)», «purgo y resuscribo sin ellos»). Reportarlos
    enseña a cerrar la pantalla sin leerla — y con ella se van los que importan.
    El diag los sigue mostrando cuando alguien va a buscarlos."""
    from api.services.av_agent_motores import _hallazgo_log
    assert _hallazgo_log(_grupo("motor_ordenes", "Recovery: UNKNOWN_LOCAL", 1, 0)) is None
    assert _hallazgo_log(_grupo("motor_portfolio_snapshot", "reconectando", 1, 0)) is None


def test_un_ERROR_suelto_SI_es_un_hallazgo():
    """Un warn que se repite una vez es ruido; un ERROR una sola vez no. La
    asimetría es a propósito: el nivel ya es el juicio de quien escribió el
    motor sobre su propia línea."""
    from api.services.av_agent_motores import _hallazgo_log
    h = _hallazgo_log(_grupo("motor_portfolio_snapshot", "símbolo inexistente",
                             1, 0, prio=3))
    assert h["regla"] == "error_de_motor" and h["severidad"] == "media"
    # ⚠️ Y repetido NO puede bajar de categoría. La primera versión probaba la
    # forma antes que el nivel, así que un ERROR 40 veces caía en «machaca» con
    # severidad MEDIA: el que más repite era el que menos se veía. Lo encontró
    # este test antes de que llegara a producción.
    muchos = _hallazgo_log(_grupo("motor_x", "boom", 40, 3600, prio=3))
    assert muchos["regla"] == "machaca" and muchos["severidad"] == "alta"
    # El mismo volumen en warn sí es media: lo que cambia es el nivel.
    igual_pero_warn = _hallazgo_log(_grupo("motor_x", "boom", 40, 3600, prio=4))
    assert igual_pero_warn["severidad"] == "media"


def test_los_hallazgos_llevan_la_CUENTA_y_la_VENTANA():
    """Sin las dos, el que lee no puede decidir: «×76» sin «en 3 minutos» no
    distingue una caída de un goteo."""
    from api.services.av_agent_motores import _hallazgo_log
    h = _hallazgo_log(_grupo("motor_cedears", "x", 76, 180))
    assert h["evidencia"]["veces"] == 76
    assert "min" in h["evidencia"]["ventana"] or "s" in h["evidencia"]["ventana"]
    assert "76" in h["motivo"]


def test_si_NO_SE_PUEDEN_LEER_los_logs_eso_ES_el_hallazgo(monkeypatch):
    """El caso que decide si esta vigilancia sirve o miente. Sin este hallazgo,
    un host sin journal daría verde para siempre — y nadie tendría forma de
    notar que hace meses no se está mirando nada."""
    from api.services import av_agent_motores as m
    monkeypatch.setattr(ls, "atencion",
                        lambda *a, **k: {"disponible": False, "lineas": [],
                                         "motivo": "no hay journalctl"})
    h = m.detectar_logs()
    assert len(h) == 1 and h[0]["regla"] == "no_pude_leer"
    assert "no sé cómo están" in h[0]["evidencia"]["texto"]


def test_un_fallo_leyendo_logs_NO_apaga_los_otros_detectores(monkeypatch):
    """Corre adentro del monitor de rueda, junto a los de precio. Una excepción
    acá no puede llevarse puesto el ciclo entero."""
    from api.services import av_agent_motores as m

    def _boom(*a, **k):
        raise RuntimeError("journal corrupto")
    monkeypatch.setattr(ls, "atencion", _boom)
    assert m.detectar_logs() == []


def test_el_monitor_de_rueda_CORRE_este_detector():
    """La skill dice que se ve en ENCONTRÓ. Si el job no lo llamara, la promesa
    del catálogo sería falsa y nadie se enteraría — ya pasó con tres detectores
    que solo imprimían en el log (AV_AGENT.md §0.t)."""
    import inspect

    from api.services import av_agent
    src = inspect.getsource(av_agent.relevar_live)
    assert "detectar_logs" in src
