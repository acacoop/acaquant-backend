"""Tests de core/pii_gateway.py — la ADUANA (pieza de SEGURIDAD del asistente).

El test crítico es NO-LEAK: dado un texto con identidades de clientes, NINGÚN
nombre/cuenta/documento real puede sobrevivir en el texto tokenizado. Si algo
de acá falla, el asistente está filtrando datos al proveedor — es un bug de
seguridad, no un bug funcional.

El catálogo se FALSEA (monkeypatch) — estos tests no tocan la DB.
"""
from __future__ import annotations

import pytest

from core import pii_gateway as pg

# Catálogo falso con los formatos reales: denominación con [id], mayúsculas,
# acentos, empresas con genéricos societarios, apellido compartido.
CATALOGO_FAKE = {
    "ids": {"805", "1230", "77"},
    "nombres": {
        "juan perez": "805",
        "maria gutierrez": "1230",
        "cooperativa agricola ganadera de armstrong": "77",
        "carlos gutierrez": "9",   # apellido compartido con maria
    },
    "tokens": {
        "perez": "805",
        "gutierrez": "",           # ambiguo: maria y carlos
        "armstrong": "77",
    },
    "documentos": {"20123456789", "27998877665"},
}


@pytest.fixture(autouse=True)
def catalogo_fake(monkeypatch):
    monkeypatch.setattr(pg, "_catalogo", lambda: CATALOGO_FAKE)


# ── NO-LEAK: la garantía del sistema ─────────────────────────────────────────

@pytest.mark.parametrize("texto", [
    "¿Cómo viene la cartera de Juan Perez este mes?",
    "como esta JUAN PEREZ",                      # mayúsculas
    "qué onda juan pérez",                       # minúsculas + acento
    "el rendimiento de Pérez",                   # apellido solo, con acento
    "J. Perez pidió el resumen",                 # inicial + apellido
    "che, María Gutiérrez sigue operando?",      # otro cliente, acentos
    "la cuenta 805 cómo está",                   # número con keyword
    "805 tuvo movimientos?",                     # id pelado del catálogo
    "el CUIT 20-1234567-8 es de quién",          # CUIT con guiones
    "documento 20123456789",                     # doc pelado
    "el doc 20.123.456 figura?",                 # doc con puntos (catálogo)
])
def test_no_leak_ninguna_identidad_sobrevive(texto):
    limpio, _m = pg.tokenize(texto)
    bajo = pg._norm(limpio)
    for palabra in ("juan", "perez", "maria", "gutierrez", "805",
                    "20123456789", "20-1234567-8", "20.123.456"):
        assert palabra not in bajo, f"LEAK: {palabra!r} sobrevivió en {limpio!r}"


def test_no_leak_nombre_completo_multipalabra():
    limpio, _m = pg.tokenize("hablé con Cooperativa Agricola Ganadera de Armstrong ayer")
    assert "armstrong" not in pg._norm(limpio)
    assert "CLIENTE_" in limpio


# ── round-trip ───────────────────────────────────────────────────────────────

def test_round_trip_restaura_el_original():
    original = "¿Cómo viene Juan Perez y la cuenta 805?"
    limpio, mapping = pg.tokenize(original)
    assert "Juan" not in limpio and "805" not in limpio
    assert pg.detokenize(limpio, mapping) == original


def test_detokenize_ficha_desconocida_queda_tal_cual():
    assert pg.detokenize("hola CLIENTE_99", {"fichas": {}}) == "hola CLIENTE_99"


# ── estabilidad de fichas ────────────────────────────────────────────────────

def test_mismo_nombre_misma_ficha_entre_turnos():
    limpio1, mapping = pg.tokenize("algo de Juan Perez")
    limpio2, mapping = pg.tokenize("de nuevo JUAN PÉREZ por favor", mapping)
    fichas1 = [f for f in mapping["fichas"] if f in limpio1]
    fichas2 = [f for f in mapping["fichas"] if f in limpio2]
    assert fichas1 and fichas1 == fichas2  # misma ficha en los dos turnos


def test_clientes_distintos_fichas_distintas():
    limpio, _m = pg.tokenize("comparame Juan Perez contra Maria Gutierrez")
    assert "CLIENTE_1" in limpio and "CLIENTE_2" in limpio


# ── enmascarado defensivo (capa 3) ───────────────────────────────────────────

def test_defensivo_nombre_no_catalogado_se_tacha_igual():
    limpio, _m = pg.tokenize("me preguntó Roberto Fernandez por bonos")
    assert "Roberto" not in limpio and "Fernandez" not in limpio
    assert "CLIENTE_" in limpio


def test_defensivo_con_conector():
    limpio, _m = pg.tokenize("la cuenta de Juan de Souza")
    assert "Souza" not in limpio


def test_whitelist_dominio_no_se_tacha():
    limpio, _m = pg.tokenize("cómo está la Renta Fija hoy en Buenos Aires")
    assert "Renta Fija" in limpio and "Buenos Aires" in limpio


def test_tickers_no_se_tachan():
    limpio, _m = pg.tokenize("precio de AL30 y GGAL en el Merval")
    assert "AL30" in limpio and "GGAL" in limpio


# ── números ──────────────────────────────────────────────────────────────────

def test_cuenta_con_keyword_sin_catalogo(monkeypatch):
    monkeypatch.setattr(pg, "_catalogo", lambda: None)
    limpio, _m = pg.tokenize("mirá la cuenta 12345")
    assert "12345" not in limpio and "CTA_" in limpio


def test_numeros_cortos_no_identificatorios_pasan():
    limpio, _m = pg.tokenize("subió 12% y cerró en 1450 puntos")
    assert "12%" in limpio and "1450" in limpio


# ── fuzzy (umbral CALIBRABLE — REGLA #2) ─────────────────────────────────────

def test_fuzzy_typo_de_apellido(monkeypatch):
    monkeypatch.setenv("ASISTENTE_FUZZY_UMBRAL", "0.80")
    limpio, _m = pg.tokenize("qué hizo Peres este mes")  # typo de Perez
    assert "Peres" not in limpio


def test_fuzzy_umbral_alto_no_tacha_palabras_lejanas(monkeypatch):
    monkeypatch.setenv("ASISTENTE_FUZZY_UMBRAL", "0.99")
    limpio, _m = pg.tokenize("los papeles subieron fuerte")
    assert "papeles" in limpio and "subieron" in limpio


# ── resolución ficha → cuenta (dentro del perímetro) ─────────────────────────

def test_ficha_cuenta_resuelve_a_id():
    _limpio, mapping = pg.tokenize("cuenta 805 por favor")
    ficha = next(f for f in mapping["fichas"] if f.startswith("CTA_"))
    assert pg.id_cuenta_de_ficha(ficha, mapping) == "805"


def test_ficha_cliente_resuelve_por_nombre():
    _limpio, mapping = pg.tokenize("cartera de Juan Perez")
    ficha = next(f for f in mapping["fichas"] if f.startswith("CLIENTE_"))
    assert pg.id_cuenta_de_ficha(ficha, mapping) == "805"


def test_apellido_ambiguo_no_resuelve():
    _limpio, mapping = pg.tokenize("qué hizo Gutierrez")
    fichas = [f for f in mapping["fichas"] if f.startswith("CLIENTE_")]
    assert fichas and pg.id_cuenta_de_ficha(fichas[0], mapping) is None


def test_ficha_inexistente_no_resuelve():
    assert pg.id_cuenta_de_ficha("CLIENTE_9", {"fichas": {}}) is None


# ── fail-closed ──────────────────────────────────────────────────────────────

def test_sin_catalogo_disponible_lo_reporta(monkeypatch):
    monkeypatch.setattr(pg, "_catalogo", lambda: None)
    assert pg.catalogo_disponible() is False


def test_fallo_interno_retiene_el_texto(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(pg, "_spans_numeros", _boom)
    limpio, _m = pg.tokenize("texto con Juan Perez")
    assert "Juan" not in limpio  # ante error interno NADA pasa sin tachar
    assert "RETENIDO" in limpio


def test_texto_vacio():
    limpio, mapping = pg.tokenize("")
    assert limpio == "" and mapping["fichas"] == {}
