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
    diagnóstico terminan contradiciéndose.

    Se mira el registro `PUERTAS` y no el `if/elif` que había adentro de
    `_diagnosticar_uno`: ese `if/elif` ERA el problema (§0.cb) — una cuarta copia
    de la tabla de ruteo, con 4 modos de 8, que declaraba «sin puerta» a 13
    hallazgos que sí la tenían.
    """
    # Las cuatro originales siguen cubiertas…
    src_todas = "\n".join(inspect.getsource(f) for f in m.PUERTAS.values())
    for puerta in ("simular_arreglo", "simular_flujos", "simular(",
                   "av_agent_salud.diagnosticar"):
        assert puerta in src_todas, f"se perdió la puerta «{puerta}»"

    # …y NINGUNA calcula por su cuenta: cada una delega en un service. Una
    # puerta con lógica propia es la segunda implementación que este test evita.
    for modo, fn in m.PUERTAS.items():
        cuerpo = inspect.getsource(fn)
        assert "from api.services import" in cuerpo, f"«{modo}» no delega"
        assert cuerpo.count("return") == 1, (
            f"«{modo}» tiene más de una salida: eso ya es lógica, no ruteo")


def test_la_lente_con_IA_va_apagada_en_masivo():
    """68 lecturas de log con LLM en una corrida es gasto que nadie pidió — y el
    análisis determinista sale igual.

    ⚠️⚠️ **ESTE TEST EXIGÍA EL BUG.** Antes decía `assert "con_ia=False" in src`,
    o sea que congelaba la INTENCIÓN mirando un STRING. Cuando la lente con IA se
    dio de baja y el parámetro desapareció de la firma, el test siguió pidiendo
    que la llamada lo pasara — y la llamada lo pasaba, contra una función que ya
    no lo aceptaba. **14 casos explotaron con TypeError en el masivo #6 y este
    test estaba en verde**, porque el string seguía ahí.

    La lección, que vale para todo el repo: **un test que verifica un texto puede
    sobrevivir a la cosa que verificaba.** Ahora se chequea lo estructural —que
    la función NO tenga por dónde gastar un token— que es lo que se quería decir
    y no se puede cumplir de mentira."""
    import inspect as _i

    from api.services import av_agent_salud

    # (a) No hay ningún interruptor de IA que prender: no existe el parámetro.
    assert "con_ia" not in _i.signature(av_agent_salud.diagnosticar).parameters
    # (b) Y el diagnóstico no llama al gateway. Si mañana alguien le suma una
    #     lente con LLM, este test lo frena antes de que corra sobre 155 casos.
    src = _i.getsource(av_agent_salud.diagnosticar)
    for gasto in ("core.ai", "generar(", "completar("):
        assert gasto not in src, f"el diagnóstico de SALUD gasta tokens: «{gasto}»"


def test_cada_ACCION_llama_a_su_funcion_con_una_firma_QUE_EXISTE():
    """**14 casos explotaron por esto** (masivo #6): el masivo llamaba a
    `av_agent_salud.diagnosticar(sujeto, con_ia=False)` y ese parámetro se había
    ido al dar de baja la lente con IA. Todos los chequeos de SALUD y todos los
    controles —la categoría entera— morían con TypeError.

    Lo grave no es el argumento de más: es que **la corrida terminaba diciendo
    «terminado»**. El masivo separa bien los que explotan, pero un
    `TypeError` de firma no es un caso raro que analizar, es código roto — y
    ninguna herramienta lo iba a ver, porque ruff no sigue una llamada entre
    módulos y el `except` la convertía en una fila más del informe.

    Este test ata la llamada a la firma real. Si mañana alguien cambia una de las
    dos, falla acá y no en una corrida de 430 segundos y 552 créditos."""
    import inspect

    from api.services import av_agent_alta, av_agent_salud

    # (función, args posicionales, kwargs) — lo mismo que hace `_un_caso`.
    llamadas = [
        (av_agent_salud.diagnosticar, ("job:x",), {}),
        (av_agent_alta.simular_flujos, ("AL30",), {}),
        (av_agent_alta.simular, ("AL30",), {"curva_1816": ""}),
        (av_agent_alta.simular_arreglo, ("AL30",), {}),
    ]
    for fn, args, kw in llamadas:
        # `bind` levanta TypeError si la llamada no encaja — sin ejecutar nada.
        inspect.signature(fn).bind(*args, **kw)
