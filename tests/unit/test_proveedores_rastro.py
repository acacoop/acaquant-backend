"""NINGÚN CLIENTE DE UN PROVEEDOR SE QUEDA MUDO.

El 2026-08-20 **el AuM no se escribió**: `jobs/portafolio_backfill` murió a las
11:00 con un `500` de Aunesa. Y el detector de caídas no vio nada, porque ese job
le pega a Aunesa con su propio `requests` en vez de `core/aunesa` — su fallo no
dejaba rastro. Sin rastro no hay `proveedor_caido`; sin eso, la correlación no
existe y el aviso queda en «la última corrida falló», sin decir por qué.

> El modo de falla no es que el detector esté mal: es que **le falta el dato**, y
> eso no lo denuncia nadie.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from core import proveedores as pr

_RAIZ = pathlib.Path(__file__).resolve().parents[2]


# ── el rastro en sí ──────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, code, url="https://aca.aunesa.com/Irmo/api/login?x=1"):
        self.status_code, self.url = code, url


def test_un_500_marca_al_proveedor_CAIDO(monkeypatch):
    visto = {}
    monkeypatch.setattr(pr, "anotar", lambda p, **k: visto.update({"p": p, **k}))
    pr.mirar(_Resp(500))
    assert visto["p"] == "aunesa" and visto["ok"] is False
    assert "500" in visto["error"]
    # El «dónde» tiene que ser la RUTA, no la URL entera con sus parámetros.
    assert visto["donde"] == "/Irmo/api/login"


def test_un_200_marca_que_ANDA():
    visto = {}
    pr.anotar_original = pr.anotar
    try:
        pr.anotar = lambda p, **k: visto.update({"p": p, **k})
        pr.mirar(_Resp(200))
    finally:
        pr.anotar = pr.anotar_original
    assert visto["ok"] is True


def test_lo_que_no_es_una_respuesta_NO_rompe():
    """Corre adentro de una request real: el instrumento no puede ser la causa
    de la falla que estaba tratando de describir."""
    for basura in (None, object(), _Resp("no-es-un-numero"), _Resp(0)):
        pr.mirar(basura)          # no levanta y no anota


def test_el_hook_de_la_SESSION_no_reemplaza_la_respuesta():
    """⚠️ Trampa de `requests`: si un hook de respuesta DEVUELVE algo, ese algo
    **reemplaza** la respuesta. Devolver `resp` sería inocuo, pero devolver
    cualquier otra cosa —o el `None` de `mirar`— rompería a todos los llamadores.
    """
    class _S:
        def __init__(self):
            self.hooks = {}

    s = _S()
    pr.rastrear(s, "aunesa")
    hook = s.hooks["response"][0]
    r = _Resp(200)
    assert hook(r) is None, "un hook que devuelve algo PISA la respuesta"


def test_rastrear_no_rompe_con_una_session_rara():
    class _Raro:
        hooks = None
    pr.rastrear(_Raro(), "aunesa")     # no levanta


# ── LA GUARDA: quien le habla a un proveedor, deja rastro ────────────────────

def _modulos_que_le_hablan_a(host: str) -> list[pathlib.Path]:
    out = []
    for raiz in ("jobs", "engines", "api", "core"):
        for f in (_RAIZ / raiz).rglob("*.py"):
            try:
                t = f.read_text(encoding="utf-8")
            except OSError:
                continue
            # El propio catálogo y este test nombran el host para declararlo.
            if host in t and f.name not in ("dependencias.py", "proveedores.py"):
                out.append(f)
    return out


# Las formas VÁLIDAS de dejar rastro. `sesion_vigilada`/`vigilar` enganchan un
# hook a la sesión y cubren TODA llamada de ese archivo, presente y futura — son
# mejores que `mirar` suelto, que hay que acordarse de poner en cada respuesta.
_FORMAS = ("sesion_vigilada", "vigilar", "mirar", "rastrear", "anotar")


def _deja_rastro(f: pathlib.Path) -> bool:
    """O pasa por el cliente único, o engancha el rastro de alguna de `_FORMAS`."""
    t = f.read_text(encoding="utf-8")
    if "core.aunesa" in t or "from core import aunesa" in t:
        return True
    arbol = ast.parse(t)
    for n in ast.walk(arbol):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in _FORMAS):
            return True
    return False


@pytest.mark.parametrize(
    "f", _modulos_que_le_hablan_a("aca.aunesa.com"),
    ids=lambda f: f.name)
def test_todo_modulo_que_le_pega_a_AUNESA_deja_rastro(f):
    """**La guarda que faltaba.** Un cliente suelto más, mañana, vuelve a dejar
    al agente ciego — y como no falla nada, nadie se entera hasta que el AuM no
    se escribe. Que el test lo cace es la única forma de que no vuelva a pasar.

    Se cumple de dos maneras: pasando por `core/aunesa` (lo correcto, y sigue
    pendiente para estos cuatro) o llamando a `proveedores.mirar` en cada
    respuesta.
    """
    assert _deja_rastro(f), (
        f"{f.relative_to(_RAIZ)} le habla a Aunesa y no deja rastro: su fallo "
        f"va a ser invisible para el detector de caídas. Usá `core/aunesa` o "
        f"pedile la sesión a `proveedores.sesion_vigilada(...)`.")


def test_la_guarda_encuentra_a_los_CUATRO_sueltos():
    """Si el escaneo dejara de encontrar archivos, el test de arriba pasaría en
    verde sin mirar nada — el modo de falla clásico de un test parametrizado."""
    nombres = {f.name for f in _modulos_que_le_hablan_a("aca.aunesa.com")}
    assert {"aum.py", "cashflow.py", "sync_comitentes.py",
            "aunesa_negocio.py"} <= nombres, nombres


# ── UN solo criterio de «está caído» (REGLA #9) ──────────────────────────────

def test_un_400_NO_es_una_caida_del_proveedor():
    """**El bug que esto congela.** Hubo dos caminos para dejar rastro y cada
    uno tenía su umbral: `mirar` marcaba caída desde 400 y el hook de la sesión
    desde 500. Enganchados los dos a la misma llamada, un 400 escribía «AUNESA
    CAÍDO» y enseguida «recuperado»: una caída inventada, prendiéndose y
    apagándose sola. `jobs/cashflow` maneja el 400 de Aunesa explícitamente, o
    sea que no era hipotético.

    Un 4xx es un problema NUESTRO: pedimos mal, o el token venció.
    """
    for c in (400, 401, 404, 422):
        assert pr.es_caida(c) is False, c
    for c in (500, 502, 503):
        assert pr.es_caida(c) is True, c


def test_los_dos_caminos_deciden_IGUAL():
    """No alcanza con que el umbral esté bien: tiene que estar escrito UNA vez.
    Si mañana alguien cambia uno solo, el sistema vuelve a inventar caídas y
    nada falla — las dos mitades siguen siendo coherentes consigo mismas."""
    import inspect
    for fn in (pr.mirar, pr.vigilar):
        assert "es_caida" in inspect.getsource(fn), (
            f"{fn.__name__} decide con su propio umbral en vez de `es_caida`")


def test_rastrear_es_vigilar_y_no_una_segunda_implementacion():
    import inspect
    assert "vigilar(" in inspect.getsource(pr.rastrear)


def test_la_sesion_no_se_engancha_DOS_veces():
    """`jobs/aum` tenía `rastrear` y `vigilar` sobre la MISMA sesión: dos hooks,
    dos anotaciones por respuesta."""
    class _S:
        def __init__(self):
            self.hooks = {}
    s = _S()
    pr.vigilar(s, "aunesa")
    pr.rastrear(s, "aunesa")
    pr.vigilar(s, "aunesa", "otro")
    assert len(s.hooks["response"]) == 1
