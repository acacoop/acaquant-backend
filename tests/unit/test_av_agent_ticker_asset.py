"""La acción que corrige el TICKER mal tipeado (§0.ca).

Los datos de los dos casos son los REALES, medidos en prod el 2026-08-22 con
`scripts/diag_espejo_assets`. Un test con datos inventados habría pasado igual
con el bug que este archivo nació para cazar.
"""
import pytest

from api.services.av_agent_hacer import ACCIONES, _elegir_ticker

# Lo que devolvió el diag, tal cual.
PLC5O = "[84857] PLC5O - ON PLUSPETROL S. A. Clase V 18/05/31"
S13N6 = "[9416] S13N6"
CURVAS = {"PLC5O", "S13N6", "AL30", "GD30"}


class TestLasTresGuardas:
    def test_los_dos_casos_reales_se_proponen(self):
        r = _elegir_ticker([(PLC5O, "PLC50"), (S13N6, "S13B6")], CURVAS)
        assert r == [(PLC5O, "PLC50", "PLC5O"), (S13N6, "S13B6", "S13N6")]

    def test_lo_que_ya_coincide_no_se_toca(self):
        assert _elegir_ticker([(PLC5O, "PLC5O")], CURVAS) == []

    def test_guarda_1_si_la_unidad_no_es_una_curva_no_se_propone(self):
        """Sin curva no sabemos cuál de los dos nombres es el bueno."""
        assert _elegir_ticker([("[123] NOEXISTE", "OTRO")], CURVAS) == []

    def test_guarda_2_no_se_pisa_el_ticker_de_OTRO_papel_real(self):
        """⚠️ La guarda más importante: si el ticker actual es una curva de
        verdad, corregirlo acá le rompe el join a ESE bono."""
        assert _elegir_ticker([(PLC5O, "AL30")], CURVAS) == []

    def test_guarda_3_dos_fichas_para_el_mismo_codigo_no_se_deciden_solas(self):
        r = _elegir_ticker(
            [("[84857] PLC5O - ON PLUSPETROL", "PLC50"),
             ("[99999] PLC5O - otra cosa", "PLC50")], CURVAS)
        assert r == []

    def test_el_ticker_vacio_tambien_se_completa(self):
        """`sin_ticker` (falta el campo) usa el mismo camino que `otro_ticker`:
        el valor sale de la unidad en los dos casos."""
        assert _elegir_ticker([(S13N6, "")], CURVAS) == [(S13N6, "", "S13N6")]

    def test_no_levanta_con_una_unidad_rara(self):
        assert _elegir_ticker([("", "X"), ("   ", "Y")], CURVAS) == []


class TestElSujetoLlegaDeDosFormas:
    """⚠️ El bug que este archivo cazó ANTES de que el user apretara el botón.

    El control `assets_ticker_partido` emite la UNIDAD como sujeto; la fila de
    ENCONTRÓ habla del TICKER del bono. La primera versión de `proponer()`
    asumía la segunda, así que por el camino real proponía **cero** — sin error
    y sin log. Un botón que aparece, no hace nada y no explica por qué es la
    misma pared que esta acción vino a sacar.
    """

    def test_el_sql_matchea_por_unidad_Y_por_codigo(self):
        import inspect
        src = inspect.getsource(ACCIONES["assets.ticker"].proponer)
        assert "upper(unidad) = ANY" in src, "tiene que aceptar la UNIDAD"
        assert "substring(unidad" in src, "y también el CÓDIGO de la unidad"

    def test_sin_casos_no_pega_a_la_base(self):
        assert ACCIONES["assets.ticker"].proponer([]) == []


class TestElContrato:
    def test_esta_registrada_y_cuelga_de_un_control_real(self):
        from jobs.controles_datos import CONTROLES
        a = ACCIONES["assets.ticker"]
        assert a.sobre in {c.id for c in CONTROLES}

    def test_el_control_NO_reimplementa_el_predicado(self):
        """Sale de `core.duplicados`, donde el par ya está declarado con su
        árbitro. Escribirlo dos veces es el problema que ese módulo evita."""
        import inspect

        from jobs.controles_datos import _chk_assets_ticker_partido
        src = inspect.getsource(_chk_assets_ticker_partido)
        assert "duplicados" in src
        assert "SELECT" not in src.upper().replace("SELECT UNO", ""), (
            "el control no puede tener su propia query")

    def test_el_duplicado_esta_declarado_sin_arreglo_automatico(self):
        from core.duplicados import DUPLICADOS
        d = next(x for x in DUPLICADOS if x.id == "ticker_curva_vs_assets")
        assert not d.arreglo_sql, (
            "el UPDATE «obvio» es peligroso por la guarda 2 — va por la acción")
        assert d.arreglo_manual

    def test_no_se_escribe_un_ticker_que_no_salga_de_la_unidad(self):
        """Las guardas se vuelven a correr AL APLICAR: entre proponer y aplicar
        pueden pasar horas y alguien pudo tocar el catálogo a mano."""
        from api.services.av_agent_hacer import Propuesta
        p = Propuesta(sujeto="[9416] S13N6", campo="TICKER", propuesto="OTRO",
                      antes="S13B6", porque="")
        with pytest.raises(ValueError, match="no dice"):
            ACCIONES["assets.ticker"].aplicar(p)


class TestElRegexNoSePuedeSEPARAR:
    """⚠️ El patrón que saca el código de la unidad vive en **tres** lugares:
    `acreencias._RE_CODIGO` (Python), el SQL de `core.duplicados` y el SQL de
    `proponer()`. No se pueden unificar —uno corre en Python y los otros dentro
    de Postgres— así que lo único que queda es exigir que digan LO MISMO.

    Es REGLA #9(B) en chico: tres copias de una regla, y si una se separa el
    control marca un caso que la acción no propone (o al revés) **sin fallar**.
    """

    PATRON = r"^\s*(?:\[\d+\]\s*)?([A-Za-z0-9]+)"

    def test_las_tres_copias_dicen_lo_mismo(self):
        import inspect

        from api.services import acreencias
        from api.services.av_agent_hacer import ACCIONES
        from core.duplicados import DUPLICADOS

        assert acreencias._RE_CODIGO.pattern == self.PATRON
        dup = next(d for d in DUPLICADOS if d.id == "ticker_curva_vs_assets")
        assert self.PATRON in dup.sql, "el SQL del duplicado se separó"
        src = inspect.getsource(ACCIONES["assets.ticker"].proponer)
        assert self.PATRON in src, "el SQL de la acción se separó"

    def test_el_patron_saca_el_codigo_de_las_dos_formas_reales(self):
        from api.services.acreencias import codigo_de_unidad
        assert codigo_de_unidad(PLC5O) == "PLC5O"   # '[id] CODE - descripción'
        assert codigo_de_unidad(S13N6) == "S13N6"   # '[id] CODE' pelado


def test_toda_accion_declara_su_riesgo():
    """El script de aplicación masiva imprime, por acción, si ESCRIBE PLATA.

    Se DECLARA y no se infiere del nombre: adivinar acá significaría soltar 133
    escrituras creyendo que no se toca ninguna valuación. Una acción nueva sin
    riesgo declarado sale como «SIN DECLARAR» en pantalla — este test la caza
    antes, que es cuando todavía es barato.
    """
    from api.services.av_agent_hacer import ACCIONES
    from scripts.agente_aplicar import _RIESGO
    faltan = set(ACCIONES) - set(_RIESGO)
    assert not faltan, f"acciones sin riesgo declarado: {faltan}"
