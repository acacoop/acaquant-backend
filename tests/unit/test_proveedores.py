"""LOS DE AFUERA SE CAEN — `core/proveedores` + `av_agent_proveedores`.

Nace de una caída de Aunesa (HTTP 500 en su login) que el user vio **de milagro**
al abrir Tesorería. La vista YA la detectaba y la mostraba bien; el problema es
que ese cartel solo existe mientras alguien tiene la pantalla abierta. Si nadie
entra, el back office puede pasar la mañana creyendo que el saldo del día está
completo cuando le falta la mitad.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import ClassVar

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
    assert h["motivo"].startswith("AUNESA CAÍDO")                    # QUIÉN
    assert "Tesorería" in h["evidencia"]["texto"]                    # QUÉ ROMPE
    assert "500 Server Error" in h["motivo"]                         # EL MOTIVO
    assert h["evidencia"]["donde"] == "login"                        # DÓNDE


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

def test_el_monitoreo_CONTINUO_no_sale_a_la_red(monkeypatch):
    """**La regla se acotó, no se levantó** (2026-08-20). El user pidió que el
    agente pueda LLAMAR a Aunesa para entender el error, y eso está bien: contesta
    la pregunta que el rastro no puede, «¿es de ellos o es nuestro?».

    Lo que no cambia es que **el camino feliz no toca la red**: 1816 cobra por
    llamada, y un chequeo cada 5 minutos se come la cuota del día antes del
    mediodía. Con todos los proveedores sanos, cero requests."""
    tocaron = []
    monkeypatch.setattr(det, "_probar", lambda p: tocaron.append(p))
    monkeypatch.setattr(pr, "estado", lambda: [_fila(ok=True)])
    assert det.detectar_proveedores() == []
    assert tocaron == [], "con todo sano no se le pregunta a nadie"


def test_cuando_YA_FALLO_si_se_prueba_y_se_dice_de_quien_es(monkeypatch):
    """*«El agente sí o sí tiene que poder llamar a Aunesa para ver la conexión y
    entender el error»* (user). Ahí la request extra es barata —ya sabemos que
    algo falló— y es la única forma de separar «se cayeron ellos» de «se nos
    venció una credencial», que se arreglan en lugares distintos."""
    monkeypatch.setattr(det, "_probar", lambda p: {
        "veredicto": "caido", "status": 500,
        "detalle": "HTTP 500 · error interno de su servidor"})
    # Sin barrido, para que se vea la línea de la PRUEBA: el barrido, cuando
    # está, la reemplaza porque dice lo mismo y además cuántas APIs caen.
    monkeypatch.setattr(det, "_barrer_si_toca", lambda p: None)
    monkeypatch.setattr(pr, "estado", lambda: [_fila()])
    h = det.detectar_proveedores()[0]
    assert "error interno de su servidor" in h["evidencia"]["texto"]
    assert h["evidencia"]["prueba"]["status"] == 500


def test_la_prueba_NUNCA_manda_credenciales():
    """Un login fallido repetido bloquea la cuenta. Y para saber si el host está
    vivo no hacen falta: **justamente porque no las manda**, un 5xx prueba que se
    rompió antes de leerlas — que es todo el valor de la prueba."""
    import inspect
    src = inspect.getsource(pr.probar)
    for prohibido in ("AUNESA_PASSWORD", "AUNESA_USERNAME", "password",
                      "auth_headers"):
        assert prohibido not in src, f"la prueba manda «{prohibido}»"
    assert "json={}" in src, "el body tiene que ir vacío"


def test_la_prueba_NO_toca_a_los_que_COBRAN():
    """1816 cobra por llamada. Probarlo sería gastar cuota para saber algo que su
    propio uso ya cuenta."""
    r = pr.probar("1816")
    assert r["veredicto"] == "no_se_puede_probar"


def test_un_500_es_DE_ELLOS_y_un_401_no(monkeypatch):
    """El contrato que publica el custodio: 200/204 anda, 400/401/403 anda y
    rechaza (correcto sin credenciales), 500 es error interno suyo. Sin esto, un
    403 y un 500 se leen igual: «no anda»."""
    class _R:
        def __init__(self, code, tipo="application/json", texto="{}"):
            self.status_code, self.text = code, texto
            self.headers = {"content-type": tipo}

        def json(self):
            import json
            return json.loads(self.text)

    import requests
    for code, esperado in ((200, "anda"), (204, "anda"), (400, "anda"),
                           (401, "anda"), (403, "anda"), (500, "caido")):
        monkeypatch.setattr(requests, "post", lambda *a, c=code, **k: _R(c))
        assert pr.probar("aunesa")["veredicto"] == esperado, code


def test_un_500_en_HTML_dice_que_se_rompio_ANTES_de_su_manejador(monkeypatch):
    """Aunesa documenta que un 500 vuelve como `{"errors":[{title,detail}]}`.
    Cuando en vez de eso llega el HTML de Tomcat, se rompió antes de llegar a su
    propio manejador de errores: no es una condición prevista por su aplicación,
    es su aplicación caída. Es la diferencia entre «me rechazaron» y «se les
    cayó», y fue exactamente lo que pasó el 2026-08-20."""
    class _R:
        status_code = 500
        headers: ClassVar = {"content-type": "text/html;charset=utf-8"}
        text = "<!doctype html><html><title>Estado HTTP 500</title>"

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _R())
    d = pr.probar("aunesa")["detalle"]
    assert "página de error" in d and "rompió" in d


def test_si_contesta_en_SU_formato_se_citan_SUS_palabras(monkeypatch):
    class _R:
        status_code = 500
        headers: ClassVar = {"content-type": "application/json"}
        text = '{"errors":[{"title":"Error interno","detail":"base no responde"}]}'

        def json(self):
            import json
            return json.loads(self.text)

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _R())
    d = pr.probar("aunesa")["detalle"]
    assert "Error interno" in d and "base no responde" in d


def test_si_ni_contesta_tambien_es_una_respuesta(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(
                            requests.exceptions.ConnectionError("sin ruta")))
    r = pr.probar("aunesa")
    assert r["alcanzable"] is False and r["veredicto"] == "caido"


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


# ── EL BARRIDO: ¿le pasa a TODAS las APIs o a una sola? ──────────────────────

def _resp(code, tipo="application/json", texto="{}"):
    class _R:
        status_code = code
        text = texto

        def __init__(self):
            self.headers = {"content-type": tipo}

        def json(self):
            import json
            return json.loads(self.text)
    return _R()


def test_si_el_LOGIN_esta_caido_NO_se_prueba_nada_mas(monkeypatch):
    """Sin token no se puede probar ninguna API, y eso NO es una limitación del
    barrido: **es el diagnóstico**. Juntar cinco 401 para decir lo mismo sería
    ruido, y encima carga contra un servicio que ya está mal."""
    monkeypatch.setattr(pr, "probar",
                        lambda p="aunesa": {"veredicto": "caido",
                                            "detalle": "HTTP 500"})
    r = pr.barrer_aunesa()
    assert r["veredicto"] == "todo_caido" and r["endpoints"] == []
    assert "no entra nada" in r["analisis"]


def test_si_fallan_TODAS_es_su_servicio_entero(monkeypatch):
    filas = [{"path": p, "para_que": q, "ok": False, "status": 500}
             for p, q, _ in pr.ENDPOINTS_AUNESA]
    a = pr._analizar(filas)
    assert a["veredicto"] == "todo_caido" and "las 5" in a["analisis"]


def test_si_falla_UNA_se_dice_que_el_RESTO_anda(monkeypatch):
    """La mitad útil del análisis parcial. Sin decirlo, el equipo da por perdido
    el día entero cuando en realidad cuatro de las cinco APIs siguen entrando."""
    filas = [{"path": p, "para_que": q, "ok": i > 0, "status": 500 if i == 0 else 200}
             for i, (p, q, _) in enumerate(pr.ENDPOINTS_AUNESA)]
    a = pr._analizar(filas)
    assert a["veredicto"] == "parcial"
    assert "El resto entra bien" in a["analisis"]
    assert "padrón de cuentas" in a["analisis"], "nombra QUÉ se rompió"


def test_el_barrido_cubre_las_APIS_QUE_USAMOS_DE_VERDAD():
    """No una lista de ejemplo: si falta una, el análisis dice «anda todo» sobre
    algo que nadie probó. Estas cinco son las que aparecen en el código."""
    paths = {p for p, _, _ in pr.ENDPOINTS_AUNESA}
    for usada in ("cuentas/listadoCuentas",
                  "cuentas/consultaMovDocsSolicitados",
                  "operaciones/consolidadosGenerales",
                  "operaciones/informes"):
        assert usada in paths, f"falta {usada}, que el sistema usa"
    assert any("posiciones" in p for p in paths), "faltan los saldos liquidados"
    for _p, para_que, _ in pr.ENDPOINTS_AUNESA:
        assert para_que, "cada API dice para qué sirve, o el análisis no se lee"


def test_el_barrido_tiene_FRENO(monkeypatch):
    """El monitor corre cada 5 minutos y el barrido son 6 requests. Sin freno,
    una caída de una hora son 72 requests contra un proveedor que ya sabemos que
    está mal — la peor hora para agregarle carga."""
    veces = []
    monkeypatch.setattr(pr, "barrer_aunesa",
                        lambda: veces.append(1) or {"veredicto": "todo_caido",
                                                    "analisis": "x"})
    det._ultimo_barrido.clear()
    for _ in range(12):
        det._barrer_si_toca("aunesa")
    assert len(veces) == 1, "barrió más de una vez adentro de la ventana"


# ── AVISAR DIRECTO ───────────────────────────────────────────────────────────

def test_el_aviso_le_llega_A_QUIEN_LO_SUFRE_no_solo_al_admin(monkeypatch):
    """ENCONTRÓ es admin-only y el que sufre que Aunesa esté caído es el back
    office, que ni ve esa pantalla. Un hallazgo que solo mira un admin no es un
    aviso para el que tiene que actuar."""
    import inspect
    src = inspect.getsource(det._a_quien)
    assert "tesoreria_escritores" in src, "no le avisa al back office"
    assert "role = 'admin'" in src, "si la allowlist está vacía, nadie se entera"


def test_el_ANALISIS_GENERAL_va_PRIMERO_en_el_aviso(monkeypatch):
    """«Fallan las cinco» o «falla una» es lo que decide qué hacer; el detalle
    técnico es el respaldo. Al revés, el que lo lee tiene que atravesar un
    traceback para llegar a lo único accionable."""
    enviados = []
    import api.services.av_agent_mensajes as msg
    monkeypatch.setattr(det, "_a_quien", lambda: ["x@y.com"])
    monkeypatch.setattr(msg, "enviar_muchos",
                        lambda m, **k: enviados.append(m) or {"enviados": len(m)})
    h = {"ticker": "aunesa", "motivo": "Aunesa no responde",
         "evidencia": {"texto": "MOTIVO EXACTO: 500"}}
    det.avisar_caida(h, barrido={"analisis": "Fallan las cinco"})
    cuerpo = enviados[0][0]["detalle"]
    assert cuerpo.startswith("Fallan las cinco")


def test_sin_destinatarios_el_aviso_lo_DICE(monkeypatch):
    """Un «enviados: 0» silencioso se lee igual que «no hacía falta avisar»."""
    monkeypatch.setattr(det, "_a_quien", list)
    r = det.avisar_caida({"ticker": "aunesa", "motivo": "x", "evidencia": {}})
    assert r["enviados"] == 0 and "nadie a quien avisar" in r["error"]


def test_un_aviso_por_DIA_y_no_por_corrida(monkeypatch):
    """El monitor corre cada 5 minutos: sin esto son 84 mensajes idénticos en una
    jornada, y el 84º informa menos que el primero."""
    import inspect
    src = inspect.getsource(det.avisar_caida)
    assert "date.today().isoformat()" in src, "el tema no lleva la fecha"
