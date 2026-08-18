"""El DIAGNÓSTICO MASIVO: el informe y las decisiones que lo hacen usable."""
from __future__ import annotations

import inspect

from api.services import av_agent_masivo as m


def _run(**kw):
    base = {"id": 1, "creado_at": "2026-08-18T03:00:00Z", "estado": "terminado",
            "hechos": 0, "total": 0, "filtro": {}, "sin_red": True,
            "creditos": None, "informe": []}
    return {**base, **kw}


# ── El informe ──────────────────────────────────────────────────────────────

def test_lo_que_exploto_va_ARRIBA_de_lo_que_esta_listo():
    """El orden no es cosmético: una excepción es un bug del agente y un `listo`
    es trabajo hecho. Si los listos van primero, los 3 que explotaron quedan al
    final de un informe de 68 y no los ve nadie."""
    txt = m.informe_texto(_run(informe=[
        {"sujeto": "A", "estado": "listo", "causa": "x"},
        {"sujeto": "B", "estado": "error", "detalle": "KeyError"},
    ]))
    assert txt.index("EXPLOTARON") < txt.index("LISTOS PARA APLICAR")


def test_el_informe_agrupa_por_CAUSA():
    """Es todo el punto: 68 casos que son 4 causas dicen que el trabajo real es
    mucho más chico de lo que parece."""
    txt = m.informe_texto(_run(informe=[
        {"sujeto": "A", "estado": "listo", "causa": "moneda_flujo"},
        {"sujeto": "B", "estado": "listo", "causa": "moneda_flujo"},
        {"sujeto": "C", "estado": "bloqueado", "causa": "sin_ejes"},
    ]))
    assert "POR CAUSA" in txt
    # La causa que más aparece va primero: es la que conviene atacar.
    assert txt.index("moneda_flujo") < txt.index("sin_ejes")


def test_un_estado_nuevo_no_desaparece_del_resumen():
    """Si mañana `_diagnosticar_uno` devuelve un estado que `_QUE_ES` no conoce,
    tiene que salir igual. Un informe que omite en silencio lo que no entiende es
    peor que uno feo."""
    txt = m.informe_texto(_run(informe=[{"sujeto": "A", "estado": "inventado"}]))
    assert "inventado" in txt


def test_el_resumen_se_deriva_y_no_se_persiste():
    """Un resumen guardado se contradice con sus propios casos en cuanto cambia
    la forma de agrupar."""
    r = m._resumen([{"estado": "listo", "causa": "c", "segundos": 1.5},
                    {"estado": "error", "segundos": 0.5}])
    assert r["por_estado"] == {"listo": 1, "error": 1}
    assert r["segundos"] == 2.0
    # La tabla no tiene columna de resumen: el detalle es la única verdad.
    assert "resumen" not in inspect.getsource(m._crear)


# ── Un caso roto no puede tumbar la corrida ─────────────────────────────────

def test_una_excepcion_en_un_caso_se_anota_y_no_levanta():
    """Un informe que se corta en el primer problema no sirve justamente el día
    que hay problemas."""
    fila = m._diagnosticar_uno({"ticker": "X", "accion": "arreglo"}, sin_red=True)
    # Sin base no puede diagnosticar, pero devuelve una fila igual.
    assert fila["sujeto"] == "X"
    assert fila["estado"] in ("error", "no_pudo")


def test_un_hallazgo_sin_puerta_se_informa_y_no_se_omite():
    """«El agente lo ve y todavía no sabe tocarlo» es exactamente la lista de lo
    que falta construir — omitirlo la borraría."""
    fila = m._diagnosticar_uno({"ticker": "X", "accion": ""}, sin_red=True)
    assert fila["estado"] == "sin_puerta"


# ── Los límites ─────────────────────────────────────────────────────────────

def test_sin_casos_no_arranca():
    assert m.arrancar([])["ok"] is False


def test_demasiados_casos_se_rechazan_con_el_motivo_en_TIEMPO():
    """El límite no es de créditos —sobran— sino de reloj: a 1,2s por llamada,
    500 casos son más de 10 minutos."""
    r = m.arrancar([{"ticker": f"T{i}"} for i in range(501)])
    assert r["ok"] is False and "minutos" in r["error"]


def test_no_se_reimplementa_ningun_diagnostico():
    """Cada caso pasa por la MISMA puerta que el modal: dos caminos al mismo
    diagnóstico terminan contradiciéndose."""
    src = inspect.getsource(m._diagnosticar_uno)
    for puerta in ("simular_arreglo", "simular_flujos", "simular(",
                   "av_agent_salud.diagnosticar"):
        assert puerta in src


def test_la_lente_con_IA_va_apagada_en_masivo():
    """68 lecturas de log con LLM en una corrida es gasto que nadie pidió — y el
    análisis determinista sale igual."""
    assert "con_ia=False" in inspect.getsource(m._diagnosticar_uno)
