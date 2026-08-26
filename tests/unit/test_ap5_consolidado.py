"""tests/unit/test_ap5_consolidado.py — el cuadro POR INSTRUMENTO del reporte.

`agrupar_consolidado()` es puro (recibe las filas de `por_instrumento`, no toca
la base), así que se puede congelar. Cada test es una forma de equivocarse que
NO grita: el cuadro sale prolijo, totaliza, y da otro número.
"""
from __future__ import annotations

from api.services.ap5_posiciones import agrupar_consolidado


def _f(**kw):
    base = {
        "familia": "agro", "producto": "MAI", "etiqueta": "MAIZ",
        "unidad": "Tn", "moneda": "Dólar MtR",
        "compra": 558700.0, "venta": 362800.0, "neta": 195900.0,
        "compra_contratos": 5587.0, "venta_contratos": 3628.0,
        "sin_multiplicador": 0,
        "acum_hoy": 1981540.0, "acum_ayer": 1562800.0, "diaria": 418740.0,
        "acumulado_desde": None, "fecha_anterior": None,
    }
    return {**base, **kw}


def test_los_numeros_del_reporte_de_la_mesa():
    """La fila de MAIZ tal cual la manda la mesa por mail."""
    b = agrupar_consolidado([_f()])[0]
    fila = b["filas"][0]
    assert (fila["compra"], fila["venta"], fila["neta"]) == (558700.0, 362800.0, 195900.0)
    assert (fila["acum_hoy"], fila["acum_ayer"], fila["diaria"]) == (
        1981540.0, 1562800.0, 418740.0)


def test_el_TOTAL_lo_calcula_el_backend():
    """No el navegador: un contador sumado en el browser no se puede verificar
    del lado del servidor, y este cuadro se imprime."""
    b = agrupar_consolidado([
        _f(producto="MAI", etiqueta="MAIZ", compra=558700.0, venta=362800.0, neta=195900.0),
        _f(producto="SOJ", etiqueta="SOJA", compra=100000.0, venta=20000.0, neta=80000.0),
        _f(producto="TRI", etiqueta="TRIGO", compra=30000.0, venta=10000.0, neta=20000.0),
    ])[0]
    assert b["total"]["compra"] == 688700.0
    assert b["total"]["venta"] == 392800.0
    assert b["total"]["neta"] == 295900.0


def test_agro_y_dolar_son_DOS_cuadros():
    """Toneladas y dólares no van en la misma tabla, y sus diferencias liquidan
    en monedas distintas: un cuadro solo obligaría a un total sin significado."""
    r = agrupar_consolidado([
        _f(familia="agro", moneda="Dólar MtR", unidad="Tn"),
        _f(familia="dolar", producto="DLR", etiqueta="DÓLAR", moneda="Pesos", unidad="USD"),
    ])
    assert [(b["tab"], b["moneda"]) for b in r] == [
        ("agro", "Dólar MtR"), ("dolar", "Pesos")]


def test_el_agro_va_PRIMERO():
    """El orden del mail: FUTUROS AGRÍCOLAS arriba, FUTUROS U$S abajo."""
    r = agrupar_consolidado([
        _f(familia="dolar", producto="DLR", moneda="Pesos"),
        _f(familia="agro", moneda="Dólar MtR"),
    ])
    assert [b["tab"] for b in r] == ["agro", "dolar"]


def test_dos_monedas_en_la_misma_tab_NO_se_suman():
    """No se asume que una tab tenga una sola moneda. Si aparecieran dos, salen
    dos cuadros — un total mezclado da un número, no falla, y está mal."""
    r = agrupar_consolidado([
        _f(moneda="Dólar MtR", compra=100.0),
        _f(producto="SOJ", moneda="Pesos", compra=999.0),
    ])
    assert len(r) == 2
    assert {b["total"]["compra"] for b in r} == {100.0, 999.0}


def test_la_familia_OTROS_no_entra_al_cuadro():
    """AGRO es trigo, soja y maíz. El WTI se mide en barriles y sumarlo daría
    toneladas que no lo son."""
    r = agrupar_consolidado([_f(familia="otros", producto="WTI", unidad="Bl")])
    assert r == []


def test_un_simbolo_sin_multiplicador_se_arrastra_al_TOTAL():
    """Si falta un multiplicador, la posición del total está incompleta y el
    cuadro tiene que poder decirlo en vez de mostrar un número redondo."""
    b = agrupar_consolidado([_f(sin_multiplicador=3), _f(producto="SOJ")])[0]
    assert b["total"]["sin_multiplicador"] == 3


def test_dos_unidades_en_un_cuadro_dejan_la_unidad_sin_afirmar():
    """`unidad` en None = no se puede rotular el total con una sola unidad."""
    b = agrupar_consolidado([_f(unidad="Tn"), _f(producto="SOJ", unidad="Bu")])[0]
    assert b["unidad"] is None
    assert b["unidades"] == ["Bu", "Tn"]


def test_los_nulos_no_rompen_el_total():
    """Una fila sin multiplicador trae compra/venta/neta en NULL."""
    b = agrupar_consolidado([_f(compra=None, venta=None, neta=None), _f(producto="SOJ")])[0]
    assert b["total"]["compra"] == 558700.0


def test_sin_filas_no_revienta():
    assert agrupar_consolidado([]) == []


# --------------------------------------------------------------------------- #
# SOJA CME → SOJA · CRN → MAIZ  (2026-08-26)
# --------------------------------------------------------------------------- #
def test_el_producto_de_chicago_cae_en_el_mismo_que_el_de_rosario():
    """`SOY` es la soja de Chicago y `CRN` el maíz de Chicago: para el reporte
    son SOJA y MAÍZ, no dos productos aparte.

    ⚠️ Se pueden sumar porque las cantidades YA están en toneladas —el
    multiplicador convierte contratos → unidad antes de esto—. Sumando
    CONTRATOS sería un error de 20×.
    """
    from api.services.ap5_posiciones import _PRODUCTO_SQL, PRODUCTO_CANONICO
    assert PRODUCTO_CANONICO == {"SOY": "SOJ", "CRN": "MAI"}
    # el mapeo tiene que estar EN la SQL, no sólo en el dict
    assert "SOY%%" in _PRODUCTO_SQL and "'SOJ'" in _PRODUCTO_SQL
    assert "CRN%%" in _PRODUCTO_SQL and "'MAI'" in _PRODUCTO_SQL


def test_no_queda_una_etiqueta_para_un_producto_que_ya_no_existe():
    """`SOY`/`CRN` se canonizan ANTES de etiquetar, así que una etiqueta para
    ellos sería código muerto que sugiere que todavía llegan."""
    from api.services.ap5_posiciones import ETIQUETAS, PRODUCTO_CANONICO
    assert not (set(ETIQUETAS) & set(PRODUCTO_CANONICO))
