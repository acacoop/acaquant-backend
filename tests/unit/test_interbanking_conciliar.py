"""CONCILIAR: nuestro saldo del banco contra el último saldo del mayor contable.

⚠️ **Todo lo que se prueba acá está MEDIDO sobre el export real** (`mayor_36.xlsx`,
Credicoop, 2026-08-18), no supuesto. Dos cosas que parecían obvias y no lo eran:

1. **El valor de la celda ya viene FIRMADO** (`-499946423.26`). La `D`/`A` que se
   ve en Excel **es formato de celda**, no texto:
   `#,##0.00" D";#,##0.00" A"` — dos secciones, la segunda es la NEGATIVA y **no
   lleva el menos**. O sea: `A` = negativo, con el número al lado en valor
   absoluto.
2. El front lee con `raw: false` para no perder esa letra, así que al backend le
   llega el TEXTO formateado. Hay que aceptar las dos formas: si mañana alguien
   cambia el lector, o el archivo llega como CSV, el conciliador no se puede
   romper en silencio — un saldo mal leído es una conciliación que miente.
"""
from __future__ import annotations

from datetime import date

import pytest

from api.services.bancos import _num_mayor, _saldo_del_mayor

FECHA = date(2026, 8, 18)


# ── El número de una celda ──────────────────────────────────────────────────
@pytest.mark.parametrize("celda,esperado", [
    # Como llega del export real, ya formateado por SheetJS (separadores US).
    ("499,946,423.26 A", -499946423.26),
    ("53,464.73 D", 53464.73),
    # El mismo número escrito a la argentina: el separador decimal se decide por
    # POSICIÓN, no por convención de país.
    ("499.946.423,26 A", -499946423.26),
    ("53.464,73 D", 53464.73),
    # Sin letra: manda el signo si lo hay.
    ("-499946423.26", -499946423.26),
    ("1.234,56", 1234.56),
    ("1,234.56", 1234.56),
    # Un separador solo, con TRES decimales, es de miles.
    ("500.000", 500000.0),
    ("500,000", 500000.0),
    # Crudo (por si alguien lee con raw:true): ya viene firmado.
    (-499946423.26, -499946423.26),
    (53464.73, 53464.73),
    # Basura: no se inventa un cero.
    ("", None), (None, None), ("Saldo inicial", None), ("   ", None),
])
def test_lee_el_numero_venga_como_venga(celda, esperado):
    v = _num_mayor(celda)
    assert v is None if esperado is None else round(v, 2) == esperado


def test_la_letra_A_MANDA_sobre_el_signo():
    """En el formato del mayor el negativo se imprime SIN signo y con la `A`. Si
    llegaran los dos, decir dos veces lo mismo no puede dar positivo."""
    assert _num_mayor("-499.946.423,26 A") == -499946423.26


# ── Encontrar el último saldo ───────────────────────────────────────────────
# La grilla tal como la manda el front para el archivo real.
GRILLA = [
    ["Fecha", "Comprobante", "Concepto", "Auxiliar", "Asiento", "Debe", "Haber", "Saldo"],
    ["", "", "Saldo inicial", "", "", "53,464.73", "", "53,464.73 D"],
    ["18/08/2026 11:31:01", "", "[Op. 1133439] bco a bco", "GEN", "-4891400", "",
     "500,000,000.00", "499,946,535.27 A"],
    ["18/08/2026 12:39:51", "NT 2026010455", "[Op. 1133668] Recibir fondos", "GEN",
     "-4891641", "112.01", "", "499,946,423.26 A"],
    ["18/08/2026 14:43:01", "NT 2026010409", "[Op. 1132184] Enviar fondos", "GEN",
     "-4890189", "", "500,000,000.00", "999,946,423.26 A"],
    ["18/08/2026 18:07:04", "", "[Op. 1132505] bco a bco", "GEN", "-4890554",
     "500,000,000.00", "", "499,946,423.26 A"],
]


def test_encuentra_el_ultimo_saldo_del_archivo_real():
    r = _saldo_del_mayor(GRILLA)
    assert r["valor"] == -499946423.26
    assert r["fila"] == 6, "la ÚLTIMA fila con saldo, no la primera ni la mayor"
    assert r["columna"] == 7
    assert r["avisos"] == []


def test_la_columna_se_busca_por_ENCABEZADO_y_no_por_posicion():
    """El export puede traer títulos arriba o columnas de más: contar desde la
    izquierda se rompe el día que agreguen una."""
    con_titulo = [["Mayor de cuentas — Credicoop"], [], *GRILLA]
    corrido = [[*f[:2], "COLUMNA NUEVA", *f[2:]] for f in GRILLA]
    assert _saldo_del_mayor(con_titulo)["valor"] == -499946423.26
    assert _saldo_del_mayor(corrido)["valor"] == -499946423.26


def test_sin_columna_saldo_adivina_pero_lo_DICE():
    """Adivinar está bien; adivinar en silencio es cómo un número equivocado pasa
    por bueno."""
    sin_encabezado = [f[:-1] + [f[-1]] for f in GRILLA[1:]]   # sin la fila de títulos
    r = _saldo_del_mayor(sin_encabezado)
    assert r["valor"] == -499946423.26
    assert r["avisos"], "tiene que avisar que la columna la eligió él"


def test_un_archivo_vacio_no_rompe():
    assert _saldo_del_mayor([])["valor"] is None
    assert _saldo_del_mayor([[], [""]])["valor"] is None


# ── La comparación ──────────────────────────────────────────────────────────
def _svc(monkeypatch, *, cierre=None, movs=(), manuales=()):
    from api.services import bancos as svc

    def _q(sql, params=None):
        t = " ".join(str(sql).split())
        # ⚠️ El ORDEN importa: `_saldos_banco` trae extracto + saldo informado +
        # manuales en UNA sola query, así que matchea varios de los substrings de
        # abajo. Va primera y se reconoce por los manuales, que ninguna otra pide.
        # `_saldos_banco` toca cinco tablas en una query; se reconoce por su
        # alias `AS sellado` y va PRIMERO, o cae en los `if` de abajo.
        if "AS sellado" in t:
            man = sum(m["ajuste"] for m in manuales)
            return [{"cuenta_id": 1, "sellado": None, "saldo_cierre": cierre,
                     "informado": None, "neto": 0, "ajuste": man, "acumulado": man}]
        if "cierres_diarios" in t:
            return []          # la apertura no se usa en estos tests
        if "FROM bancos.cuentas" in t:
            return [{"id": 1, "bank_number": "191", "bank_name": "Credicoop",
                     "account_number": "0010701456", "account_type": "CC",
                     "currency": "ARS", "account_label": "ACA", "activa": True,
                     "origen": "interbanking"}]
        if "FROM bancos.movimientos" in t:
            return list(movs)
        return []

    monkeypatch.setattr(svc, "_q", _q)
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    return svc


def _mov(h, importe, tipo, desc=""):
    return {"mov_hash": h, "fecha": FECHA, "fecha_proceso": None, "importe": importe,
            "tipo": tipo, "descripcion_banco": desc, "descripcion_ib": "",
            "codigo_operacion_ib": "1", "comprobante": 1}


def test_cuando_los_dos_saldos_coinciden_CONCILIA(monkeypatch):
    s = _svc(monkeypatch, cierre=53464.73)
    out = s.conciliar("x@y", 1, FECHA, GRILLA[:2])
    assert out["saldo_excel"] == 53464.73
    assert out["diferencia"] == 0.0
    assert out["concilia"] is True


def test_la_diferencia_se_explica_con_UN_movimiento(monkeypatch):
    """El caso que motivó la pantalla: al mayor le falta registrar algo que el
    banco sí informó, así que la diferencia es exactamente ese movimiento."""
    s = _svc(monkeypatch, cierre=53464.73 - 112.01,
             movs=[_mov("h1", 112.01, "D", "COMISION"), _mov("h2", 5000.0, "C")])
    out = s.conciliar("x@y", 1, FECHA, GRILLA[:2])
    assert out["concilia"] is False
    assert out["diferencia"] == -112.01
    assert len(out["candidatos"]) == 1
    assert out["candidatos"][0]["movimientos"][0]["mov_hash"] == "h1"


def test_si_ningun_movimiento_explica_la_diferencia_lo_DICE(monkeypatch):
    """«No encontré» tiene que decirse: sin eso, una lista vacía se lee como que
    no hay diferencia."""
    s = _svc(monkeypatch, cierre=999.99, movs=[_mov("h1", 5.0, "D")])
    out = s.conciliar("x@y", 1, FECHA, GRILLA[:2])
    assert out["candidatos"] == []
    assert any("revisarlo a mano" in a for a in out["avisos"])


def test_avisa_cuando_la_diferencia_es_solo_el_SIGNO(monkeypatch):
    """La pista que ahorra una hora: si los dos saldos coinciden al dar vuelta el
    signo del mayor, no falta ningún movimiento — la cuenta está del otro lado.
    ⚠️ NO se corrige solo: invertir un signo por nuestra cuenta es exactamente
    cómo se fabrica una conciliación que miente."""
    s = _svc(monkeypatch, cierre=499946423.26)
    out = s.conciliar("x@y", 1, FECHA, GRILLA)
    assert out["concilia"] is False
    assert any("invierte el signo" in a for a in out["avisos"])
    assert out["saldo_excel"] == -499946423.26, "el signo NO se tocó"


def test_el_ajuste_manual_entra_y_se_avisa(monkeypatch):
    """Nuestro saldo es el MISMO que muestra el consolidado, ajuste incluido: si
    acá se usara el saldo pelado, dos pantallas dirían dos números para la misma
    cuenta y el mismo día."""
    s = _svc(monkeypatch, cierre=53000.0,
             manuales=[{"cuenta_id": 1, "ajuste": 464.73, "n": 1}])
    out = s.conciliar("x@y", 1, FECHA, GRILLA[:2])
    assert out["saldo_nuestro"] == 53464.73
    assert out["ajuste_manual"] == 464.73
    assert out["concilia"] is True
    assert any("cargados a mano" in a for a in out["avisos"])


def test_sin_saldo_nuestro_no_se_inventa_una_conciliacion(monkeypatch):
    s = _svc(monkeypatch, cierre=None)
    out = s.conciliar("x@y", 1, FECHA, GRILLA)
    assert out["saldo_nuestro"] is None
    assert out["diferencia"] is None
    assert out["concilia"] is None


def test_una_cuenta_que_no_existe_se_rechaza(monkeypatch):
    from api.services import bancos as svc
    monkeypatch.setattr(svc, "_q", lambda sql, params=None: [])
    with pytest.raises(ValueError, match="no existe"):
        svc.conciliar("x@y", 99, FECHA, GRILLA)


# ── El detalle de los dos lados ─────────────────────────────────────────────
# ⚠️ `importe = Debe − Haber`, medido sobre el export real: el saldo inicial más
# la suma de los movimientos da EXACTAMENTE el saldo final del archivo. Esa
# igualdad no es un detalle contable — es lo que permite auto-verificar el
# parseo.
def test_el_importe_del_mayor_es_debe_menos_haber():
    from api.services.bancos import _movimientos_del_mayor

    d = _movimientos_del_mayor(GRILLA)
    assert [m["importe"] for m in d["movimientos"]] == [
        -500_000_000.0, 112.01, -500_000_000.0, 500_000_000.0]
    assert d["suma"] == -499_999_887.99


def test_una_fila_SIN_FECHA_no_es_un_movimiento():
    """El saldo inicial tiene Debe cargado pero no es un movimiento. Se lo
    reconoce por la FECHA y no tratando de identificarlo por su saldo.

    ⚠️ La versión anterior identificaba el «saldo inicial» y auto-verificaba el
    parseo (inicial + movimientos = saldo final). Se sacó: el formato del mayor
    admite hasta 7 decimales (`#,##0.00#####`), así que un `1.515.504,677` es
    genuinamente ambiguo contra un separador de miles — el saldo inicial se leyó
    MIL VECES más grande y el aviso salió gritando en un archivo perfecto. Un
    aviso que grita cuando no pasa nada entrena a ignorar todos los avisos."""
    from api.services.bancos import _movimientos_del_mayor

    d = _movimientos_del_mayor(GRILLA)
    assert len(d["movimientos"]) == 4, "las 4 filas con fecha, no las 5 con importe"
    assert all("Saldo inicial" not in m["concepto"] for m in d["movimientos"])
    assert d["avisos"] == []


def test_los_dos_detalles_viajan_en_la_respuesta(monkeypatch):
    s = _svc(monkeypatch, cierre=53464.73,
             movs=[_mov("h1", 112.01, "D", "COMISION"), _mov("h2", 5000.0, "C", "TRF")])
    out = s.conciliar("x@y", 1, FECHA, GRILLA)
    assert [m["importe"] for m in out["banco_movimientos"]] == [-112.01, 5000.0]
    assert out["banco_suma"] == 4887.99
    assert len(out["mayor_movimientos"]) == 4
    assert out["mayor_suma"] == -499_999_887.99


# ── Encontrar la explicación cuando no es exacta ────────────────────────────
# ⚠️ Caso real (2026-08-19): la diferencia daba 1.176.659,79 y el movimiento que
# la explicaba era de 1.176.659,78. UN CENTAVO. Con igualdad exacta el buscador
# contestaba «ningún movimiento da exactamente esa diferencia» y escondía el
# movimiento que cualquiera reconoce de un vistazo.
def test_encuentra_el_movimiento_aunque_falte_UN_CENTAVO(monkeypatch):
    s = _svc(monkeypatch, cierre=1_342_918.71,
             movs=[_mov("h1", 1_176_659.78, "C", "CREDITO POR DATANET"),
                   _mov("h2", 300.0, "D", "COMISION")])
    # El mayor cierra en 166.258,92 → diferencia 1.176.659,79.
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["diferencia"] == 1_176_659.79
    assert len(out["candidatos"]) == 1
    c = out["candidatos"][0]
    assert c["movimientos"][0]["descripcion"] == "CREDITO POR DATANET"
    assert c["resto"] == 0.01, "y dice cuánto sobra: no se hace pasar por exacta"
    # El resto viaja EN EL CANDIDATO y no como párrafo aparte: la pantalla lo
    # marca al lado del movimiento al que le pasa. Un aviso arriba hablaba de
    # todas las opciones para algo que le pasa a una.
    assert not any("no es exacta" in a for a in out["avisos"])


def test_una_coincidencia_EXACTA_gana_sobre_una_aproximada(monkeypatch):
    """El orden de las pasadas importa: si se buscara con tolerancia desde el
    principio, «esto es» y «esto se le parece» valdrían lo mismo."""
    s = _svc(monkeypatch, cierre=166_258.92 + 500.0,
             movs=[_mov("h1", 500.0, "C", "EXACTO"),
                   _mov("h2", 500.5, "C", "PARECIDO")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert len(out["candidatos"]) == 1
    assert out["candidatos"][0]["movimientos"][0]["descripcion"] == "EXACTO"
    assert out["candidatos"][0]["resto"] == 0.0


def test_encuentra_el_movimiento_con_el_SIGNO_al_reves(monkeypatch):
    """El mismo importe de la otra mano sigue siendo EL movimiento: el back
    office necesita verlo. Se marca en vez de disimularlo."""
    s = _svc(monkeypatch, cierre=166_258.92 - 900.0,
             movs=[_mov("h1", 900.0, "C", "TRANSFERENCIA")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["diferencia"] == -900.0
    assert out["candidatos"][0]["signo_invertido"] is True
    # Se marca EN EL CANDIDATO (la pantalla le pone la etiqueta al lado), no en
    # un aviso de texto suelto.
    assert not any("signo AL REVÉS" in a for a in out["avisos"])


def test_una_diferencia_grande_NO_se_explica_con_cualquier_cosa(monkeypatch):
    """La tolerancia es UN PESO fijo y no un porcentaje: sobre 500 millones, un
    porcentaje daría miles de pesos de margen y empezaría a «encontrar»
    coincidencias que no lo son."""
    s = _svc(monkeypatch, cierre=166_258.92 + 1_000_000.0,
             movs=[_mov("h1", 999_950.0, "C", "PARECIDO PERO NO")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["candidatos"] == []
    assert any("revisarlo a mano" in a for a in out["avisos"])


# ── El margen es PROPORCIONAL, con piso ─────────────────────────────────────
# Un margen fijo no escala en los dos sentidos: sobre 500 millones, un peso es
# tan estricto como la igualdad exacta y vuelve a esconder el movimiento; sobre
# mil pesos, un porcentaje solo tampoco alcanzaría para un centavo. Van los dos.
@pytest.mark.parametrize("diferencia,esperado", [
    (100.0, 1.0),               # chicas: manda el piso
    (1_176_659.79, 11.77),      # el caso real
    (500_000_000.0, 5_000.0),   # grandes: manda el porcentaje
])
def test_el_margen_escala_con_la_diferencia(diferencia, esperado):
    from api.services.bancos import tolerancia
    assert tolerancia(diferencia) == esperado


def test_el_margen_NO_alcanza_para_hacer_pasar_un_movimiento_por_otro(monkeypatch):
    """0,001% es un peso cada 100.000: alcanza para un redondeo y no para
    confundir dos movimientos distintos.

    ⚠️ Y el margen APROXIMADO (0,5%) tampoco lo deja pasar, porque **solo aplica
    a combinaciones**: un movimiento suelto que «casi» da es OTRO movimiento."""
    s = _svc(monkeypatch, cierre=166_258.92 + 1_000_000.0,
             movs=[_mov("h1", 999_950.0, "C", "PARECIDO PERO NO")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["tolerancia"] == 10.0, "±10 sobre un millón"
    assert out["candidatos"] == [], "50.000 de distancia no es un redondeo"


# ── CALZAR POR IMPORTE ──────────────────────────────────────────────────────
# Las leyendas de los dos lados no se parecen y cambian todo el tiempo
# (`TRANSFERENCIA ENTRE CUENT` contra `[Op. 1136612] bco a bco`), así que
# cruzarlas por texto es imposible. Lo único que significa lo mismo de los dos
# lados es el IMPORTE.
MAYOR_100_Y_50 = [
    ["Fecha", "Concepto", "Debe", "Haber", "Saldo"],
    ["", "Saldo inicial", "", "", "166,258.92 D"],
    ["18/08/2026", "[Op. 1] Depósito", "100.00", "", "166,358.92 D"],
    ["18/08/2026", "[Op. 2] Extracción", "", "50.00", "166,308.92 D"],
]


@pytest.mark.parametrize("valores,esperado", [
    # Uno a uno: el mismo importe tres veces de un lado y dos del otro deja UNO
    # suelto. Calzar «el grupo contra el grupo» taparía justo el que falta.
    (([100.0, 100.0, 100.0], [100.0, 100.0]), (3 - 2, 0)),
    # El signo importa: un crédito no calza contra un débito del mismo tamaño.
    (([100.0], [-100.0]), (1, 1)),
    (([], [500.0]), (0, 1)),
])
def test_el_calce_es_UNO_A_UNO_y_por_importe_exacto(valores, esperado):
    from api.services.bancos import _calzar_por_importe

    izq, der = _calzar_por_importe(*valores)
    assert (sum(1 for c in izq if c is None),
            sum(1 for c in der if c is None)) == esperado
    # Cada par tiene su marca de los DOS lados: es lo que la pantalla usa para
    # esconderlos juntos.
    assert sorted(c for c in izq if c) == sorted(c for c in der if c)


def test_lo_calzado_sale_de_la_busqueda_y_de_los_totales_sin_calzar(monkeypatch):
    """Dos cosas de un solo caso, y las dos son el punto de la feature:

    1. Un movimiento que tiene su igual del otro lado YA está registrado en los
       dos sistemas: no puede ser el que falta, así que no se lo propone como
       explicación. Acá los dos créditos de 100 son idénticos y solo el que
       quedó SUELTO aparece como candidato.
    2. La resta de las dos sumas «sin calzar» ES la diferencia — los pares se
       cancelan entre sí. Por eso la vista filtrada muestra exactamente los
       movimientos que la producen y ninguno más.
    """
    s = _svc(monkeypatch, cierre=166_308.92 + 100.0,
             movs=[_mov("h1", 100.0, "C", "TRANSFERENCIA"),
                   _mov("h2", 100.0, "C", "TRANSFERENCIA"),
                   _mov("h3", 50.0, "D", "EXTRACCION")])
    out = s.conciliar("x@y", 1, FECHA, MAYOR_100_Y_50)

    assert out["diferencia"] == 100.0
    assert out["calce"]["pares"] == 2
    assert out["calce"]["banco_sin_calzar"] == 1
    assert out["calce"]["mayor_sin_calzar"] == 0
    assert (out["calce"]["banco_suma_sin_calzar"]
            - out["calce"]["mayor_suma_sin_calzar"]) == out["diferencia"]

    # El calce viaja movimiento por movimiento: la vista esconde por este campo.
    assert [bool(m["calce"]) for m in out["banco_movimientos"]] == [True, False, True]
    assert all(m["calce"] for m in out["mayor_movimientos"])

    assert len(out["candidatos"]) == 1, "el 100 ya calzado no se propone otra vez"
    assert out["candidatos"][0]["movimientos"][0]["mov_hash"] == "h2"


# ── Combinaciones LARGAS y explicaciones aproximadas ────────────────────────
# ⚠️ Caso real (2026-08-21): una diferencia de 4.256.787,71 que salía de sumar
# VARIOS movimientos del banco. El buscador viejo combinaba hasta 3 con
# `combinations()` y ni siquiera generaba la explicación: contestaba «ningún
# movimiento llega a esa diferencia» y escondía la única pista que había.
def test_encuentra_una_combinacion_de_MAS_DE_TRES_movimientos(monkeypatch):
    s = _svc(monkeypatch, cierre=166_258.92 + 1_500.0,
             movs=[_mov(f"h{i}", v, "C", f"TRF {i}")
                   for i, v in enumerate([100.0, 200.0, 300.0, 400.0, 500.0])])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["diferencia"] == 1_500.0
    c = out["candidatos"][0]
    assert c["cantidad"] == 5
    assert c["resto"] == 0.0
    assert c["aproximado"] is False


def test_cuando_NO_da_exacto_publica_la_APROXIMADA_marcada_y_con_el_resto(monkeypatch):
    """«Sumando estos casi llegás» es una pista, no un hallazgo: se muestra, pero
    marcada como aproximada y diciendo cuánto queda sin explicar. Esconderla
    manda a hacer a mano exactamente la misma suma."""
    s = _svc(monkeypatch, cierre=166_258.92 + 1_000.0,
             movs=[_mov("h1", 590.0, "C", "TRF A"), _mov("h2", 400.0, "C", "TRF B")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    c = out["candidatos"][0]
    assert c["aproximado"] is True
    assert c["cantidad"] == 2
    assert c["suma"] == 990.0
    assert c["resto"] == 10.0, "lo que queda sin explicar se dice SIEMPRE"
    # El margen del último recurso se publica: es el que más fácil puede hacer
    # pasar una coincidencia por un hallazgo.
    assert out["tolerancia_aproximada"] == 100.0


def test_una_explicacion_EXACTA_gana_sobre_una_aproximada_tambien_en_combinacion(monkeypatch):
    s = _svc(monkeypatch, cierre=166_258.92 + 1_000.0,
             movs=[_mov("h1", 590.0, "C", "CASI"), _mov("h2", 400.0, "C", "CASI"),
                   _mov("h3", 700.0, "C", "EXACTO"), _mov("h4", 300.0, "C", "EXACTO")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    c = out["candidatos"][0]
    assert c["aproximado"] is False
    assert c["resto"] == 0.0
    assert {m["descripcion"] for m in c["movimientos"]} == {"EXACTO"}


# ── Explicaciones POR CONSTRUCCIÓN ──────────────────────────────────────────
# ⚠️ Caso real (Patagonia, 19/08/2026): al mayor le faltaban los ONCE movimientos
# del banco que no calzaron — 4 créditos grandes y 7 débitos que eran los gastos
# bancarios. `_buscar` no podía encontrarlo por DOS motivos a la vez: la
# combinación mezcla signos (prohibido, y con razón) y son 11 contra un tope de
# 8. La pantalla decía «ningún movimiento llega a esa diferencia» con la
# respuesta entera a la vista, y el back office la sumaba a mano.
#
# La salida NO fue aflojar `_buscar`: fue ver que esto no es una búsqueda. Si al
# mayor no le quedó nada sin calzar, que todo lo que le falta sea todo lo que al
# banco le sobró no es un hallazgo, es una IDENTIDAD. Por eso se AFIRMA en vez de
# buscarse, y por eso este candidato sí puede mezclar signos: no elige nada.
def test_si_al_mayor_no_le_queda_nada_sin_calzar_FALTAN_TODOS_los_del_banco(monkeypatch):
    s = _svc(monkeypatch, cierre=166_308.92 + 1_000.0 - 300.0,
             movs=[_mov("h1", 100.0, "C", "CALZA"),      # tiene su par en el mayor
                   _mov("h2", 1_000.0, "C", "TRANSFERENCIA"),
                   _mov("h3", 300.0, "D", "COMISION"),
                   _mov("h4", 50.0, "D", "CALZA")])      # tiene su par en el mayor
    out = s.conciliar("x@y", 1, FECHA, MAYOR_100_Y_50)

    assert out["diferencia"] == 700.0
    assert out["calce"]["mayor_sin_calzar"] == 0
    c = out["candidatos"][0]
    assert c["cantidad"] == 2, "los dos que NO calzaron, con signos mezclados"
    assert c["suma"] == 700.0
    assert c["resto"] == 0.0
    assert c["accion"] == "falta_en_el_mayor"
    assert "todo lo que no calzó del banco" in (c["motivo"] or "")
    assert {m["descripcion"] for m in c["movimientos"]} == {"TRANSFERENCIA", "COMISION"}


def test_si_los_DOS_lados_tienen_sueltos_no_se_afirma_nada(monkeypatch):
    """La identidad se rompe: con movimientos sin calzar de los dos lados, el
    conjunto de uno solo ya no es «todo lo que falta». Ahí no hay nada que
    afirmar y se vuelve a lo que se pueda buscar."""
    s = _svc(monkeypatch, cierre=166_308.92 + 1_000.0,
             movs=[_mov("h1", 100.0, "C", "CALZA"), _mov("h2", 1_000.0, "C", "TRF")])
    # El mayor tiene su extracción de 50 sin par del lado del banco.
    out = s.conciliar("x@y", 1, FECHA, MAYOR_100_Y_50)
    assert out["calce"]["mayor_sin_calzar"] == 1
    assert not any("todo lo que no calzó" in (c["motivo"] or "")
                   for c in out["candidatos"])



# ── El umbral NOMINAL de $1 ─────────────────────────────────────────────────
# Caso real: 590.708,27 contra 590.708,12 — QUINCE CENTAVOS que la pantalla
# mostraba como hallazgo y mandaban al back office a buscar un movimiento
# inexistente. Debajo de un peso no hay ningún movimiento que pueda explicar la
# diferencia: es redondeo del sistema contable.
@pytest.mark.parametrize("cierre,concilia", [
    (166_258.92, True),          # idénticos
    (166_259.07, True),          # 15 centavos → no hay diferencia
    (166_259.91, True),          # 99 centavos → tampoco
    (166_259.92, False),         # un peso → sí
    (166_358.92, False),         # cien pesos → sí
])
def test_debajo_de_UN_PESO_no_hay_diferencia(monkeypatch, cierre, concilia):
    s = _svc(monkeypatch, cierre=cierre)
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["concilia"] is concilia


# ── El SIGNO dice de qué lado está el problema ──────────────────────────────
# diferencia = nuestro − mayor:
#   · positiva → el banco tiene más: FALTA un movimiento en el mayor;
#   · negativa → el mayor tiene más: SOBRA un movimiento en el mayor.
def test_diferencia_POSITIVA_es_que_FALTA_en_el_mayor(monkeypatch):
    s = _svc(monkeypatch, cierre=166_258.92 + 5_000.0,
             movs=[_mov("h1", 5_000.0, "C", "CREDITO POR DATANET")])
    grilla = [["Fecha", "Concepto", "Debe", "Haber", "Saldo"],
              ["", "Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    c = out["candidatos"][0]
    assert c["lado"] == "banco"
    assert c["accion"] == "falta_en_el_mayor"
    assert c["movimientos"][0]["descripcion"] == "CREDITO POR DATANET"


def test_diferencia_NEGATIVA_es_que_SOBRA_en_el_mayor(monkeypatch):
    """Y la descripción sale TAL COMO LA ESCRIBE EL MAYOR: es lo que la hace
    encontrable en el sistema donde hay que ir a borrarla."""
    s = _svc(monkeypatch, cierre=166_258.92 - 7_000.0)
    grilla = [["Fecha", "Concepto", "Debe", "Haber", "Saldo"],
              ["", "Saldo inicial", "", "", "173,258.92 D"],
              ["18/08/2026", "[Op. 1131723] Extracción CE 2026005024", "", "7,000.00",
               "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    c = out["candidatos"][0]
    assert c["lado"] == "mayor"
    assert c["accion"] == "sobra_en_el_mayor"
    assert c["movimientos"][0]["descripcion"] == "[Op. 1131723] Extracción CE 2026005024"


def test_NUNCA_se_cruzan_movimientos_de_los_dos_lados(monkeypatch):
    """Una explicación que mezcla un movimiento del banco con uno del mayor no es
    una explicación: es una coincidencia aritmética. Acá ningún lado llega solo a
    la diferencia, aunque sumados sí darían — y tiene que decir que no encontró."""
    s = _svc(monkeypatch, cierre=166_258.92 + 300.0,
             movs=[_mov("h1", 100.0, "C", "DEL BANCO")])
    grilla = [["Fecha", "Concepto", "Debe", "Haber", "Saldo"],
              ["", "Saldo inicial", "", "", "166,458.92 D"],
              ["18/08/2026", "DEL MAYOR", "", "200.00", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["diferencia"] == 300.0
    assert out["candidatos"] == [], "100 del banco + 200 del mayor NO es una explicación"


# ── Lo confirmado se anota ──────────────────────────────────────────────────
def test_la_accion_se_valida(monkeypatch):
    from api.services import bancos as svc
    monkeypatch.setattr(svc, "_q", lambda sql, params=None: [{"id": 1}])
    monkeypatch.setattr(svc, "_exec", lambda sql, params=None: 1)
    with pytest.raises(ValueError, match="Acción inválida"):
        svc.confirmar_pendiente("x@y", 1, FECHA, "arreglalo", "X", 10.0)
    with pytest.raises(ValueError, match="descripción"):
        svc.confirmar_pendiente("x@y", 1, FECHA, "falta_en_el_mayor", "  ", 10.0)
