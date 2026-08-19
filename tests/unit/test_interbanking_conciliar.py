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
