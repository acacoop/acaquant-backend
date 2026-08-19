"""¿LOS PERMISOS SON REALES O ESTÁN EN LOS PAPELES? — `av_agent_seguridad`.

    *«Que sea capaz de detectar si algún endpoint está mal hecho y se puede
    consultar a la fuerza por tener solo permisos "en los papeles". Una vez me
    había pasado que Vercel me dejaba todo sin protección y no me daba cuenta.»*

Lo que se congela son los límites que hacen que una prueba activa sea aceptable
—porque un agente pidiendo endpoints sin credenciales es, técnicamente, un
scanner— y que el resultado no dé falsa tranquilidad.
"""
from __future__ import annotations

from typing import ClassVar

from api import superficie
from api.services import av_agent_seguridad as seg

# ── La superficie: ver el 100%, no el 7% ───────────────────────────────────

def test_se_ven_TODAS_las_rutas_no_las_de_primer_nivel():
    """**El agujero que originó todo esto.** `app.routes` devuelve envoltorios,
    no rutas: un `for` ingenuo veía 37 de 541 y pasaba en verde."""
    from api.main import app

    assert len(superficie.rutas()) > 400
    assert len(superficie.rutas()) > len(list(app.routes)) * 5


def test_ningun_path_repite_su_prefijo():
    """`gen_mapa_app` duplicaba el prefijo en 395 de 541 paths
    (`/api/ia/api/ia/observabilidad`) — el `prefix` de un `APIRouter` ya viene
    aplicado a sus propias rutas. Un path que no existe es peor que uno faltante:
    cualquier herramienta que intente PROBARLO recibe 404 y concluye que está
    protegido."""
    import re

    malos = [r.path for r in superficie.rutas()
             if re.search(r"(/api/[\w-]+)(?=.*\1)", r.path)]
    assert not malos, f"paths con el prefijo duplicado: {malos[:5]}"


def test_los_gates_HEREDADOS_del_include_se_ven():
    """Las `dependencies=` de un `include_router` NO bajan a cada ruta: viven en
    `include_context`. Sin acumularlas, los 126 endpoints de Manager aparecen
    como «sin gate» estando gateados."""
    manager = [r for r in superficie.rutas() if r.path.startswith("/api/manager/")]
    assert manager
    assert all(r.pide_bearer for r in manager), "el bearer del include se perdió"


def test_NINGUNA_escritura_queda_sin_gate():
    """El invariante que se puede leer del código."""
    assert superficie.resumen()["escrituras_sin_gate"] == []


def test_las_rutas_abiertas_estan_DECLARADAS():
    """Abrir un endpoint tiene que ser una decisión explícita, no un olvido que
    el chequeo deja pasar en silencio."""
    assert seg.declarado()["abiertas_inesperadas"] == []


# ── La prueba activa: los límites que la hacen aceptable ───────────────────

def test_sin_URL_configurada_NO_prueba(monkeypatch):
    """No se inventa la URL. Probar contra el host equivocado y salir en verde es
    peor que no probar — te deja tranquilo justo donde tuviste el incidente."""
    monkeypatch.setattr(seg, "URL_PUBLICA", "")
    r = seg.probar()
    assert r["ok"] is False and r["alcance"] is None


def test_JAMAS_prueba_una_escritura():
    """Probar un POST «a ver si me deja» puede escribir de verdad; un DELETE es
    impensable. Solo GET, y eso no es una convención: está en el filtro."""
    import inspect
    src = inspect.getsource(seg.probar)
    assert '"GET" in r.metodos' in src
    assert ".post(" not in src and ".delete(" not in src and ".put(" not in src


def test_no_ADIVINA_urls(monkeypatch):
    """Solo prueba rutas del inventario propio. Un agente que compone URLs para
    ver qué encuentra dejó de ser un chequeo y pasó a ser otra cosa."""
    import inspect
    src = inspect.getsource(seg.probar)
    assert "superficie.rutas()" in src


def test_no_prueba_rutas_con_parametros():
    """Con `{id}` no se puede armar una URL real, y un valor inventado devuelve
    404 — que no dice NADA sobre permisos. Esas quedan cubiertas por la lectura."""
    import inspect
    assert '"{" not in r.path' in inspect.getsource(seg.probar)


def test_tiene_throttle():
    """Es tráfico contra producción."""
    assert seg.PAUSA_S > 0
    import inspect
    assert "time.sleep(PAUSA_S)" in inspect.getsource(seg.probar)


def test_el_resultado_SIEMPRE_dice_que_capa_cubrio(monkeypatch):
    """**En seguridad una media verdad se lee como un sí.** Un verde que no
    aclara su alcance da falsa tranquilidad justo en la capa del incidente: esta
    prueba pega a la API, NO al front de Vercel."""
    monkeypatch.setattr(seg, "URL_PUBLICA", "https://ejemplo")
    monkeypatch.setattr(superficie, "rutas", list)

    class _S:
        headers: ClassVar[dict] = {}

        def get(self, *a, **k):
            raise AssertionError("no debería llamar: la lista está vacía")

    import requests
    monkeypatch.setattr(requests, "Session", _S)
    r = seg.probar()
    assert r["ok"] is True
    assert "Vercel" in r["alcance"] and "NO cubre" in r["alcance"]


# ── El veredicto ───────────────────────────────────────────────────────────

def test_un_200_sin_credencial_es_el_HALLAZGO(monkeypatch):
    monkeypatch.setattr(seg, "declarado", lambda: {
        "abiertas_inesperadas": [], "total": 1, "sin_gate": [],
        "escrituras_sin_gate": [], "abiertas_declaradas": []})
    monkeypatch.setattr(seg, "probar", lambda: {"ok": True, "filtran": [
        {"path": "/api/portfolio/aum", "status": 200, "bytes": 4200,
         "gates": ["verify_api_key", "require_module_portfolios"]}]})
    h = seg.detectar_seguridad()
    assert len(h) == 1 and h[0]["severidad"] == "alta"
    assert h[0]["evidencia"]["capa"] == "efectivo"
    assert "en los papeles" in h[0]["evidencia"]["texto"]


def test_un_404_NO_es_un_hallazgo():
    """No dice nada en ningún sentido — puede ser una ruta con parámetros o un
    deploy a medias. Reportarlo sería ruido que enseña a ignorar el aviso."""
    assert 404 in seg.MUDOS and 404 not in seg.RECHAZOS


def test_405_cuenta_como_RECHAZO():
    """La ruta existe, el método no aplica: tampoco filtró nada."""
    assert 405 in seg.RECHAZOS


def test_la_evidencia_distingue_LEER_de_PROBAR(monkeypatch):
    """Son dos capas distintas y el que lee tiene que saber cuál falló: una se
    arregla en el router, la otra en el borde."""
    monkeypatch.setattr(seg, "declarado", lambda: {
        "abiertas_inesperadas": ["/api/algo"], "total": 1, "sin_gate": [],
        "escrituras_sin_gate": [], "abiertas_declaradas": []})
    monkeypatch.setattr(seg, "probar", lambda: {"ok": False})
    assert seg.detectar_seguridad()[0]["evidencia"]["capa"] == "declarado"


def test_entro_a_SKILLS_por_la_ley():
    from api.services.av_agent_skills import SIN_IA, catalogo
    ids = {s.id: s for s in catalogo()}
    assert "detectar.permiso_flojo" in ids
    assert ids["detectar.permiso_flojo"].usa_ia == SIN_IA
