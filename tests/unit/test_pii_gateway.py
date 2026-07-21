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
    # empleados de la mesa (decisión b 2026-07-21: tampoco salen al proveedor)
    "operadores": {"martin operetti": "Martin Operetti"},
    "operadores_tokens": {"operetti": "Martin Operetti"},
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


# ── calibración 2026-07-21 (incidentes del primer smoke/no-leak en prod) ─────

def test_vocabulario_de_negocio_jamas_se_tacha():
    """Incidente real: '¿cuál es el AuM total administrado hoy?' terminó con
    'total' y 'administrado' tokenizados como clientes (el catálogo tiene
    FCIs con palabras comunes). El vocabulario de negocio no es candidato."""
    texto = "¿Cuál es el AuM total administrado hoy? Dame el resumen de la cartera"
    limpio, _m = pg.tokenize(texto)
    assert limpio == texto  # ni una palabra tachada


def test_sufijo_societario_se_absorbe(monkeypatch):
    """Incidente real del no-leak e2e: 'Empresa SA' dejaba el 'SA' suelto al
    lado de la ficha. El sufijo entra en la tachadura."""
    cat = dict(CATALOGO_FAKE)
    cat = {**cat, "nombres": {**cat["nombres"], "molinos rio sa": "50",
                              "molinos rio": "50"},
           "tokens": {**cat["tokens"], "molinos": "50"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    limpio, _m = pg.tokenize("qué hizo Molinos Rio SA este mes")
    bajo = pg._norm(limpio)
    assert "molinos" not in bajo
    assert not pg.re.search(r"\bsa\b", bajo)  # el sufijo no queda suelto


def test_corte_por_frecuencia_en_catalogo(monkeypatch):
    """Un token repetido en más de N clientes queda fuera del índice (no
    identifica); hasta N con 2+ dueños queda como '' (detecta, no resuelve)."""
    monkeypatch.setenv("ASISTENTE_TOKEN_MAX_CLIENTES", "2")
    filas = [(str(i), f"[{i}] FIDEICOMISO ZUTANO {i}", None) for i in range(4)]
    filas.append(("9", "[9] RARISIMO UNICO", None))
    filas.append(("10", "[10] RARISIMO OTRO", None))

    class _Cur:
        def __init__(self):
            self._sql = ""

        def execute(self, sql, *a):
            self._sql = sql

        def fetchall(self):
            # la 1ra query del catálogo es la de operadores (2 columnas)
            return [] if "operadores" in self._sql else filas

        def __enter__(self):
            return self

        def __exit__(self, *a): ...

    class _Conn(_Cur):
        def cursor(self):
            return _Cur()

    class _Pool:
        def connection(self):
            return _Conn()

    import core.postgres
    monkeypatch.setattr(core.postgres, "get_pool", lambda: _Pool())
    cat = pg._leer_catalogo_sql()
    assert "zutano" not in cat["tokens"]        # 4 clientes > corte 2 → fuera
    assert cat["tokens"].get("rarisimo") == ""  # 2 dueños ≤ corte → ambiguo
    assert cat["tokens"].get("unico") == "9"    # único dueño → resuelve


def test_texto_generado_respeta_numeros(monkeypatch):
    """Incidente real (trazas 2026-07-21): con 1836 ids de cuenta, los
    agregados de las tools se hacían trizas — el AuM 605.25 viajó como
    CTA_10.CTA_9 y '893 cuentas' como CTA_8. En texto GENERADO por el código
    los números se respetan; los NOMBRES se tachan igual."""
    cat = {**CATALOGO_FAKE, "ids": {"605", "25", "893", "9", "126"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    crudo = ("AuM total: 605.25 mil millones ARS en 893 cuentas. "
             "COOPERATIVAS: 126.32 mil millones (20.9%). Mayor tenedor: Juan Perez")
    limpio, _m = pg.tokenize(crudo, texto_generado=True)
    assert "605.25" in limpio and "893" in limpio and "126.32" in limpio
    assert "COOPERATIVAS" in limpio and "(20.9%)" in limpio
    assert "Juan" not in limpio and "Perez" not in limpio  # nombres igual se tachan


def test_texto_de_usuario_no_rompe_decimales(monkeypatch):
    """En texto del USUARIO los números pelados siguen tachándose (pueden ser
    cuentas), pero un decimal o un % jamás se parte."""
    cat = {**CATALOGO_FAKE, "ids": {"805", "20", "9"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    limpio, _m = pg.tokenize("subió 20.9% y la cuenta 805 operó")
    assert "20.9%" in limpio          # decimal intacto
    assert "805" not in limpio        # la cuenta pelada SÍ se tacha


def test_vocabulario_protegido_no_se_tacha(monkeypatch):
    """Incidente real (2026-07-21): en una tabla de aranceles los TIPOS DE
    OPERACIÓN salieron como CLIENTE_12 porque compartían una palabra con el
    nombre de algún cliente. El caller marca su vocabulario como intocable."""
    cat = {**CATALOGO_FAKE, "tokens": {**CATALOGO_FAKE["tokens"], "compras": "77"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    texto = "Compras A3: 272,8 millones · Compras PPT: 23,3 millones · Juan Perez"
    # sin protección, 'Compras' se tacharía (está en el índice de tokens)
    sucio, _m = pg.tokenize(texto, texto_generado=True)
    assert "Compras" not in sucio
    # con protección, el vocabulario sobrevive y el NOMBRE se sigue tachando
    limpio, _m2 = pg.tokenize(texto, texto_generado=True,
                              protegidos=["Compras A3", "Compras PPT"])
    assert "Compras A3" in limpio and "Compras PPT" in limpio
    assert "Perez" not in limpio and "CLIENTE_" in limpio


def test_etiquetas_de_segmento_no_son_clientes():
    limpio, _m = pg.tokenize("COOPERATIVAS y PRODUCTORES concentran el AuM")
    assert limpio == "COOPERATIVAS y PRODUCTORES concentran el AuM"


def test_nombre_de_pila_se_absorbe_con_el_apellido():
    """Caso real del primer uso en panel: 'Nicolas Mollo' — 'mollo' matchea
    por catálogo pero 'Nicolas' (fuera del índice por el corte de frecuencia)
    quedaba suelto al lado de la ficha. El nombre de pila precedente entra
    en la tachadura."""
    limpio, _m = pg.tokenize("¿cuánto operó hoy Nicolas Perez?")
    bajo = pg._norm(limpio)
    assert "perez" not in bajo and "nicolas" not in bajo
    assert limpio.count("CLIENTE_") == 1  # UNA ficha, nombre completo


# ── fuzzy (umbral CALIBRADO 2026-07-21: 0.95 medido con el diag) ─────────────

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


# ── operadores (empleados — decisión b 2026-07-21: tampoco salen) ────────────

def test_operador_en_pregunta_se_tacha_como_operador():
    limpio, mapping = pg.tokenize("¿cuánto facturó Martin Operetti este mes?")
    bajo = pg._norm(limpio)
    assert "operetti" not in bajo and "OPERADOR_" in limpio
    ficha = next(f for f in mapping["fichas"] if f.startswith("OPERADOR_"))
    assert pg.operador_de_ficha(ficha, mapping) == "Martin Operetti"


def test_operador_roundtrip():
    original = "los números de Martin Operetti"
    limpio, mapping = pg.tokenize(original)
    assert pg.detokenize(limpio, mapping) == original


def test_asignar_ficha_directa_estable():
    m = pg._mapping_nuevo()
    f1 = pg.asignar_ficha(m, "OPERADOR", "Ana Gomez")
    f2 = pg.asignar_ficha(m, "OPERADOR", "ANA GOMEZ")  # normalizado → misma
    assert f1 == f2 == "OPERADOR_1"
    with pytest.raises(ValueError):
        pg.asignar_ficha(m, "JEFE", "x")


def test_apellido_operador_suelto_resuelve():
    limpio, mapping = pg.tokenize("qué hizo Operetti")
    assert "Operetti" not in limpio
    ficha = next(f for f in mapping["fichas"] if f.startswith("OPERADOR_"))
    assert pg.operador_de_ficha(ficha, mapping) == "Martin Operetti"


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
