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
        if "FROM bancos.cuentas" in t:
            return [{"id": 1, "bank_number": "191", "bank_name": "Credicoop",
                     "account_number": "0010701456", "account_type": "CC",
                     "currency": "ARS", "account_label": "ACA", "activa": True,
                     "origen": "interbanking"}]
        if "extracto_dia" in t:
            return [{"saldo_cierre": cierre}] if cierre is not None else []
        if "movimientos_manuales" in t:
            return list(manuales)
        if "FROM bancos.saldos" in t:
            return []
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
    assert any("Ningún movimiento" in a for a in out["avisos"])


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
    assert any("no es exacta" in a for a in out["avisos"])


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
    assert any("signo AL REVÉS" in a for a in out["avisos"])


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
    assert any("Ningún movimiento" in a for a in out["avisos"])


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
    confundir dos movimientos distintos."""
    s = _svc(monkeypatch, cierre=166_258.92 + 1_000_000.0,
             movs=[_mov("h1", 999_950.0, "C", "PARECIDO PERO NO")])
    grilla = [["Concepto", "Debe", "Haber", "Saldo"],
              ["Saldo inicial", "", "", "166,258.92 D"]]
    out = s.conciliar("x@y", 1, FECHA, grilla)
    assert out["tolerancia"] == 10.0, "±10 sobre un millón"
    assert out["candidatos"] == [], "50.000 de distancia no es un redondeo"
