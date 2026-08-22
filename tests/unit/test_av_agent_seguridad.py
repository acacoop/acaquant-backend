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
    """**El agujero que originó todo esto.** `app.routes` puede devolver
    envoltorios (`_IncludedRouter`) en vez de rutas: un `for` ingenuo veía 37
    de 541 y pasaba en verde.

    ⚠️ Si los envoltorios existen o no lo decide la VERSIÓN de FastAPI (con la
    pineada, 0.136.x, `app.routes` viene plano y trae las 560+ directas). La
    primera versión de este test congelaba ese detalle (`rutas() > app.routes
    × 5`) y fallaba justamente cuando `app.routes` no esconde nada. Lo que se
    congela es la intención: superficie ve el 100% en CUALQUIERA de los dos
    mundos."""
    from fastapi.routing import APIRoute

    from api.main import app

    rutas = superficie.rutas()
    assert len(rutas) > 400

    envoltorios = [r for r in app.routes if type(r).__name__ == "_IncludedRouter"]
    if envoltorios:
        # FastAPI lazy: app.routes esconde las rutas adentro de envoltorios →
        # superficie tiene que ver MUCHO más que el primer nivel.
        assert len(rutas) > len(list(app.routes)) * 5
    else:
        # FastAPI plano: app.routes ya trae todo → superficie no puede ver
        # ni UNA menos que las APIRoute reales.
        planas = [r for r in app.routes if isinstance(r, APIRoute)]
        assert len(rutas) >= len(planas)


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


# ── Las dos listas: público de verdad vs. candado en el borde ──────────────

def test_las_dos_listas_no_se_pisan():
    """Son categorías distintas y excluyentes: o no hay candado porque no hace
    falta, o el candado existe y vive afuera del repo. Un path en las dos sería
    una contradicción que además decide si se prueba o no."""
    assert not (set(seg.ABIERTOS_OK) & set(seg.PROTEGIDOS_EN_EL_BORDE))


def test_cada_declaracion_lleva_su_MOTIVO():
    """Declarar sin decir por qué es correr el chequeo, no resolverlo: el que
    audite dentro de seis meses tiene que poder juzgar si sigue valiendo."""
    for lista in (seg.ABIERTOS_OK, seg.PROTEGIDOS_EN_EL_BORDE):
        assert all(len(motivo) > 20 for motivo in lista.values())


def test_lo_declarado_que_EXISTE_sigue_sin_gate():
    """Anti-rot. Si alguien le puso gate en el código a algo declarado, la
    declaración quedó vieja y miente. No se exige que el path exista: `/oauth/*`
    y `/mcp` se montan solo con las env vars del MCP, así que en un checkout sin
    configurar no están — y eso no es un error."""
    reales = {r.path: r for r in superficie.rutas()}
    for path in {**seg.ABIERTOS_OK, **seg.PROTEGIDOS_EN_EL_BORDE}:
        r = reales.get(path)
        assert r is None or r.sin_gate, f"{path} ya tiene gate: sacalo de la lista"


def test_el_borde_SIEMPRE_se_prueba_aunque_no_tenga_gate(monkeypatch):
    """**El caso Vercel.** Son las únicas rutas cuyo veredicto depende 100% de la
    prueba: el código no las gatea a propósito. El filtro general las descartaría
    (`not r.sin_gate`), así que se agregan aparte."""
    pedidos: list[str] = []

    class _R:
        path, metodos, gates = "/oauth/authorize", frozenset({"GET"}), ()
        sin_gate = True

    monkeypatch.setattr(seg, "URL_PUBLICA", "https://ejemplo")
    monkeypatch.setattr(superficie, "rutas", lambda: [_R()])

    class _Resp:
        status_code, content, headers = 401, b"", {}

    class _S:
        headers: ClassVar[dict] = {}

        def get(self, url, **k):
            pedidos.append(url)
            return _Resp()

    import requests
    monkeypatch.setattr(requests, "Session", _S)
    monkeypatch.setattr(seg, "PAUSA_S", 0)
    r = seg.probar()
    assert pedidos == ["https://ejemplo/oauth/authorize"]
    assert r["rechazan"] == 1


def test_un_302_al_login_es_RECHAZO_no_filtracion():
    """Así contesta Cloudflare Access cuando no hay sesión. Contarlo como fuga
    llenaría el aviso de falsos positivos, que es como se aprende a ignorarlo."""
    class _Resp:
        status_code = 302
        content = b""
        headers: ClassVar[dict] = {
            "location": "https://aca.cloudflareaccess.com/cdn-cgi/access/login/x"}

    assert seg._veredicto(_Resp()) == "rechaza"


def test_un_200_en_el_borde_es_el_CANDADO_QUE_NO_ESTA(monkeypatch):
    """El código no lo gatea a propósito; si el borde tampoco, está abierto."""
    monkeypatch.setattr(seg, "declarado", lambda: {
        "abiertas_inesperadas": [], "total": 1, "sin_gate": [],
        "escrituras_sin_gate": [], "abiertas_declaradas": [], "en_el_borde": []})
    monkeypatch.setattr(seg, "probar", lambda: {"ok": True, "filtran": [],
        "borde_abierto": [{"path": "/oauth/authorize", "status": 200,
                           "bytes": 3100, "gates": []}]})
    h = seg.detectar_seguridad()
    assert len(h) == 1 and h[0]["regla"] == "borde_sin_candado"
    assert h[0]["severidad"] == "alta"


def test_si_la_prueba_NO_CORRE_lo_dice(monkeypatch):
    """**El silencio no es un verde.** Sin este aviso, un job que solo probó lo
    declarado se lee igual que uno que probó todo y no encontró nada — y la
    diferencia es justo la capa donde estuvo el incidente."""
    monkeypatch.setattr(seg, "declarado", lambda: {
        "abiertas_inesperadas": [], "total": 541, "sin_gate": [],
        "escrituras_sin_gate": [], "abiertas_declaradas": [], "en_el_borde": []})
    monkeypatch.setattr(seg, "probar", lambda: {"ok": False, "motivo": "falta X"})
    h = seg.detectar_seguridad()
    assert [x["regla"] for x in h] == ["prueba_no_corrio"]
    assert h[0]["evidencia"]["corrio"] is False


# ── La MEMORIA: que esto no sea una foto ──────────────────────────────────

def _sin_delta(monkeypatch, **kw):
    base = {"abiertas_inesperadas": [], "total": 1, "sin_gate": [],
            "escrituras_sin_gate": [], "abiertas_declaradas": [], "en_el_borde": []}
    monkeypatch.setattr(seg, "declarado", lambda: {**base, **kw})
    monkeypatch.setattr(seg, "probar", lambda: {"ok": True, "filtran": [],
                                                "borde_abierto": []})


def test_un_endpoint_que_PERDIO_su_gate_es_el_hallazgo(monkeypatch):
    """**Lo que ninguna foto ve.** Se cierra uno, se abre otro y el total de
    abiertos no se mueve: sin comparar contra ayer, la regresión es invisible."""
    _sin_delta(monkeypatch)
    monkeypatch.setattr(seg, "comparar", lambda: {
        "ok": True, "primera": False, "fecha_previa": "2026-08-18", "total": 541,
        "nuevos": [], "desaparecidos": [],
        "perdieron_gate": [{"path": "/api/portfolio/aum", "metodos": "GET",
                            "gates_ayer": "verify_api_key", "escribe": False}]})
    h = seg.detectar_seguridad()
    assert [x["regla"] for x in h] == ["perdio_el_gate"]
    assert h[0]["severidad"] == "alta"
    assert h[0]["evidencia"]["gates_ayer"] == "verify_api_key"


def test_distingue_un_agujero_NUEVO_de_uno_que_ya_estaba(monkeypatch):
    """No es lo mismo «esto se publicó así hoy» que «esto viene así hace meses»:
    lo primero tiene un culpable identificable y se arregla en el momento."""
    _sin_delta(monkeypatch, abiertas_inesperadas=["/api/nuevo", "/api/viejo"])
    monkeypatch.setattr(seg, "comparar", lambda: {
        "ok": True, "primera": False, "fecha_previa": "2026-08-18", "total": 2,
        "nuevos": [{"path": "/api/nuevo"}], "desaparecidos": [],
        "perdieron_gate": []})
    por_path = {x["ticker"]: x for x in seg.detectar_seguridad()}
    assert por_path["/api/nuevo"]["evidencia"]["desde"] == "hoy"
    assert por_path["/api/viejo"]["evidencia"]["desde"] == "ya estaba"


def test_sin_foto_previa_NO_INVENTA_desde_cuando(monkeypatch):
    """«No sé» es una respuesta válida; afirmar «ya estaba» sin haber mirado
    ayer sería exactamente el tipo de dato inventado que rompe la confianza."""
    _sin_delta(monkeypatch, abiertas_inesperadas=["/api/x"])
    monkeypatch.setattr(seg, "comparar", lambda: {"ok": True, "primera": True})
    assert seg.detectar_seguridad()[0]["evidencia"]["desde"] == "sin foto previa"


def test_la_PRIMERA_foto_no_reporta_541_endpoints_nuevos(monkeypatch):
    """El día uno todo es «nuevo». Avisar de 541 endpoints es la forma más
    rápida de que el aviso se apague para siempre — el mismo criterio que la
    primera foto del tamaño de la base."""
    _sin_delta(monkeypatch)
    monkeypatch.setattr(seg, "comparar", lambda: {"ok": True, "primera": True})
    assert seg.detectar_seguridad() == []


def test_la_foto_de_la_superficie_NO_crece_sin_techo():
    """Una tabla que vigila a la app y crece todos los días es un chiste que se
    cuenta solo. Y la purga va en la MISMA transacción que el INSERT."""
    import inspect
    assert seg.FECHAS_QUE_SE_GUARDAN == 2
    src = inspect.getsource(seg.sacar_foto)
    assert "DELETE FROM manager.superficie_dia" in src
    assert src.index("INSERT INTO") < src.index("DELETE FROM") < src.index("commit()")


def test_una_foto_rota_no_tumba_el_chequeo(monkeypatch):
    """La memoria es una mejora, no un requisito: si la tabla no está todavía,
    el detector tiene que seguir contestando lo que sí puede saber."""
    _sin_delta(monkeypatch, abiertas_inesperadas=["/api/x"])
    def _boom():
        raise RuntimeError("no existe la tabla")
    monkeypatch.setattr(seg, "comparar", _boom)
    h = seg.detectar_seguridad()
    assert len(h) == 1 and h[0]["regla"] == "sin_gate"


def test_la_URL_se_lee_AL_USARLA_no_al_importar(monkeypatch):
    """Este módulo se importa muy temprano (lo arrastra `api.superficie`), y
    `load_dotenv()` puede correr después. Con la constante leída al import,
    alguien carga la variable en el `.env`, reinicia, y el chequeo sigue diciendo
    «falta AV_AGENT_URL_PUBLICA» sin ninguna pista de por qué. **Un orden de
    imports no puede ser la razón por la que un chequeo de seguridad no corre.**"""
    monkeypatch.setattr(seg, "URL_PUBLICA", "")
    monkeypatch.setenv("AV_AGENT_URL_PUBLICA", "https://api.ejemplo.com/")
    assert seg._url() == "https://api.ejemplo.com"


def test_sin_la_variable_en_NINGUN_lado_no_prueba(monkeypatch):
    monkeypatch.setattr(seg, "URL_PUBLICA", "")
    monkeypatch.delenv("AV_AGENT_URL_PUBLICA", raising=False)
    assert seg._url() == ""
