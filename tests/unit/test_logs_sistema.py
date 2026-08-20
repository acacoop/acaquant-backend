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
    a = ls.patron("2026-08-20 11:02:03 conexión caída")
    b = ls.patron("2026-08-19 23:59:00 conexión caída")
    assert a == b and "<fecha>" in a


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
