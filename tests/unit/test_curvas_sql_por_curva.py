"""La pertenencia a una curva la deciden los EJES, no la columna `curva`.

Estos tests congelan el cambio de 2026-08-16 en `core/curvas_sql.py`. Lo que
protegen no es el código: es que no vuelva a existir una SEGUNDA definición de
"a qué curva pertenece este bono". Mientras la columna fue la fuente, un dual
sólo podía tener una palabra y se escondía de una de sus dos tablas.

No tocan la base: se inyecta el master en el cache del módulo.
"""
import pytest

from core import curvas_sql as cs

# La columna `curva` de cada doc dice a propósito algo DISTINTO de sus ejes: es
# la única forma de probar que ya nadie la lee.
_MASTER = [
    # dual CER + TAMAR — la columna sólo pudo guardar una de las dos patas
    {"ticker_corto": "TTD26", "curva": "cer",
     "emisor_tipo": "soberano", "moneda_eje": "ARS", "ajuste": "fija",
     "ajuste_alt": "tamar"},
    # corporativo en USD a tasa fija: su curva ES `soberanos` (hard dólar), pero
    # la columna decía `on_energia` porque ahí se metía el sector del emisor
    {"ticker_corto": "YMCXO", "curva": "on_energia",
     "emisor_tipo": "corporativo", "moneda_eje": "USD", "ajuste": "fija"},
    {"ticker_corto": "AL30", "curva": "soberanos",
     "emisor_tipo": "soberano", "moneda_eje": "USD", "ajuste": "fija",
     "ley": "local"},
    {"ticker_corto": "TX26", "curva": "cer",
     "emisor_tipo": "soberano", "moneda_eje": "ARS", "ajuste": "cer"},
    # sin ejes: la columna dice `tasa_fija` pero nadie lo clasificó
    {"ticker_corto": "SA24", "curva": "tasa_fija"},
    # badlar todavía no tiene curva propia
    {"ticker_corto": "RMJ28", "curva": "on_finanzas",
     "emisor_tipo": "provincial", "moneda_eje": "ARS", "ajuste": "badlar"},
]


@pytest.fixture(autouse=True)
def _master(monkeypatch):
    monkeypatch.setattr(cs, "_master", lambda: _MASTER)
    monkeypatch.setattr(cs, "cargar_todos", lambda: [dict(d) for d in _MASTER])


def _tk(docs):
    return sorted(d["ticker_corto"] for d in docs)


def test_dual_esta_en_sus_dos_curvas():
    """Todo el cambio de modelo en una assert: un dual no vive en una tabla."""
    assert "TTD26" in _tk(cs.por_curva("tasa_fija"))
    assert "TTD26" in _tk(cs.por_curva("tamar"))


def test_corporativo_usd_cae_en_soberanos_y_no_en_su_sector():
    """`on_energia` no es una curva — es el emisor. El rendimiento de una ON en
    USD a tasa fija se compara contra el hard dólar."""
    assert _tk(cs.por_curva("soberanos")) == ["AL30", "YMCXO"]


def test_la_columna_curva_ya_no_decide():
    """SA24 dice `tasa_fija` en la columna y no tiene ejes → no entra a ninguna."""
    assert "SA24" not in _tk(cs.por_curva("tasa_fija"))
    assert _tk(cs.sin_curva()) == ["RMJ28", "SA24"]


def test_corporativos_es_el_eje_del_emisor():
    assert _tk(cs.corporativos()) == ["YMCXO"]
    assert "YMCXO" not in _tk(cs.no_corporativos())


def test_no_corporativos_incluye_a_los_sin_clasificar():
    """Sin `emisor_tipo` un bono se MUESTRA, no se esconde — igual que el viejo
    `not_like('on%')` incluía los de curva NULL."""
    assert "SA24" in _tk(cs.no_corporativos())


def test_corporativos_y_no_corporativos_particionan_el_master():
    assert len(cs.corporativos()) + len(cs.no_corporativos()) == len(_MASTER)


def test_esta_en_curva_es_el_mismo_predicado_que_por_curva():
    """El invariante que evita que el editor y el listado se contradigan."""
    for curva in ("tasa_fija", "cer", "soberanos", "tamar", "dolar_linked"):
        esperado = _tk(cs.por_curva(curva))
        assert sorted(d["ticker_corto"] for d in _MASTER
                      if cs.esta_en_curva(d, curva)) == esperado


def test_agrupado_por_curva_agrupa_igual_que_por_curva():
    grupos = cs.agrupado_por_curva()
    for curva in ("tasa_fija", "cer", "soberanos", "tamar"):
        assert _tk(grupos.get(curva) or []) == _tk(cs.por_curva(curva))
    # y el dual está en los dos grupos, que es lo que mete su TEA en las dos matrices
    assert "TTD26" in _tk(grupos["tasa_fija"]) and "TTD26" in _tk(grupos["tamar"])
