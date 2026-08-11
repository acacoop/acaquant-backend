"""El cache del histórico de agro no puede mezclar scopes.

Contexto (2026-08-11): `_agro_nuestro_mensual` es la query más cara de `ops_agro`
(314 ms de 1103, la única sin filtro de fecha) y su resultado no depende de nada
que el usuario toque en la vista — solo de `scope` y `nivel5`. Por eso se cacheó.

El riesgo que eso introduce es el que importa: **`scope` es el filtro de cuentas
por grupo**. Si la clave del cache no lo incluyera, un usuario podría recibir el
histórico calculado para OTRO scope — no un problema de performance, uno de datos
que no le corresponden. Este test fija que cada scope tiene su propia entrada.

El decorador `@cached` arma la clave con todos los argumentos, así que esto ya
funciona; el test está para que siga funcionando si alguien toca la firma (por
ejemplo, sacando `scope` de los parámetros y leyéndolo de otro lado).
"""
from __future__ import annotations

import pytest

from api.cache import invalidate
from api.services import operaciones_sql as ops


@pytest.fixture(autouse=True)
def _cache_limpio():
    invalidate("_agro_nuestro_mensual")
    yield
    invalidate("_agro_nuestro_mensual")


def test_cada_scope_tiene_su_propia_entrada_de_cache(monkeypatch):
    llamadas: list[dict] = []

    # Toneladas distintas por scope, para poder distinguir las dos respuestas.
    por_scope = {"cta-A": 111.0, "cta-B": 222.0}

    def _q_falso(sql: str, params: dict | None = None):
        p = params or {}
        llamadas.append(p)
        cta = (p.get("scope") or [""])[0]
        return [{"p": "2026-01", "c": "SOJA", "ton": por_scope.get(cta, 0.0)}]

    monkeypatch.setattr(ops, "_q", _q_falso)

    a = ops._agro_nuestro_mensual(scope=("cta-A",), nivel5=None)
    b = ops._agro_nuestro_mensual(scope=("cta-B",), nivel5=None)

    assert a["2026-01"]["SOJA"] != b["2026-01"]["SOJA"], \
        "dos scopes distintos NO pueden compartir la entrada del cache"
    assert len(llamadas) == 2, "cada scope tiene que haber ido a la base una vez"


def test_el_mismo_scope_pega_a_la_base_una_sola_vez(monkeypatch):
    """Lo que motivó el cambio: la vista dispara este mismo cálculo una y otra vez
    al cambiar filtros que no lo afectan."""
    n = {"veces": 0}

    def _q_falso(sql: str, params: dict | None = None):
        n["veces"] += 1
        return [{"p": "2026-01", "c": "SOJA", "ton": 10}]

    monkeypatch.setattr(ops, "_q", _q_falso)

    for _ in range(5):
        ops._agro_nuestro_mensual(scope=None, nivel5=None)
    assert n["veces"] == 1, "5 llamadas iguales tienen que costar UNA query"


def test_el_where_es_el_mismo_para_todas_las_queries_de_agro():
    """`_agro_base` existe para que el share y el resto de la vista no puedan
    filtrar distinto. Si alguien vuelve a escribir el filtro inline, esto avisa."""
    base_sin, p_sin = ops._agro_base(None, None)
    assert "commodity IN ('SOJA', 'TRIGO', 'MAIZ')" in base_sin
    assert "anulado_en IS NULL" in base_sin
    assert p_sin == {}

    base_scope, p_scope = ops._agro_base(("x", "y"), None)
    assert "id_cuenta = ANY(%(scope)s)" in base_scope
    assert p_scope["scope"] == ["x", "y"]

    base_n5, p_n5 = ops._agro_base(None, "COOPERATIVAS")
    assert "nivel_5 = %(nivel5)s" in base_n5
    assert p_n5["nivel5"] == "COOPERATIVAS"
