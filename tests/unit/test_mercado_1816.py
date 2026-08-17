"""Tests del cliente de 1816 (core/mercado_1816.py) — parte pura, sin red.

Congela el aplanado de series (el shape verificado contra la API real 2026-07-18:
instrumentos.<ticker>.<campo> = [[fecha, valor], …]). Doc: docs/VISTA_RESEARCH.md.
"""
from __future__ import annotations

from core import mercado_1816


def test_parse_series_aplana_y_saltea_nulos():
    data = {
        "fuente": "byma", "moneda": "ars", "plazo": 1, "convencionTna": "180-360",
        "instrumentos": {
            "AL30": {
                "precioClean": [["2026-06-18", 134026.3], ["2026-06-19", 135000.0]],
                "tea": [["2026-06-18", 0.0926], ["2026-06-19", None]],  # null se saltea
            },
        },
    }
    filas = mercado_1816.parse_series(data)
    # 2 de precioClean + 1 de tea (el null no cuenta) = 3
    assert len(filas) == 3
    al30_tea = [f for f in filas if f["campo"] == "tea"]
    assert al30_tea == [{
        "ticker": "AL30", "fecha": "2026-06-18", "campo": "tea", "valor": 0.0926,
        "fuente": "byma", "moneda": "ars", "plazo": 1, "convencion_tna": "180-360",
    }]


def test_parse_series_vacio_no_rompe():
    assert mercado_1816.parse_series({}) == []
    assert mercado_1816.parse_series({"instrumentos": {}}) == []
    assert mercado_1816.parse_series({"instrumentos": {"AL30": {"tea": []}}}) == []


def test_cashflow_arma_path_y_campos(monkeypatch):
    """El ticker va en el PATH (no en query) y `campos` es obligatorio para la API:
    sin default explícito, un llamado sin campos daría 400."""
    visto = {}
    monkeypatch.setattr(mercado_1816, "_get",
                        lambda path, params=None: visto.update(path=path, params=params))
    mercado_1816.cashflow(" al30 ")
    assert visto["path"] == "/v1/mercado/cashflow/AL30"
    assert visto["params"]["campos"] == list(mercado_1816.CAMPOS_CASHFLOW)

    mercado_1816.cashflow("GD30", ["flujoTotal"])
    assert visto["params"] == {"campos": ["flujoTotal"]}


def test_cashflow_rechaza_ticker_corto():
    """La API pide >= 3 caracteres: se corta acá para no gastar el request."""
    for malo in ("", "  ", "AL"):
        try:
            mercado_1816.cashflow(malo)
        except mercado_1816.Error1816:
            continue
        raise AssertionError(f"debió rechazar {malo!r}")


def test_instrumentos_solo_performing(monkeypatch):
    """`solo_performing=False` es lo que agrega los VENCIDOS; omitirlo NO manda el
    parámetro (deja el default de la API)."""
    visto = {}
    monkeypatch.setattr(mercado_1816, "_get",
                        lambda path, params=None: visto.update(params=params))
    mercado_1816.instrumentos(curva_id=8)
    assert visto["params"] == {"curvaId": 8}
    mercado_1816.instrumentos(curva_id=8, solo_performing=False)
    assert visto["params"] == {"soloPerforming": "false", "curvaId": 8}


def test_disponible_depende_de_la_key(monkeypatch):
    monkeypatch.delenv("MERCADO_1816_API_KEY", raising=False)
    assert mercado_1816.disponible() is False
    monkeypatch.setenv("MERCADO_1816_API_KEY", "x")
    assert mercado_1816.disponible() is True


def test_la_METADATA_no_cuenta_como_DATOS_y_el_retroceso_igual_corre(monkeypatch):
    """**Regresión del 2026-08-17 (TMG27).** El predicado de «esta rueda trajo
    datos» era `any(campo is not None)`. Con 4 campos —todos de valor— funcionaba;
    al ampliar la lista para enriquecer el diagnóstico entraron `fuente`,
    `convencionTna` y `fechaLiquidacion`, que 1816 devuelve SIEMPRE aunque el
    precio sea `null`. Resultado: True en la primera vuelta y el retroceso NUNCA
    corría — un lunes temprano, o un papel que no operó ese día, contestaba «no
    publicó precio» en vez de traer la última rueda buena, que es justo para lo
    que existe esta función."""
    # El lunes 17 responde, pero solo con la ficha del pedido: ni precio ni tasa.
    # Antes del fix, ESTA respuesta se daba por buena.
    solo_ficha = {"precioDirty": None, "tea": None, "fuente": "byma",
                  "convencionTna": "180-360", "fechaLiquidacion": "2026-08-18"}
    ruedas = {
        "2026-08-17": {"instrumentos": {"AL30": solo_ficha},
                       "fechaOperacion": "2026-08-17"},
        "2026-08-14": {"instrumentos": {"AL30": {**solo_ficha, "precioDirty": 72.5,
                                                 "tea": 0.11}},
                       "fechaOperacion": "2026-08-14"},
    }
    pedidos, atras = [], []

    def _fake(tickers, campos, *, fecha_operacion=None, **kw):
        pedidos.append(fecha_operacion)
        return ruedas.get(fecha_operacion,
                          {"instrumentos": {"AL30": dict(solo_ficha)}})

    monkeypatch.setattr(mercado_1816, "indicadores", _fake)
    campos = ["precioDirty", "tea", "fuente", "convencionTna", "fechaLiquidacion"]
    r = mercado_1816.indicadores_vigentes(["AL30"], campos, fecha="2026-08-18",
                                          al_retroceder=lambda d: atras.append(d))

    assert r["fechaOperacion"] == "2026-08-14"
    assert r["instrumentos"]["AL30"]["precioDirty"] == 72.5
    # Martes 18 → lunes 17 (solo ficha, NO corta) → viernes 14 (el sábado y el
    # domingo los saltea `_habil_anterior`).
    assert pedidos == ["2026-08-18", "2026-08-17", "2026-08-14"]
    # Y las ruedas descartadas quedan anotadas: es lo que el mensaje de fracaso
    # necesita para decir QUÉ se probó en vez de culpar a la fecha del pedido.
    assert [d.isoformat() for d in atras] == ["2026-08-18", "2026-08-17"]


def test_pedir_SOLO_metadata_no_agota_el_retroceso(monkeypatch):
    """Si TODO lo pedido es metadata no hay mejor criterio que el viejo. Dejar la
    lista de campos-dato vacía haría que el predicado fuera False siempre y
    quemaría 5 llamadas a la API para devolver `{}` con la respuesta en la mano."""
    pedidos = []

    def _fake(tickers, campos, *, fecha_operacion=None, **kw):
        pedidos.append(fecha_operacion)
        return {"instrumentos": {"AL30": {"fuente": "byma"}},
                "fechaOperacion": fecha_operacion}

    monkeypatch.setattr(mercado_1816, "indicadores", _fake)
    r = mercado_1816.indicadores_vigentes(["AL30"], ["fuente"], fecha="2026-08-14")
    assert r["fechaOperacion"] == "2026-08-14"
    assert len(pedidos) == 1
