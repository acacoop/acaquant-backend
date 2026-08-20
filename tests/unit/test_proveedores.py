"""LOS DE AFUERA SE CAEN — `core/proveedores` + `av_agent_proveedores`.

Nace de una caída de Aunesa (HTTP 500 en su login) que el user vio **de milagro**
al abrir Tesorería. La vista YA la detectaba y la mostraba bien; el problema es
que ese cartel solo existe mientras alguien tiene la pantalla abierta. Si nadie
entra, el back office puede pasar la mañana creyendo que el saldo del día está
completo cuando le falta la mitad.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services import av_agent_proveedores as det
from core import proveedores as pr


def _fila(**kw):
    base = {"proveedor": "aunesa", "ok": False,
            "ultimo_error": "HTTPError: 500 Server Error for url: "
                            "https://aca.aunesa.com/Irmo/api/login",
            "ultimo_error_at": datetime.now(UTC) - timedelta(minutes=2),
            "ultimo_ok_at": datetime.now(UTC) - timedelta(hours=3),
            "fallos_seguidos": 4, "donde": "login",
            "actualizado_at": datetime.now(UTC)}
    return {**base, **kw}


# ── el aviso tiene que decir TRES cosas ──────────────────────────────────────

def test_el_aviso_dice_QUIEN_QUE_SE_ROMPE_y_EL_MOTIVO_EXACTO(monkeypatch):
    """Las tres estaban en el cartel que el user vio de milagro, y las tres hacen
    falta. Sin el error textual no se distingue «se cayó el proveedor» de «se nos
    vencieron las credenciales», que se resuelven en lugares distintos y por
    personas distintas."""
    monkeypatch.setattr(pr, "estado", lambda: [_fila()])
    h = det.detectar_proveedores()[0]
    assert h["severidad"] == "alta"
    assert "Aunesa" in h["motivo"]                                   # QUIÉN
    assert "Tesorería" in h["evidencia"]["texto"]                    # QUÉ ROMPE
    assert "500 Server Error" in h["evidencia"]["texto"]             # EL MOTIVO
    assert "login" in h["evidencia"]["texto"]                        # DÓNDE


def test_una_caida_VIEJA_no_sigue_en_pantalla(monkeypatch):
    """Nadie apaga el registro cuando el proveedor se recupera: simplemente dejan
    de anotarse fallos. Sin ventana, un 500 de la semana pasada seguiría avisando
    para siempre — y una pantalla con un problema viejo enseña a ignorarla, que
    es exactamente lo que pasó con los hallazgos de rueda (§0.u)."""
    vieja = datetime.now(UTC) - timedelta(hours=5)
    monkeypatch.setattr(pr, "estado", lambda: [_fila(ultimo_error_at=vieja)])
    assert det.detectar_proveedores() == []


def test_un_proveedor_que_ANDA_no_genera_nada(monkeypatch):
    monkeypatch.setattr(pr, "estado", lambda: [_fila(ok=True)])
    assert det.detectar_proveedores() == []


def test_si_NO_SE_PUEDE_LEER_el_estado_no_se_cae_el_ciclo(monkeypatch):
    """Corre adentro del monitor de rueda, junto a los detectores de precio."""
    def _boom():
        raise RuntimeError("sin base")
    monkeypatch.setattr(pr, "estado", _boom)
    assert det.detectar_proveedores() == []


def test_TODO_proveedor_declara_QUE_SE_ROMPE_si_se_cae():
    """Es la mitad útil del aviso. «Se cayó X» obliga al que lee a averiguar si
    eso le arruina el día o no le toca en nada."""
    for clave, p in pr.PROVEEDORES.items():
        assert p.rompe and len(p.rompe) > 20, f"«{clave}» no dice qué rompe"
        assert p.host and "." in p.host


# ── cómo se entera: sin health checks ────────────────────────────────────────

def test_NO_se_le_pregunta_a_nadie_por_su_salud():
    """Dos motivos: **1816 cobra por llamada** —un ping cada 5 minutos se come la
    cuota del día antes del mediodía— y **un health check puede mentir**: un
    proveedor que contesta el ping y devuelve 500 en el endpoint que usamos de
    verdad sale verde. El rastro lo dejan las llamadas REALES."""
    import inspect
    src = inspect.getsource(det) + inspect.getsource(pr)
    for prohibido in ("requests.get", "requests.post", "httpx", "urlopen"):
        assert prohibido not in src, (
            f"«{prohibido}»: el detector no puede salir a la red — el estado se "
            "lee del rastro que dejan las llamadas reales")


def test_un_OK_detras_de_otro_OK_no_escribe(monkeypatch):
    """Se llama desde adentro de un cliente HTTP, o sea en el camino de cada
    request. Si escribiera siempre, un proveedor sano costaría una escritura por
    llamada — y los daemons llaman todo el día."""
    escrituras = []
    monkeypatch.setattr(pr, "_guardar",
                        lambda p, **k: escrituras.append((p, k["ok"])))
    pr._ultimo.clear()
    pr.anotar("aunesa", ok=True)
    pr.anotar("aunesa", ok=True)
    assert escrituras == [], "un proveedor sano no puede costar escrituras"


def test_el_FALLO_se_escribe_y_la_RECUPERACION_tambien(monkeypatch):
    """El fallo es la señal; la recuperación es la que apaga el aviso rápido en
    vez de esperar a que venza la ventana."""
    escrituras = []
    monkeypatch.setattr(pr, "_guardar",
                        lambda p, **k: escrituras.append((p, k["ok"])))
    pr._ultimo.clear()
    pr.anotar("aunesa", ok=False, error="500")
    pr.anotar("aunesa", ok=True)
    assert escrituras == [("aunesa", False), ("aunesa", True)]


def test_una_caida_larga_no_escribe_mil_veces(monkeypatch):
    """Un daemon reintentando contra un proveedor caído puede fallar decenas de
    veces por minuto. Sin freno, una caída de una hora son miles de escrituras
    para decir siempre lo mismo."""
    escrituras = []
    monkeypatch.setattr(pr, "_guardar",
                        lambda p, **k: escrituras.append(p))
    pr._ultimo.clear()
    for _ in range(50):
        pr.anotar("aunesa", ok=False, error="500")
    assert len(escrituras) == 1


def test_anotar_NUNCA_levanta(monkeypatch):
    """Se llama en el camino de una request real: si fallara, rompería la llamada
    que estaba tratando de describir. El instrumento no puede ser la causa."""
    def _boom(*a, **k):
        raise RuntimeError("base caída")
    monkeypatch.setattr(pr, "_guardar", _boom)
    pr._ultimo.clear()
    pr.anotar("aunesa", ok=False, error="x")     # no debe levantar


def test_un_proveedor_DESCONOCIDO_se_ignora(monkeypatch):
    escrituras = []
    monkeypatch.setattr(pr, "_guardar", lambda p, **k: escrituras.append(p))
    pr._ultimo.clear()
    pr.anotar("no_existe", ok=False)
    assert escrituras == []


# ── el rastro se deja donde pasa ─────────────────────────────────────────────

def test_el_CLIENTE_de_aunesa_deja_el_rastro():
    """Un solo lugar: si cada caller tuviera que acordarse, el que se olvide deja
    un agujero invisible. Y tiene que anotar también el status ≥400, que NO
    levanta excepción — el caller decide qué tolerar, así que un 500 repetido
    pasaría entero sin dejar rastro."""
    import inspect

    from core import aunesa
    src = inspect.getsource(aunesa)
    assert src.count("proveedores.anotar") >= 4
    assert "status_code < 400" in src


def test_el_monitor_de_rueda_CORRE_este_detector():
    import inspect

    from api.services import av_agent
    assert "detectar_proveedores" in inspect.getsource(av_agent.relevar_live)
