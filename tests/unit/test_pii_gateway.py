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


def test_las_fechas_no_se_rompen(monkeypatch):
    """Caso real 2026-07-21: 'al 31/05/2026' salió como 'al CTA_2/05/2026'
    porque 31 era un id de cuenta. Una fecha no identifica a nadie; romperla
    sí arruina la respuesta (el modelo recibe un período corrupto)."""
    cat = {**CATALOGO_FAKE, "ids": {"31", "5", "2026", "1", "805"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    for texto in (
        "aranceles desde 01/06/2025 al 31/05/2026",
        "del 2026-05-31 al 2026-06-30",
        "el mes de junio 2026",
        "desde el 31 de mayo de 2026",
        "entre 05/2026 y 06/2026",
    ):
        limpio, _m = pg.tokenize(texto)
        assert limpio == texto, f"la aduana rompió una fecha: {limpio!r}"


def test_cuenta_pelada_sigue_tachandose_fuera_de_fechas(monkeypatch):
    """El fix de fechas no puede abrir la puerta: un id suelto se sigue tachando."""
    cat = {**CATALOGO_FAKE, "ids": {"31", "805"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    limpio, _m = pg.tokenize("mirá la cuenta 805 y también 31")
    assert "805" not in limpio and "CTA_" in limpio


def test_vocabulario_protegido_no_se_tacha(monkeypatch):
    """Incidente real (2026-07-21): en una tabla de aranceles los TIPOS DE
    OPERACIÓN salieron como CLIENTE_12 porque compartían una palabra con el
    nombre de algún cliente. El caller marca su vocabulario como intocable."""
    cat = {**CATALOGO_FAKE, "tokens": {**CATALOGO_FAKE["tokens"], "compras": "77"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    texto = "Compras A3: 272,8 millones · Compras PPT: 23,3 millones · Juan Perez"
    # En una PREGUNTA del usuario 'Compras' sí se tacharía (está en el índice
    # de tokens y ahí el match por palabra suelta sigue activo). Es justamente
    # la razón por la que el caller declara su vocabulario como protegido.
    sucio, _m = pg.tokenize(texto)
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


# ── texto GENERADO por nuestro código: la aduana no puede romper las etiquetas ─

_CATALOGO_CON_PALABRAS_COMUNES = {
    **CATALOGO_FAKE,
    # tokens REALES del catálogo de producción que son también palabras
    # comunes del castellano — el sondeo 2026-07-22 encontró estos tres
    # rompiendo las etiquetas de las tools.
    "tokens": {**CATALOGO_FAKE["tokens"],
               "control": "11", "entre": "12", "pico": "13"},
}


def test_texto_generado_no_tacha_palabras_sueltas(monkeypatch):
    """CASOS REALES del sondeo: "el permiso de Control Comercial" salía como
    "el permiso de CLIENTE_1", "AuM entre X e Y" perdía el "entre" y "día
    pico" perdía el "pico". Nuestras etiquetas coincidían con un token del
    nombre de algún cliente. En texto que escribió NUESTRO código el match por
    palabra suelta se apaga: las identidades que las tools emiten ya vienen
    fichadas desde adentro del perímetro."""
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    texto = "variación del AuM entre 2026-06-30 y 2026-07-21 · día pico: 2026-08-09"
    limpio, _m = pg.tokenize(texto, texto_generado=True)
    assert "entre" in limpio and "pico" in limpio
    assert "CLIENTE_" not in limpio


def test_en_la_pregunta_del_usuario_el_token_suelto_SIGUE_tachando(monkeypatch):
    """La contracara: lo que escribe el USUARIO es libre (puede tirar un
    apellido solo) → ahí el match por token es imprescindible y no se toca."""
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    limpio, _m = pg.tokenize("cuánto operó Perez")
    assert "Perez" not in limpio and "CLIENTE_1" in limpio


def test_texto_generado_igual_tacha_un_nombre_completo_que_colara(monkeypatch):
    """Lo que NO se debilita: si una tool emitiera un nombre real completo,
    la aduana lo sigue tachando (es la red de seguridad del token-out)."""
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    limpio, _m = pg.tokenize("el mayor tenedor es Juan Perez", texto_generado=True)
    assert "Perez" not in limpio and "CLIENTE_" in limpio


def test_texto_generado_igual_tacha_documentos(monkeypatch):
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    limpio, _m = pg.tokenize("CUIT 20123456789 del titular", texto_generado=True)
    assert "20123456789" not in limpio


def test_etiqueta_del_sistema_protegida_no_se_tacha(monkeypatch):
    """"Control Comercial" es el nombre de un PERMISO, no de una persona: la
    capa defensiva lo veía como un par Capitalizado. Se protege desde el
    módulo dueño (core.roles.LABEL_CONTROL_COMERCIAL), no con un literal."""
    from core.roles import LABEL_CONTROL_COMERCIAL

    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    limpio, _m = pg.tokenize(
        f"ese dato requiere el permiso de {LABEL_CONTROL_COMERCIAL}",
        texto_generado=True, protegidos=(LABEL_CONTROL_COMERCIAL,))
    assert LABEL_CONTROL_COMERCIAL in limpio and "CLIENTE_" not in limpio


# ── Leak del historial re-inyectado (hallazgo del security review 2026-07-23) ──

def test_identidad_conocida_no_escapa_en_texto_generado(monkeypatch):
    """EL LEAK: un apellido suelto entra en el turno 1 (detector de palabra
    suelta ON), se ficha, y la respuesta se persiste con el nombre REAL. Al
    re-inyectar ese turno como historial (texto_generado=True), el detector
    suelto está apagado (v1.94) y el apellido volvía a viajar al proveedor.
    La red final lo re-enmascara porque YA es una identidad conocida del chat."""
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    # turno 1: el usuario nombra a un cliente por su apellido suelto
    _limpio1, mapping = pg.tokenize("cómo viene Perez")
    ficha = next(f for f in mapping["fichas"] if f.startswith("CLIENTE_"))
    assert mapping["fichas"][ficha]  # quedó fichado

    # turno 2: se re-inyecta la respuesta previa (texto NUESTRO) con el nombre
    # real — el caso que filtraba
    limpio, _m = pg.tokenize("El mayor tenedor es Perez este mes.", mapping,
                             texto_generado=True)
    assert "Perez" not in limpio            # ya NO escapa
    assert ficha in limpio                  # y sale con SU misma ficha


def test_la_red_final_no_pisa_una_ficha_ya_puesta(monkeypatch):
    """La red no debe tocar el interior de una ficha ya colocada."""
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    _l, mapping = pg.tokenize("cómo viene Perez")
    limpio, _m = pg.tokenize("CLIENTE_1 operó mucho", mapping, texto_generado=True)
    assert "CLIENTE_1" in limpio and "CLIENTE_CLIENTE" not in limpio


def test_la_red_final_no_reintroduce_deformacion_v194(monkeypatch):
    """La red SOLO toca valores que este chat fichó. Una etiqueta del sistema
    ('entre', 'pico') que coincide con un token del catálogo global pero NUNCA
    fue una identidad del chat sigue intacta — no se re-rompe lo de v1.94."""
    monkeypatch.setattr(pg, "_catalogo", lambda: _CATALOGO_CON_PALABRAS_COMUNES)
    mapping = pg._mapping_nuevo()   # chat SIN identidades fichadas
    limpio, _m = pg.tokenize("AuM entre junio y julio · día pico 2026-08-09",
                             mapping, texto_generado=True)
    assert "entre" in limpio and "pico" in limpio and "CLIENTE_" not in limpio


def test_valor_conocido_corto_no_masca_subcadenas(monkeypatch):
    """Boundary check: un valor conocido no debe tacharse dentro de otra
    palabra (word-boundary)."""
    cat = {**_CATALOGO_CON_PALABRAS_COMUNES,
           "tokens": {**_CATALOGO_CON_PALABRAS_COMUNES["tokens"], "sur": "50"}}
    monkeypatch.setattr(pg, "_catalogo", lambda: cat)
    _l, mapping = pg.tokenize("cliente Sur")     # ficha 'sur'
    limpio, _m = pg.tokenize("el mercado del Sur y la sursala", mapping,
                             texto_generado=True)
    assert "sursala" in limpio                   # subcadena intacta
