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


def test_texto_libre_se_capa():
    r = nav.resolver("operaciones_volumen", {"cuenta": "x" * 300}, "u@x.com")
    assert r["ok"] and len(r["estado"]["ops.search"]) <= 80


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
