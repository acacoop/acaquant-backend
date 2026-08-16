"""Tests del canal de PREGUNTAS del AV Agent (E1.c) — docs/AV_AGENT.md.

Se testea lo PURO: generar preguntas desde hallazgos y parsear el comando de
respuesta. Lo que toca la base (registrar / responder / efecto) vive detrás de
`get_pool` y se prueba en integración.

**El parser es lo más peligroso del módulo.** Un error ahí no rompe nada
visiblemente: guarda la respuesta equivocada en la pregunta equivocada, y el
efecto de `ignorar` es silencioso y permanente — un bono que querías dar de alta
deja de proponerse para siempre y nadie se entera. Por eso prefiere GRITAR
(`ValueError`) antes que interpretar.
"""
from __future__ import annotations

import pytest

from api.services import av_agent_preguntas as preg


def _falta(ticker: str, curva: str = "Soberanos ARS CER", vto: str | None = None,
           **ev) -> dict:
    return {"tipo": "falta_en_base", "ticker": ticker, "regla": "no_esta_en_curvas",
            "severidad": "media", "motivo": "…",
            "evidencia": {"curva_1816": curva, "vencimiento_1816": vto, **ev}}


# ── generación ───────────────────────────────────────────────────────────────


def test_un_faltante_genera_una_pregunta_que_se_entiende_sola():
    """Se lee semanas después, sin el hilo que la originó: tiene que decir el
    ticker, de dónde salió y qué se puede contestar."""
    p = preg.preguntas_de_hallazgos([_falta("TZXD8", vto="2028-12-15")])[0]
    assert p["clave"] == "falta:TZXD8"
    assert "TZXD8" in p["pregunta"] and "Soberanos ARS CER" in p["pregunta"]
    assert "2028-12-15" in p["pregunta"]
    assert p["opciones"] == list(preg.RESPUESTAS_FALTA)


def test_la_pregunta_trae_EMISOR_y_denominacion():
    """Un ticker solo no es una pregunta contestable: `M31G6` no le dice nada a
    nadie. El emisor y la denominación vienen del catálogo de 1816, que el censo
    ya trae en el mismo crédito."""
    p = preg.preguntas_de_hallazgos([_falta(
        "M31G6", curva="Soberanos ARS Tamar", vto="2026-08-31",
        emisor="Tesoro Nacional", denominacion="BONO TESORO TAMAR AGO-26",
        moneda="ARS")])[0]
    assert "Tesoro Nacional" in p["pregunta"]
    assert "BONO TESORO TAMAR AGO-26" in p["pregunta"]
    assert "ARS" in p["pregunta"] and "2026-08-31" in p["pregunta"]


def test_si_la_casa_LO_TIENE_la_pregunta_lo_dice_primero():
    """Un bono en la tenencia que no está en mercado.curvas NO VALÚA. Ahí la
    respuesta deja de ser una preferencia y pasa a ser un arreglo pendiente, así
    que el aviso va ANTES que cualquier otro dato."""
    p = preg.preguntas_de_hallazgos([_falta("TB27", en_cartera=True,
                                            emisor="Tesoro Nacional")])[0]
    assert p["pregunta"].index("CARTERA") < p["pregunta"].index("Tesoro Nacional")


def test_la_denominacion_no_se_repite_cuando_ES_el_ticker():
    """1816 a veces manda la denominación igual al ticker. Repetirlo es ruido en
    una lista de 24 preguntas."""
    p = preg.preguntas_de_hallazgos([_falta("GD46", denominacion="GD46")])[0]
    assert p["pregunta"].count("GD46") == 1


def test_sin_emisor_la_pregunta_igual_se_entiende():
    """1816 puede no traer el emisor de un instrumento. Se degrada mostrando lo
    que sí hay — nunca un «None» en pantalla."""
    p = preg.preguntas_de_hallazgos([_falta("XXX9")])[0]
    assert "None" not in p["pregunta"]
    assert "XXX9" in p["pregunta"] and "Soberanos ARS CER" in p["pregunta"]


def test_NO_pregunta_por_las_tasas_rotas():
    """*"¿Por qué este bono tiene paridad 150.000%?"* no es una pregunta para el
    user: es el trabajo del agente (E4). Mandársela sería delegarle justo el
    laburo que el agente vino a hacer."""
    tasas = [{"tipo": "tasa_sospechosa", "ticker": "YMCTO", "regla": "paridad_fuera_de_rango",
              "severidad": "alta", "motivo": "…", "evidencia": {}},
             {"tipo": "sin_flujo", "ticker": "DICP", "regla": "flujos_vacios",
              "severidad": "alta", "motivo": "…", "evidencia": {}}]
    assert preg.preguntas_de_hallazgos(tasas) == []


def test_la_clave_es_estable_para_el_mismo_ticker():
    """Es lo que hace que el agente NO repregunte: la clave es única en la tabla,
    así que la segunda corrida no inserta nada."""
    a = preg.preguntas_de_hallazgos([_falta("CUAP")])[0]
    b = preg.preguntas_de_hallazgos([_falta("CUAP", curva="otra")])[0]
    assert a["clave"] == b["clave"] == "falta:CUAP"


# ── parser del comando ───────────────────────────────────────────────────────


def test_parsea_una_respuesta_simple():
    assert preg.parsear_respuestas("3=alta") == [(3, "alta")]


def test_parsea_lista_y_RANGOS():
    """El rango existe porque la forma real de contestar 24 preguntas es "estas
    dos sí, el resto no". Sin rangos, contestar en la consola web del Droplet es
    tan doloroso que no se contesta (REGLA #0)."""
    assert preg.parsear_respuestas("3=alta, 5-7=ignorar") == [
        (3, "alta"), (5, "ignorar"), (6, "ignorar"), (7, "ignorar")]


def test_normaliza_espacios_y_mayusculas():
    assert preg.parsear_respuestas("  4 = IGNORAR ") == [(4, "ignorar")]


@pytest.mark.parametrize("texto", ["3", "=alta", "3=", "x=alta", "9-5=ignorar",
                                   "3-x=alta"])
def test_una_entrada_ambigua_GRITA_en_vez_de_adivinar(texto):
    """`ignorar` es silencioso y permanente. Ante cualquier duda el parser tiene
    que fallar ruidoso: adivinar acá significa ignorar un bono que querías dar de
    alta, y eso no se descubre nunca."""
    with pytest.raises(ValueError):
        preg.parsear_respuestas(texto)


def test_texto_vacio_no_es_error_es_nada():
    assert preg.parsear_respuestas("") == []
    assert preg.parsear_respuestas("  ,  ") == []


# ── las decisiones de diseño ─────────────────────────────────────────────────


def test_las_decisiones_abiertas_estan_bien_formadas():
    """Viven en código y no solo en el doc para que el agente pueda PREGUNTARLAS:
    una decisión que solo existe en un markdown depende de que alguien lo lea."""
    claves = {d["clave"] for d in preg.DECISIONES_ABIERTAS}
    assert claves == {"decision:alcance", "decision:conflicto", "decision:diagnostico"}
    for d in preg.DECISIONES_ABIERTAS:
        assert "?" in d["pregunta"], d["clave"]
        assert len(d["opciones"]) >= 2, d["clave"]
