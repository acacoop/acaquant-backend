"""Tests de copiloto/navegacion.py — NAVEGACIÓN ASISTIDA (el guía te lleva).

Lo que congelan:
- LA JAULA: destino/filtro/valor fuera de la whitelist se RECHAZA (el modelo
  jamás manda algo crudo al frontend).
- El contrato con el frontend: las claves de estado son las MISMAS que las
  vistas persisten (`ops.mercado`, `operaciones.tab`…) — si alguien renombra
  una clave en el front sin tocar acá, el botón abriría la vista sin filtros.
- RBAC: no se ofrece ni se resuelve un destino que el usuario no puede ver.
"""
from __future__ import annotations

import pytest

from api.services.copiloto import navegacion as nav

CATALOGOS_FAKE = {
    "mercados": ["BYMA", "A3", "MAV", "MAE", "FCI Bilateral"],
    "segmentos": ["PRODUCTORES", "COOPERATIVAS", "INSTITUCIONALES", "AGRO"],
    "niveles3": ["FCI", "BONOS"],
    "tipos_operacion": ["Compras", "Ventas", "Suscripción", "Rescate"],
}


@pytest.fixture(autouse=True)
def catalogos(monkeypatch):
    monkeypatch.setattr(nav, "_valores", lambda c: CATALOGOS_FAKE.get(c, []))
    # acceso permitido salvo que el test lo cambie
    monkeypatch.setattr("api.services.copiloto.derivacion._acceso", lambda u, cfg: True)


# ── la jaula ─────────────────────────────────────────────────────────────────

def test_destino_desconocido_se_rechaza():
    r = nav.resolver("../etc/passwd", {}, "u@x.com")
    assert r["ok"] is False and "desconocido" in r["error"]


def test_filtro_desconocido_se_rechaza():
    r = nav.resolver("operaciones_volumen", {"inventado": "x"}, "u@x.com")
    assert r["ok"] is False and "inventado" in r["error"]


def test_valor_fuera_del_catalogo_se_rechaza():
    r = nav.resolver("operaciones_volumen", {"mercado": "NASDAQ"}, "u@x.com")
    assert r["ok"] is False and "NASDAQ" in r["error"]
    assert "BYMA" in r["error"]  # le enseña los válidos


def test_fecha_mal_formada_se_rechaza():
    r = nav.resolver("operaciones_volumen", {"desde": "ayer"}, "u@x.com")
    assert r["ok"] is False and "YYYY-MM-DD" in r["error"]


# ── resolución de CUENTA (el paso que faltaba, cazado por el user) ──────────

CUENTAS_FAKE = [("805", "MOLLO, NICOLAS EZEQUIEL"), ("900", "PEREZ, JUAN")]


@pytest.fixture
def cuentas(monkeypatch):
    monkeypatch.setattr(nav, "_cuentas", lambda: CUENTAS_FAKE)


def test_cuenta_se_resuelve_a_la_denominacion_exacta(cuentas):
    """El user dijo 'nicolas mollo' pero la cuenta es 'MOLLO, NICOLAS
    EZEQUIEL': el código resuelve, y setea el filtro REAL (ops.denominacion),
    no solo el texto del buscador."""
    r = nav.resolver("operaciones_volumen", {"cuenta": "nicolas mollo"}, "u@x.com")
    assert r["ok"]
    assert r["estado"]["ops.denominacion"] == "MOLLO, NICOLAS EZEQUIEL"
    assert r["estado"]["ops.search"] == "MOLLO, NICOLAS EZEQUIEL"


def test_cuenta_por_numero(cuentas):
    r = nav.resolver("operaciones_volumen", {"cuenta": "805"}, "u@x.com")
    assert r["ok"] and r["estado"]["ops.denominacion"] == "MOLLO, NICOLAS EZEQUIEL"


def test_cuenta_ambigua_o_inexistente_se_rechaza(cuentas):
    r = nav.resolver("operaciones_volumen", {"cuenta": "zzz"}, "u@x.com")
    assert r["ok"] is False and "no encontré" in r["error"]


# ── CLIENTE vs OPERADOR: no se adivina, se consulta ─────────────────────────

OPERADORES_FAKE = [("jc@aca.com", "Javier Curzel"), ("mm@aca.com", "MOLLO, NICOLAS EZEQUIEL")]


@pytest.fixture
def personas(monkeypatch):
    monkeypatch.setattr(nav, "_cuentas", lambda: CUENTAS_FAKE)
    monkeypatch.setattr(nav, "_operadores", lambda: [("jc@aca.com", "Javier Curzel")])


def test_persona_que_es_operador_no_se_trata_como_cuenta(personas):
    """El caso que enojó al user: asumía 'operador' sin consultar. Ahora, si
    NO es cuenta pero SÍ operador, la tool se lo dice y le pide reintentar."""
    r = nav.resolver("operaciones_volumen", {"cuenta": "javier curzel"}, "u@x.com")
    assert r["ok"] is False
    assert "OPERADOR" in r["error"] and "`operador`" in r["error"]


def test_filtro_operador_resuelve_a_email(personas):
    r = nav.resolver("operaciones_volumen", {"operador": "javier curzel"}, "u@x.com")
    assert r["ok"] and r["estado"]["ops.operador"] == "jc@aca.com"
    assert "Javier Curzel" in r["resumen"]          # el botón muestra el nombre
    assert "Curzel" not in r["resumen_llm"]         # al modelo NO


def test_persona_ambigua_obliga_a_preguntar(monkeypatch):
    """Mismo nombre como cuenta Y como operador → el sistema NO elige: manda
    al modelo a preguntarle al usuario."""
    monkeypatch.setattr(nav, "_cuentas", lambda: CUENTAS_FAKE)
    monkeypatch.setattr(nav, "_operadores", lambda: OPERADORES_FAKE)
    r = nav.resolver("operaciones_volumen", {"cuenta": "mollo"}, "u@x.com")
    assert r["ok"] is False and r.get("ambiguo") is True
    assert "preguntale al usuario" in r["error"].lower()


def test_clasificar_persona_los_tres_casos(monkeypatch):
    monkeypatch.setattr(nav, "_cuentas", lambda: CUENTAS_FAKE)
    monkeypatch.setattr(nav, "_operadores", lambda: [("jc@aca.com", "Javier Curzel")])
    assert nav.clasificar_persona("nicolas mollo")["cuenta"] == "MOLLO, NICOLAS EZEQUIEL"
    assert nav.clasificar_persona("nicolas mollo")["operador"] is None
    assert nav.clasificar_persona("javier curzel")["operador"] == "jc@aca.com"
    assert nav.clasificar_persona("javier curzel")["cuenta"] is None
    assert nav.clasificar_persona("nadie xyz") == {"cuenta": None, "operador": None}


def test_clasificar_acepta_ficha_de_la_aduana(personas):
    quien = nav.clasificar_persona("CLIENTE_1", {"fichas": {"CLIENTE_1": "nicolas mollo"}})
    assert quien["cuenta"] == "MOLLO, NICOLAS EZEQUIEL"


def test_la_denominacion_real_NO_vuelve_al_modelo(cuentas):
    """PRIVACIDAD: el guía habla con el proveedor barato — la denominación
    canónica del cliente va al FRONTEND, nunca al modelo."""
    buzon: dict = {}
    salida = nav.ejecutor(buzon, "u@x.com")(
        "abrir_vista", {"destino": "operaciones_volumen",
                        "filtros": {"cuenta": "nicolas mollo"}})
    assert "EZEQUIEL" not in salida and "MOLLO" not in salida
    assert buzon["navegacion"]["estado"]["ops.denominacion"] == "MOLLO, NICOLAS EZEQUIEL"
    assert "MOLLO" in buzon["navegacion"]["resumen"]  # el botón sí lo muestra


# ── el contrato con el frontend (claves de sessionStorage) ───────────────────

def test_operaciones_volumen_arma_el_estado_real():
    r = nav.resolver("operaciones_volumen",
                     {"mercado": "byma", "desde": "2026-06-01", "hasta": "2026-06-30"},
                     "u@x.com")
    assert r["ok"] and r["ruta"] == "/operaciones"
    e = r["estado"]
    assert e["operaciones.tab"] == "operaciones"   # la pestaña correcta
    assert e["ops.mercado"] == "BYMA"              # canonizado del catálogo
    assert e["ops.desde"] == "2026-06-01" and e["ops.hasta"] == "2026-06-30"
    assert e["ops.modo"] == "RANGO"                # sin esto las fechas se ignoran


def test_aranceles_usa_sus_propias_claves():
    r = nav.resolver("operaciones_aranceles",
                     {"segmento": "PRODUCTORES", "desde": "2026-01-01",
                      "hasta": "2026-06-30", "abrir_por": "operador"}, "u@x.com")
    assert r["ok"]
    e = r["estado"]
    assert e["operaciones.tab"] == "aranceles"
    assert e["ar.segmento"] == "PRODUCTORES" and e["ar.modo"] == "RANGO"
    assert e["ar.dim"] == "operador"
    assert not any(k.startswith("ops.") for k in e)  # no mezcla claves de tabs


def test_garantia_es_un_destino_resoluble():
    """El caso que motivó las EQUIVALENCIAS del guía: 'títulos en garantía'."""
    r = nav.resolver("back_office_tenencia", {"garantia": "solo_gar"}, "u@x.com")
    assert r["ok"] and r["ruta"] == "/back-office"
    assert r["estado"]["backoffice.tab"] == "tenencia"
    assert r["estado"]["tenencia.garMode.v2"] == "solo_gar"


def test_match_tolerante_de_valores():
    assert nav._match_catalogo("byma", CATALOGOS_FAKE["mercados"]) == "BYMA"
    assert nav._match_catalogo("fci bilateral", CATALOGOS_FAKE["mercados"]) == "FCI Bilateral"
    assert nav._match_catalogo("nada", CATALOGOS_FAKE["mercados"]) is None


def test_match_tolera_typos():
    """Caso real: el usuario escribió 'byam'. Un filtro no tiene por qué
    fallar por una letra cambiada."""
    assert nav._match_catalogo("byam", CATALOGOS_FAKE["mercados"]) == "BYMA"
    assert nav._match_catalogo("cooperativa", CATALOGOS_FAKE["segmentos"]) == "COOPERATIVAS"
    # pero sigue sin inventar: algo lejano no matchea con nada
    assert nav._match_catalogo("nasdaq", CATALOGOS_FAKE["mercados"]) is None


def test_sin_filtros_igual_navega():
    r = nav.resolver("operaciones_depositos", {}, "u@x.com")
    assert r["ok"] and r["estado"] == {"operaciones.tab": "depositos"}
    assert r["resumen"] == ""


# ── RBAC ─────────────────────────────────────────────────────────────────────

def test_destino_sin_permiso_no_se_ofrece_ni_resuelve(monkeypatch):
    monkeypatch.setattr("api.services.copiloto.derivacion._acceso",
                        lambda u, cfg: cfg["modulo"] != "back-office")
    ids = [d["id"] for d in nav.destinos_para("u@x.com")]
    assert "operaciones_volumen" in ids and "back_office_tenencia" not in ids
    r = nav.resolver("back_office_tenencia", {}, "u@x.com")
    assert r["ok"] is False and "permiso" in r["error"]


def test_descripcion_lista_valores_vigentes():
    txt = nav.descripcion_destinos("u@x.com")
    assert "operaciones_volumen" in txt and "BYMA" in txt
    assert "YYYY-MM-DD" in txt


# ── el ejecutor (lo que ve el motor) ─────────────────────────────────────────

def test_ejecutor_deja_la_navegacion_en_el_buzon():
    buzon: dict = {}
    ejecutar = nav.ejecutor(buzon, "u@x.com")
    salida = ejecutar("abrir_vista", {"destino": "operaciones_volumen",
                                      "filtros": {"mercado": "MAV"}})
    assert "MAV" in salida
    assert buzon["navegacion"]["ruta"] == "/operaciones"
    assert buzon["navegacion"]["estado"]["ops.mercado"] == "MAV"


def test_ejecutor_error_no_deja_navegacion():
    buzon: dict = {}
    ejecutar = nav.ejecutor(buzon, "u@x.com")
    salida = ejecutar("abrir_vista", {"destino": "operaciones_volumen",
                                      "filtros": {"mercado": "INVENTADO"}})
    assert "navegacion" not in buzon        # nada llega al frontend
    assert "INVENTADO" in salida            # y el modelo se entera del porqué


def test_ejecutor_tool_desconocida():
    buzon: dict = {}
    assert "desconocida" in nav.ejecutor(buzon, "u@x.com")("rm_rf", {})


# ── LA ADUANA en el guía (decisión user 2026-07-21) ──────────────────────────

def test_ficha_se_resuelve_a_la_cuenta_real(cuentas):
    """TOKEN-IN: el modelo nunca vio el nombre (llegó como CLIENTE_1); el
    código recupera lo que escribió el usuario y resuelve la cuenta."""
    mapping = {"fichas": {"CLIENTE_1": "Nicolas Mollo"}}
    r = nav.resolver("operaciones_volumen", {"cuenta": "CLIENTE_1"}, "u@x.com",
                     mapping=mapping)
    assert r["ok"]
    assert r["estado"]["ops.denominacion"] == "MOLLO, NICOLAS EZEQUIEL"


def test_ficha_sin_mapping_no_resuelve(cuentas):
    r = nav.resolver("operaciones_volumen", {"cuenta": "CLIENTE_9"}, "u@x.com",
                     mapping={"fichas": {}})
    assert r["ok"] is False


def test_guia_no_manda_identidades_al_proveedor(monkeypatch, cuentas):
    """EL CIRCUITO COMPLETO, con el caso real del user: pregunta con nombre →
    a la IA sale tokenizado → la tool resuelve adentro → el botón trae la
    cuenta exacta → el usuario ve el nombre real."""
    from api.services import copiloto
    from core import pii_gateway as pg

    monkeypatch.setattr(pg, "_catalogo", lambda: {
        "ids": {"805"}, "nombres": {"mollo, nicolas ezequiel": "805"},
        "tokens": {"mollo": "805", "ezequiel": "805"}, "documentos": set(),
        "operadores": {}, "operadores_tokens": {}})
    monkeypatch.setattr(pg, "cargar_mapping", lambda cid, em: pg._mapping_nuevo())
    guardado: dict = {}
    monkeypatch.setattr(pg, "guardar_mapping",
                        lambda cid, em, m: guardado.update(m))

    visto = {}

    def fake_tools(tarea, system=None, user=None, tools=None, ejecutar=None,
                   usuario=None, detalle=None, historial=None):
        visto["user"] = user
        visto["detalle"] = detalle
        # el modelo devuelve la ficha tal cual la vio
        ejecutar("abrir_vista", {"destino": "operaciones_volumen",
                                 "filtros": {"cuenta": "CLIENTE_1"}})
        return "Te llevo a las operaciones de CLIENTE_1.", 7, ""

    monkeypatch.setattr("core.ai.completar_con_tools", fake_tools)
    monkeypatch.setattr("core.ai.motivo_presupuesto", lambda u: None)
    monkeypatch.setattr("core.roles.has_access", lambda u, m: True)
    monkeypatch.setattr("core.roles.get_user_role", lambda u: "admin")

    out = copiloto.preguntar("ayuda", "cuánto operó Nicolas Mollo en julio",
                             usuario="jefe@x.com", conv_id="c1")
    assert out["ok"] is True
    # 1. NADA del nombre salió al proveedor
    for pieza in (visto["user"], visto["detalle"]):
        assert "Mollo" not in pieza and "mollo" not in pieza.lower()
    assert "CLIENTE_1" in visto["user"]
    # 2. la tool resolvió la cuenta EXACTA adentro del perímetro
    assert out["navegacion"]["estado"]["ops.denominacion"] == "MOLLO, NICOLAS EZEQUIEL"
    # 3. el usuario ve el nombre real (detokenizado)
    assert "Nicolas Mollo" in out["respuesta"] and "CLIENTE_1" not in out["respuesta"]
    # 4. las fichas quedan persistidas para los próximos turnos
    assert guardado["fichas"]["CLIENTE_1"] == "Nicolas Mollo"


def test_claves_de_estado_existen_en_el_frontend():
    """CONTRATO CRUZADO: cada clave que declara el catálogo tiene que existir
    como `usePersistedState("<clave>"…)` en acaquant-web. Si alguien renombra
    una clave allá, el botón abriría la vista SIN filtros y en silencio — este
    test lo caza. Skipea si el repo hermano no está al lado (CI del backend)."""
    import os
    import re

    web = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "..", "acaquant-web", "src", "components")
    if not os.path.isdir(web):
        pytest.skip("acaquant-web no está en el checkout")

    fuente = []
    for dirpath, _d, files in os.walk(web):
        for f in files:
            if f.endswith((".tsx", ".ts")):
                with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                    fuente.append(fh.read())
    todo = "\n".join(fuente)
    persistidas = set(re.findall(r'usePersistedState<[^>]*>\(\s*"([^"]+)"', todo))
    persistidas |= set(re.findall(r'usePersistedState\(\s*"([^"]+)"', todo))

    declaradas: set[str] = set()
    for d in nav._DESTINOS.values():
        declaradas |= set(d["estado_base"])
        for f in (d["filtros"] or {}).values():
            declaradas.add(f["clave"])
            declaradas |= set(f["con"])

    faltan = sorted(declaradas - persistidas)
    assert not faltan, (
        f"claves declaradas en navegacion.py que NINGUNA vista persiste: {faltan}. "
        "O se renombraron en el frontend, o la vista todavía usa useState.")


def test_motor_devuelve_la_navegacion(monkeypatch):
    """El buzón llega hasta el payload del panel."""
    from api.services import copiloto

    def fake_tools(tarea, system=None, user=None, tools=None, ejecutar=None,
                   usuario=None, detalle=None, historial=None):
        ejecutar("abrir_vista", {"destino": "operaciones_volumen",
                                 "filtros": {"mercado": "BYMA"}})
        return "Te llevo: ahí vas a ver el volumen de BYMA.", 5, ""

    monkeypatch.setattr("core.ai.completar_con_tools", fake_tools)
    monkeypatch.setattr("core.ai.motivo_presupuesto", lambda u: None)
    monkeypatch.setattr("core.roles.has_access", lambda u, m: True)
    monkeypatch.setattr("core.roles.get_user_role", lambda u: "admin")
    out = copiloto.preguntar("ayuda", "cuánto se operó en BYMA", usuario="u@x.com")
    assert out["ok"] is True
    assert out["navegacion"]["ruta"] == "/operaciones"
    assert out["navegacion"]["estado"]["ops.mercado"] == "BYMA"
