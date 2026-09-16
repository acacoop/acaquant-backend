"""Tests del cliente de 1816 (core/mercado_1816.py) — parte pura, sin red.

Congela el aplanado de series (el shape verificado contra la API real 2026-07-18:
instrumentos.<ticker>.<campo> = [[fecha, valor], …]). Doc: docs/RESEARCH.md.
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


def test_el_token_es_COMPARTIDO_y_los_logins_son_un_recurso_escaso():
    """El «auth HTTP 429» nunca fue un problema de autenticación ni de créditos.
    El panel del plan (2026-08-17) lo dice:

        Créditos diarios      3.863 / 100.000    ← sobra
        Máx. peticiones/seg   1
        **Máx. tokens por día   50**             ← ESTE

    El token dura 24 h, así que UNO alcanza para todo el día — pero vivía en un
    dict de módulo, o sea en memoria de CADA proceso: `tamar_1816` corre 15 veces
    por día, `api.service` pide uno nuevo en cada deploy, más cada job y cada
    corrida manual. Cada uno quemaba uno de los 50.

    Y el backoff lo empeoraba: **reintentar contra una CUOTA consume justo el
    recurso que se acabó**. Backoff es la respuesta correcta a un rate limit
    (transitorio) y la peor posible a una cuota diaria."""
    import inspect

    from core import mercado_1816 as m

    src = inspect.getsource(m._token)
    # El orden es del más barato al más caro, y el login va ÚLTIMO.
    assert src.index('_estado["token"]') < src.index("_fila_token()") < src.index("_auth()")
    assert "_LOGINS_MAX_DIA" in src, "el presupuesto se chequea ANTES de gastar"

    # Un `_auth` exitoso tiene que COMPARTIR el token o el ahorro no existe.
    assert "_guardar_token(" in inspect.getsource(m._auth)

    # Contra una cuota no se reintenta cinco veces.
    assert m._AUTH_REINTENTOS == 2
    assert m._LOGINS_MAX_DIA < 50, "hay que dejar margen bajo el tope del plan"

    # Sin Postgres NO se rompe: un cliente de red no puede quedar inutilizable
    # porque la base no conteste. Se pierde el ahorro, no el servicio.
    assert m._fila_token() == {} or isinstance(m._fila_token(), dict)
    est = m.estado_token()
    assert est["tope"] == m._LOGINS_MAX_DIA and "restantes" in est


def test_el_limite_de_1_PETICION_POR_SEGUNDO_es_GLOBAL():
    """El throttle vivía en memoria, así que garantizaba el intervalo *dentro de un
    proceso* — y los que tocan 1816 son varios a la vez (`api.service` sirviendo el
    modal mientras corre el cron de `tamar_1816`). Dos procesos con 2,5 s cada uno
    pueden mandar dos peticiones en el mismo segundo sin enterarse, y el plan dice
    **máx. 1/seg**."""
    import inspect

    from core import mercado_1816 as m

    assert m._MIN_INTERVALO_GLOBAL_S >= 1.0, "el plan permite 1 petición por segundo"
    src = inspect.getsource(m._throttle)
    assert "_marcar_llamada()" in src, "el intervalo global tiene que entrar al throttle"

    # ⚠️ El previo se guarda en su PROPIA columna: en un `ON CONFLICT DO UPDATE
    # ... RETURNING`, Postgres devuelve la fila NUEVA, así que restarle `now()` a
    # `llamadas_at` daría siempre 0 y el throttle global sería decorativo.
    marc = inspect.getsource(m._marcar_llamada)
    assert "llamadas_prev_at" in marc
    assert "RETURNING extract(epoch from (now() - llamadas_prev_at))" in marc
    # Sin base devuelve 0 y manda el throttle local (degradar ≠ fallar).
    assert m._marcar_llamada() == 0.0


def test_la_moneda_de_las_series_sale_de_en_que_PAGA_el_bono():
    """`moneda_series` es la regla que decide a qué dólar se pide —y se lee— la
    serie de cada bono, y el predicado es `monedaPago`, NUNCA `monedaDenom`.

    El caso que obliga a distinguirlos son los **dólar-linked**: denominados en
    USD, pagan en pesos. Con `monedaDenom` caerían en `mep`, que 1816 no publica
    para ellos (medido 2026-08-17 con D30O6: todo `None`) → la serie quedaría
    VACÍA y el bono desaparecería del laboratorio, sin un solo error.
    """
    from core import mercado_1816 as m

    assert m.moneda_series("USD") == "mep"      # Bonares, Globales, BOPREALes
    assert m.moneda_series("usd") == "mep"      # el catálogo no garantiza la caja
    assert m.moneda_series("ARS") == "ars"      # CER, tasa fija, TAMAR, duales
    assert m.moneda_series("ars") == "ars"
    # Dólar-linked: el catálogo dice monedaDenom=USD y monedaPago=ARS. Lo que
    # manda es el pago.
    assert m.moneda_series("ARS") == "ars"
    # Sin ficha en el catálogo NO se inventa una conversión: queda el default de
    # la API, que es lo que se venía haciendo.
    assert m.moneda_series(None) == "ars"
    assert m.moneda_series("") == "ars"
    assert m.moneda_series("   ") == "ars"
    # Y lo que devuelve tiene que ser un valor que la API acepte: un valor
    # inventado no falla en su campo, hace fallar la llamada ENTERA.
    assert m.moneda_series("USD") in m.MONEDAS
    assert m.moneda_series("ARS") in m.MONEDAS


def test_el_writer_y_el_reader_de_series_usan_LA_MISMA_regla_de_moneda():
    """Nadie más puede derivar la moneda por su cuenta.

    Son dos mitades del mismo dato: `jobs/mercado_1816_series` decide en qué
    moneda GRABA y `api/services/research_1816_sql` en qué moneda BUSCA. Si cada
    uno tuviera su copia del criterio, el día que difieran no falla nada — el
    job escribe filas en `mep` y la lectura pide `ars`, y el gráfico queda vacío
    o dibuja la serie vieja al CCL. Es la REGLA #9 en una línea de código.
    """
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parents[2]
    for archivo in ("jobs/mercado_1816_series.py",
                    "api/services/research_1816_sql.py"):
        src = (raiz / archivo).read_text(encoding="utf-8")
        assert "mercado_1816.moneda_series(" in src, (
            f"{archivo} tiene que DERIVAR la moneda con core.mercado_1816."
            "moneda_series, no decidirla por su cuenta")


def test_la_lectura_de_series_FILTRA_por_moneda():
    """`moneda` es parte de la PK de `mkt_1816_series`, así que las filas en
    `mep` conviven con las viejas en `ars` del MISMO ticker, fecha y campo.

    Una lectura sin `moneda` en el WHERE devuelve las dos, y el JOIN de
    `spread()` devuelve el producto cartesiano: hasta cuatro spreads por fecha,
    todos plausibles, ninguno marcado. Este test congela el filtro.
    """
    import inspect

    from api.services import research_1816_sql as svc

    ser = inspect.getsource(svc.series)
    assert "moneda = %s" in ser, "series() tiene que filtrar por moneda"
    spr = inspect.getsource(svc.spread)
    assert "x.moneda = %s" in spr and "y.moneda = %s" in spr, (
        "spread() tiene que filtrar LOS DOS lados del JOIN por moneda")


def test_la_moneda_efectiva_no_deja_el_grafico_en_blanco_antes_del_backfill():
    """Entre el deploy y el rebajado, un hard dollar tiene solo la serie vieja en
    `ars`. Exigir `mep` a secas lo borraría del selector y dejaría el gráfico
    vacío; el fallback devuelve lo que HAY y el rótulo de la UI dice CCL."""
    from api.services.research_1816_sql import _moneda_efectiva

    assert _moneda_efectiva("mep", {"mep": 1364, "ars": 1364}) == "mep"
    assert _moneda_efectiva("mep", {"ars": 1364}) == "ars"      # todavía sin rebajar
    assert _moneda_efectiva("ars", {"ars": 672}) == "ars"
    assert _moneda_efectiva("mep", {}) == "mep"                 # bono sin series
    # Con varias y ninguna igual al objetivo, gana la que más historia tiene.
    assert _moneda_efectiva("mep", {"ars": 10, "ccl": 900}) == "ccl"
