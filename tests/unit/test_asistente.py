"""El asistente (asistente/, docs/AvAgentAI.md): lo que no se puede romper."""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

RAIZ = Path(__file__).resolve().parents[2]
CUENTAS = ["805", "1346"]


@pytest.fixture
def permiso():
    from asistente import permitido
    from asistente.mundos import cartera as MC

    fake = {"cuentas": [{"id_cuenta": c, "nombre": f"NOMBRE {c}"} for c in CUENTAS],
            "cuantas": len(CUENTAS)}
    with patch.object(permitido, "cuentas", return_value=list(CUENTAS)), \
         patch.object(MC, "cuentas_disponibles", return_value=fake):
        yield


# ── agentes ─────────────────────────────────────────────────────────────────


def test_cada_agente_es_un_objeto_con_su_tarea_declarada_en_el_ruteo():
    from asistente import despacho as DESP
    from asistente import junta as JU
    from asistente.mundos import MUNDOS
    from core import modelos

    for a in (DESP.DESPACHO, JU.JUNTA, *MUNDOS.values()):
        assert a.tarea in modelos.tareas(), f"`{a.nombre}` rutea por una tarea que no existe"
        assert a.instruccion({}).strip()
    assert MUNDOS["cartera"].tarea != MUNDOS["mercado"].tarea, "cada mundo tiene su propio ruteo"
    assert JU.JUNTA.tarea == MUNDOS["cartera"].tarea, "la junta ve datos del negocio"
    assert not DESP.DESPACHO.herramientas and not JU.JUNTA.herramientas


def test_solo_el_mundo_cuenta_ve_datos_del_negocio_y_aprende_el_foco():
    from asistente import despacho as DESP
    from asistente.mundos import MUNDOS
    from core import modelos

    assert modelos.resolver(MUNDOS["cartera"].tarea).datos_negocio
    assert not modelos.resolver(MUNDOS["mercado"].tarea).datos_negocio
    assert not modelos.resolver(DESP.DESPACHO.tarea).datos_negocio
    assert MUNDOS["cartera"].foco == ("cuenta",) and not MUNDOS["mercado"].foco


def test_el_mundo_de_cada_herramienta_se_declara_y_los_dos_se_excluyen():
    from asistente import herramientas as H
    from asistente.mundos import MUNDOS

    assert set(H.TODAS) == {f for a in MUNDOS.values() for f in a.herramientas}
    assert not set(MUNDOS["cartera"].herramientas) & set(MUNDOS["mercado"].herramientas)
    assert len(H.POR_NOMBRE) == len(H.TODAS), "dos herramientas con el mismo nombre"
    for fn in MUNDOS["cartera"].herramientas:
        p = inspect.signature(fn).parameters.get("cuenta")
        assert p is not None and p.default is inspect._empty, f"`{fn.__name__}`: cuenta obligatoria"
    for fn in MUNDOS["mercado"].herramientas:
        assert "cuenta" not in inspect.signature(fn).parameters, (
            f"`{fn.__name__}` es del mercado y recibe `cuenta`: alcance sin permiso")


def test_la_instruccion_de_cuenta_lleva_las_cuentas_y_el_foco_al_final(permiso):
    from asistente.mundos import MUNDOS

    sin = MUNDOS["cartera"].instruccion({})
    con = MUNDOS["cartera"].instruccion({"cuenta": "805"})
    assert "805" in sin and "NOMBRE 805" in sin
    assert con.startswith(sin) and con.rstrip().endswith("cuenta = 805.")
    assert "preguntale cuál quiere" in sin
    assert "cuenta = 805" not in MUNDOS["mercado"].instruccion({"cuenta": "805"})


def test_el_despacho_describe_cada_mundo_por_su_nombre():
    from asistente import despacho as DESP
    from asistente.mundos import MUNDOS

    txt = DESP.DESPACHO.instruccion({})
    for n, a in MUNDOS.items():
        assert f"{n}: {a.describe}" in txt


# ── herramientas ────────────────────────────────────────────────────────────


def test_el_esquema_sale_de_la_firma_con_enum_y_tipos_reales():
    from asistente import herramientas as H
    from asistente.mundos import cartera as MC
    from asistente.mundos import mercado as MM
    from core import curvas_ejes as ce

    props = H.ficha(MM.curva)["function"]["parameters"]["properties"]
    assert props["curva"]["enum"] == list(MM.Curva.__args__)
    assert props["ordenar_por"]["enum"] == list(MM.OrdenCurva.__args__)
    assert props["limit"]["type"] == "integer"
    assert H.ficha(MC.cobros_futuros)["function"]["parameters"]["properties"]["dias"]["type"] == "integer"
    assert H.ficha(MC.cobros_futuros)["function"]["parameters"]["required"] == ["cuenta"]
    assert set(MM.Curva.__args__) == set(ce.PILLS)


def test_cada_ficha_entra_en_su_techo():
    from asistente import herramientas as H

    for fn in H.TODAS:
        assert H.peso_ficha(fn) <= H.MAX_FICHA_CHARS, f"`{fn.__name__}` pasa el techo"


def test_un_docstring_dice_que_hace_y_nunca_como_contestar():
    from asistente import herramientas as H

    prohibidas = ("al contestar", "aclarás siempre", "decila siempre", "decilo siempre")
    for fn in H.TODAS:
        doc = (fn.__doc__ or "").lower()
        assert doc.strip(), f"`{fn.__name__}` sin docstring: el modelo no sabría para qué sirve"
        for frase in prohibidas:
            assert frase not in doc, f"`{fn.__name__}` dice cómo contestar"


def test_lo_que_empieza_con_guion_bajo_no_viaja_al_modelo():
    from asistente import herramientas as H

    r = {"total": 1, "posiciones": [{"a": 1}], "_tabla": {"campo": "posiciones"}}
    assert H.para_el_modelo(r) == {"total": 1, "posiciones": [{"a": 1}]}
    assert H.para_el_modelo("texto") == "texto"


def test_toda_consulta_a_datos_de_cuentas_lleva_el_permiso():
    from asistente import permitido

    fuente = (RAIZ / "asistente" / "mundos" / "cartera.py").read_text(encoding="utf-8")
    consultas = re.findall(r'sql_\w+ = f"""(.*?)"""', fuente, re.S)
    assert consultas
    for sql in consultas:
        assert "{permitido.FILTRO_SQL}" in sql, "una consulta de cuentas sin el permiso"
    assert permitido.FILTRO_SQL.startswith("t.id_cuenta = ANY(")


def test_sin_cuentas_habilitadas_no_se_muestra_nada():
    from asistente import permitido
    from asistente.mundos import cartera as MC

    with patch.object(permitido, "cuentas", return_value=[]):
        for fn, args in ((MC.cobros_futuros, {"cuenta": "805"}),
                         (MC.tenencia_actual, {"cuenta": "805"})):
            r = fn(**args)
            assert "error" in r and "habilitada" in r["error"]


def test_una_cuenta_no_habilitada_vuelve_como_error(permiso):
    from asistente.mundos import MUNDOS

    for fn in MUNDOS["cartera"].herramientas:
        r = fn(cuenta="999")
        assert "error" in r and "preguntale" in r["que_hacer"].lower()


def test_el_total_de_cobros_no_sale_de_la_lista_recortada(permiso):
    from asistente.mundos import cartera as MC

    filas = [(f"2026-10-{d:02d}", "AL30", "USD", 10.0, "Tesoro") for d in range(1, 29)] * 20
    cur = MagicMock()
    cur.fetchall.side_effect = [[("USD", 5600.0)], [("2026-10", "USD", 5600.0)],
                                [("AL30", "USD", 5600.0, "Tesoro", None)], filas]
    cur.fetchone.return_value = ("2026-09-12", "MOLLO")
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    with patch.object(MC, "get_pool", return_value=pool):
        r = MC.cobros_futuros("805", dias=60)
    assert r["truncado"] and len(r["pagos"]) == MC.MAX_PAGOS and r["cuantos_pagos"] == len(filas)
    assert r["total"] == {"USD": 5600.0}, "el total sale de su propia consulta"
    assert "SIN MONEDA" not in r["total"]


def test_la_tenencia_sale_del_mismo_codigo_que_la_pantalla(permiso):
    from api.services import valuaciones_sql
    from asistente.mundos import cartera as MC

    fake = {"fecha": "2026-09-12", "total": 100.0,
            "posiciones": [{"ticker": "TX26", "cantidad": 1, "valuacion": 100.0, "share": 1.0}]}
    with patch.object(valuaciones_sql, "posiciones_actuales", return_value=fake) as p:
        r = MC.tenencia_actual("805", horizonte="t0")
    assert p.call_args.kwargs["con_pnl"] is False and p.call_args.kwargs["horizonte"] == "t0"
    assert r["total"] == 100.0 and r["_tabla"]["campo"] == "posiciones"
    for c in r["_tabla"]["columnas"]:
        assert c in r["posiciones"][0] or c == "emisor"
    assert "error" in MC.tenencia_actual("805", horizonte="t9")


def test_la_curva_lee_la_vista_de_curvas_y_ordena_de_verdad():
    from datetime import date

    from api.services import curvas_vista as CV
    from asistente.mundos import mercado as MM

    def bono(i, tea, pill="cer", ruido=False):
        return {"ticker_corto": f"T{i}", "pill": pill, "emisor": "Tesoro", "vencimiento": "2027-01-01",
                "tasa_ruido": ruido,
                "metrics": {"last_price": 100.0 + i, "TEA": tea, "TEM": 0.0254,
                            "paridad": 98.7, "duration": 1.234 + i, "total_nominals": 1000 * i}}

    vista = {"bonos": [bono(i, 0.10 + i / 100) for i in range(60)]
             + [bono(99, 9.99, ruido=True), bono(98, None), bono(97, 0.5, pill="tasa_fija")]}
    with patch.object(CV, "get_curvas_vista", return_value=vista), \
         patch("asistente.mundos.mercado.date", wraps=date) as d:
        d.today.return_value = date(2026, 1, 1)
        r = MM.curva("cer", ordenar_por="tea", limit=10)
        i0 = r["instrumentos"][0]
        assert r["cuantos"] == 62 and r["truncado"] and len(r["instrumentos"]) == 10
        assert i0["ticker"] == "T59" and i0["tea_pct"] == 69.0, "el que más rinde primero"
        assert (i0["tem_pct"], i0["paridad_pct"], i0["duration"], i0["precio"]) == (2.54, 98.7, 60.23, 159.0)
        assert i0["meses_al_vencimiento"] == 12.0
        assert all(t["ticker"] != "T97" for t in r["instrumentos"])
        with patch.object(MM, "MAX_INSTRUMENTOS", 100):
            todo = MM.curva("cer", limit=100)["instrumentos"]
        assert [t["ticker"] for t in todo[-2:]] == ["T99", "T98"], "ruido y sin tasa van últimas"
        assert MM.curva("cer", ordenar_por="volumen_dia", limit=1)["instrumentos"][0]["ticker"] == "T99"
        assert MM.curva("cer", ordenar_por="duration", limit=1)["instrumentos"][0]["ticker"] == "T0"
        assert len(MM.curva("cer", limit=10_000)["instrumentos"]) == MM.MAX_INSTRUMENTOS
        for c in r["_tabla"]["columnas"]:
            assert c in i0
    assert "error" in MM.curva("bonos")


def test_la_ficha_de_un_bono_no_adivina_otro_ticker_y_usa_la_pata_principal():
    from api.services import bono_detalle as BD
    from asistente.mundos import mercado as MM

    with patch.object(BD, "get_bono", return_value={"error": "XX no está en el master"}):
        r = MM.ficha_bono("xx")
    assert "error" in r and "preguntale" in r["que_hacer"].lower()
    assert "error" in MM.ficha_bono("")

    flujos = [{"fecha": f"2026-{m:02d}-15", "interes": 1.0, "amortizacion": 0.0, "monto": 1.0,
               "futuro": m >= 10} for m in range(1, 13)]
    flujos += [{"fecha": f"20{a}-{m:02d}-15", "interes": 1.0, "amortizacion": 0.0, "monto": 1.0,
                "futuro": True} for a in (27, 28) for m in range(1, 13)]
    ficha = {"ticker": "AL30",
             "ficha": {"emisor": "Tesoro", "moneda": "USD", "fecha_vencimiento": "2030-07-09",
                       "flujo_vencimiento": None},
             "unidad_flujo": "por 100 VN", "nota_flujo": None, "flujos": flujos,
             "pata_principal": "hard_dollar",
             "patas": [{"pata": "cer", "metrics": {"last_price": 1.0, "TEA": 0.9}},
                       {"pata": "hard_dollar",
                        "metrics": {"last_price": 71.5, "TEA": 0.1234, "TEM": 0.0097,
                                    "paridad": 80.2, "duration": 2.345}}]}
    with patch.object(BD, "get_bono", return_value=ficha):
        r = MM.ficha_bono(" al30 ")
    assert r["ticker"] == "AL30" and "flujo_vencimiento" not in r["ficha"]
    assert r["cuantos_pagos"] == 27 and r["truncado"] and len(r["proximos_pagos"]) == MM.MAX_FLUJOS
    assert r["proximos_pagos"][0]["fecha"] == "2026-10-15"
    assert r["hoy"] == {"precio": 71.5, "tea_pct": 12.34, "tem_pct": 0.97,
                        "paridad_pct": 80.2, "duration": 2.35}
    assert "pata_principal" in inspect.getsource(BD.get_bono)


# ── puerta, control, esquema, estado ────────────────────────────────────────


def test_la_puerta_corta_una_cuenta_inventada_y_no_nombra_herramientas(permiso):
    from asistente import herramientas as H
    from asistente import puerta

    assert puerta.revisar("x", {"cuenta": "805"}) is None
    assert puerta.revisar("x", {"curva": "cer"}) is None
    corte = puerta.revisar("x", {"cuenta": "999"})
    assert corte and "999" in corte["error"]
    codigo = (RAIZ / "asistente" / "puerta.py").read_text(encoding="utf-8")
    for nombre in H.POR_NOMBRE:
        assert nombre not in codigo


def test_la_puerta_no_corta_la_conversacion_si_un_control_revienta():
    from asistente import puerta

    def roto(nombre, args):
        raise RuntimeError("x")

    with patch.object(puerta, "CONTROLES", (roto,)):
        assert puerta.revisar("x", {"cuenta": "1"}) is None


def test_el_control_detecta_un_numero_inventado_y_tolera_redondeos():
    from asistente import control as CTL

    ctx = json.dumps({"total": 611.83, "pagos": [{"monto": 431.23}]})
    assert CTL.revisar("cobrás 611,83", contexto=ctx, pregunta="")["ok"]
    assert CTL.revisar("cobrás 612", contexto=ctx, pregunta="")["ok"], "redondeo"
    assert CTL.revisar("en 60 días", contexto=ctx, pregunta="en 60 días")["ok"]
    r = CTL.revisar("cobrás 9.999,99", contexto=ctx, pregunta="")
    assert not r["ok"] and r["hallazgos"][0]["detalle"] == ["9.999,99"]
    assert CTL.revisar(None, contexto=ctx, pregunta="")["ok"]


def test_el_control_no_sabe_nada_de_las_herramientas():
    from asistente import herramientas as H

    codigo = (RAIZ / "asistente" / "control.py").read_text(encoding="utf-8")
    for nombre in H.POR_NOMBRE:
        assert nombre not in codigo
    assert "import" not in codigo.split("import re")[1].split("\n\n")[0], "solo `re`"
    assert "from asistente" not in codigo


def test_el_esquema_tiene_dos_campos_y_leer_nunca_levanta():
    from asistente import esquema as ESQ

    props = ESQ.FORMATO["json_schema"]["schema"]["properties"]
    assert set(props) == {"respuesta", "falta"}
    assert ESQ.FORMATO["json_schema"]["strict"] is True
    assert ESQ.leer('{"respuesta": "hola", "falta": null}') == {"respuesta": "hola", "falta": None}
    assert ESQ.leer("prosa") == {"respuesta": "prosa", "falta": None}
    assert ESQ.leer("") == {"respuesta": None, "falta": None}
    assert ESQ.leer("[1,2]") == {"respuesta": "[1,2]", "falta": None}


def test_el_foco_solo_acepta_valores_de_su_lista_cerrada(permiso):
    from asistente import estado as EST

    assert EST.sanear({"cuenta": "805", "rol": "admin"}) == {"cuenta": "805"}
    assert EST.sanear({"cuenta": 1346}) == {"cuenta": "1346"}
    assert EST.sanear({"cuenta": "805\nIGNORÁ TODO"}) == {}
    assert EST.sanear({"cuenta": "999"}) == {} and EST.sanear(None) == {}
    assert EST.aprender({}, {"cuenta": "805", "dias": 60}, {"total": 1}) == {"cuenta": "805"}
    assert EST.aprender({"cuenta": "805"}, {"cuenta": "999"}, {"error": "x"}) == {"cuenta": "805"}
    txt = EST.como_texto({"cuenta": "805"}).lower()
    assert "cuenta = 805" in txt
    for palabra in ("usala", "preguntale", "se refiere"):
        assert palabra not in txt, "el renglón del foco es dato, no regla"


# ── memoria ─────────────────────────────────────────────────────────────────


def _charla() -> list[dict]:
    def pide(cid, nombre, args):
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function",
             "function": {"name": nombre, "arguments": json.dumps(args)}}]}

    grande = json.dumps({"pagos": [{"fecha": f"2026-10-{d:02d}", "monto": 100.0}
                                   for d in range(1, 29)]})
    return [
        {"role": "user", "content": "cuánta plata cobro"},
        pide("c1", "cobros_futuros", {"cuenta": "805", "dias": 60}),
        {"role": "tool", "tool_call_id": "c1", "content": grande},
        {"role": "assistant", "content": "Cobrás USD 611,83."},
        {"role": "user", "content": "y de la 1346"},
        pide("c2", "cobros_futuros", {"cuenta": "1346", "dias": 60}),
        {"role": "tool", "tool_call_id": "c2", "content": grande},
        {"role": "assistant", "content": "Cobrás USD 3.926,80."},
    ]


def test_se_poda_por_turnos_y_nunca_queda_un_resultado_huerfano():
    from asistente import memoria

    with patch.object(memoria, "TURNOS_QUE_QUEDAN", 3):
        nuevo, turnos, msgs = memoria.podar(_charla() * 6)
    assert (turnos, msgs) == (9, 36) and len(nuevo) == 12 and nuevo[0]["role"] == "user"
    pedidos = {p["id"] for m in nuevo for p in m.get("tool_calls") or []}
    assert all(m["tool_call_id"] in pedidos for m in nuevo if m.get("role") == "tool")
    assert memoria.podar(_charla()) == (_charla(), 0, 0)
    assert memoria.podar([{"role": "assistant", "content": "x"}] + _charla()) == (_charla(), 0, 1)
    with patch.object(memoria, "TURNOS_QUE_QUEDAN", 8), patch.object(memoria, "MENSAJES_QUE_QUEDAN", 9):
        nuevo, turnos, _ = memoria.podar(_charla() * 3)
    assert len(nuevo) == 8 and turnos == 4, "el techo de mensajes tira turnos enteros"


def test_el_achicado_conserva_el_id_y_deja_entero_el_mas_reciente():
    from asistente import memoria

    hist = _charla()
    nuevo, ahorro = memoria.achicar(hist)
    assert [m.get("tool_call_id") for m in nuevo] == [m.get("tool_call_id") for m in hist]
    assert ahorro > 0 and nuevo[2]["content"].startswith("[resultado de cobros_futuros(")
    assert "volvé a llamar" in nuevo[2]["content"]
    assert nuevo[6]["content"] == hist[6]["content"]


def test_los_mensajes_del_proveedor_van_y_vuelven_identicos():
    from langchain_core.messages import AIMessage

    from asistente import memoria

    hist = _charla()
    assert memoria.a_dicts(memoria.desde_dicts(hist)) == hist
    # Un mensaje armado por LangChain (sin `crudo`) exporta también los pedidos
    # con argumentos ilegibles, para que su `tool` no quede huérfano.
    m = AIMessage(content="", tool_calls=[{"name": "curva", "args": {"curva": "cer"}, "id": "a", "type": "tool_call"}],
                  invalid_tool_calls=[{"name": "curva", "args": "{x", "id": "b", "error": "e", "type": "invalid_tool_call"}])
    d = memoria.a_dicts([m])[0]
    assert [c["id"] for c in d["tool_calls"]] == ["a", "b"] and d["tool_calls"][1]["function"]["arguments"] == "{x"
    msgs = memoria.desde_dicts(hist)
    assert msgs[1].tool_calls[0]["args"] == {"cuenta": "805", "dias": 60}
    assert "system" not in {m.type for m in msgs}
    ctx = memoria.contexto(hist, excluir="Cobrás USD 3.926,80.")
    assert "611,83" in ctx and "3.926,80" not in ctx and '"cuenta": "805"' in ctx


# ── modelos (ruteo) y traza ─────────────────────────────────────────────────


def test_completar_lleva_el_detalle_del_que_llama_a_la_traza():
    from langchain_core.messages import AIMessage

    from core import modelos

    visto = {}

    def fake_modelo(tarea, *, usuario=None, sesion=None, esquema=None, traza=None):
        visto["detalle"] = traza.detalle
        traza.ids.append(9)
        m = MagicMock()
        m.invoke.return_value = AIMessage(content="ok")
        return m

    with patch.object(modelos, "modelo", fake_modelo), patch.object(modelos, "ajustes", return_value={}):
        assert modelos.completar_con_traza("agente_emisor", system="s", user="u",
                                           detalle="emisor · 3") == ("ok", 9)
    assert visto["detalle"] == "emisor · 3"


def test_el_ruteo_resuelve_por_tarea_y_una_desconocida_no_corre():
    from core import modelos

    with patch.object(modelos, "ajustes", return_value={}):
        t = modelos.resolver("asistente_cartera")
        assert (t.proveedor, t.datos_negocio, t.usa_herramientas) == ("openai", True, True)
        m = modelos.resolver("asistente_mercado")
        assert (m.proveedor, m.datos_negocio) == (modelos.PROVEEDOR_DEFAULT, False)
        assert not t.elegido
    with patch.object(modelos, "ajustes", return_value={"tarea:asistente_mercado": "openai/gpt-x"}):
        e = modelos.resolver("asistente_mercado")
        assert (e.proveedor, e.modelo, e.elegido) == ("openai", "gpt-x", True)
    with patch.object(modelos, "ajustes", return_value={"tarea:asistente_mercado": "nadie/x"}):
        assert not modelos.resolver("asistente_mercado").elegido, "elección inválida → default"
    with pytest.raises(KeyError):
        modelos.resolver("no_existe")


def test_sin_clave_o_con_ruteo_inseguro_la_llamada_no_sale():
    from core import modelos

    t = modelos.resolver("asistente_cartera")
    with patch.object(modelos, "configurado", return_value=False), pytest.raises(modelos.SinClave):
        modelos.permitido_salir(t)
    inseguro = modelos.Tarea(**{**t.__dict__, "proveedor": "deepseek"})
    with patch.object(modelos, "configurado", return_value=True), \
         patch("config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA", False), pytest.raises(modelos.RuteoInseguro):
        modelos.permitido_salir(inseguro)
    with patch.object(modelos, "configurado", return_value=True), \
         patch("config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA", True):
        modelos.permitido_salir(inseguro)


def test_el_modelo_se_arma_con_el_proveedor_de_langchain_y_el_esquema_solo_si_lo_soporta():
    from langchain_deepseek import ChatDeepSeek
    from langchain_openai import ChatOpenAI

    from core import modelos

    with patch.object(modelos, "clave", return_value="k"):
        o = modelos.armar("openai", "gpt-x", max_tokens=10, timeout_s=5, esquema={"type": "json_schema"})
        d = modelos.armar("deepseek", "ds-x", max_tokens=10, timeout_s=5, esquema={"type": "json_schema"})
    assert isinstance(o, ChatOpenAI) and o.model_kwargs.get("response_format") == {"type": "json_schema"}
    assert o.store is False, "OpenAI no guarda la conversación de su lado"
    assert isinstance(d, ChatDeepSeek) and "response_format" not in d.model_kwargs
    assert o.max_retries == 1 and d.max_retries == 1


def test_la_traza_escribe_una_fila_por_llamada_con_sesion_y_cache():
    from uuid import uuid4

    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    from core import traza as TR

    cur = MagicMock()
    cur.fetchone.return_value = (42,)
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    tr = TR.Traza("asistente_cartera", "gpt-x", usuario="u", sesion="s1")
    assert TR.Traza("t", "m", detalle="emisor · 3").detalle == "emisor · 3", "el que llama manda"
    rid = uuid4()
    msg = AIMessage(content="hola", usage_metadata={"input_tokens": 100, "output_tokens": 5,
                                                    "total_tokens": 105,
                                                    "input_token_details": {"cache_read": 80}})
    with patch("core.postgres.get_pool", return_value=pool):
        tr.on_chat_model_start({}, [[HumanMessage(content="qué hay")]], run_id=rid)
        tr.on_llm_end(LLMResult(generations=[[ChatGeneration(message=msg)]]), run_id=rid)
        tr.on_llm_error(RuntimeError("500"), run_id=uuid4())
    assert tr.ids == [42, 42]
    fila = cur.execute.call_args_list[0].args[1]
    assert fila[0:3] == ("asistente_cartera", "gpt-x", "u") and fila[-1] == "s1"
    assert (fila[3], fila[4], fila[6]) == (100, 5, True) and fila[8] == "qué hay"
    assert (fila[10], fila[11]) == (80, 20), "caché: leído y no leído"
    error = cur.execute.call_args_list[1].args[1]
    assert error[6] is False and "500" in error[7]


# ── grafo ───────────────────────────────────────────────────────────────────


class _Falso:
    """Un ChatModel falso: el despacho contesta `despacho`; un agente con
    herramientas pide la primera con argumentos razonables y después redacta."""

    def __init__(self, despacho: str, tarea: str, esquema: dict | None):
        self.despacho, self.tarea, self.esquema, self.tools = despacho, tarea, esquema, []

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    def invoke(self, mensajes):
        from langchain_core.messages import AIMessage

        ultimo = mensajes[-1]
        uso = lambda i, o: {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}  # noqa: E731
        if self.tarea == "asistente_despacho":
            return AIMessage(content=self.despacho, usage_metadata=uso(50, 3))
        if ultimo.type == "human" and self.tools and not ultimo.content.startswith("Pregunta:"):
            from langchain_core.utils.function_calling import convert_to_openai_tool

            f = convert_to_openai_tool(self.tools[0])["function"]
            args = {"cuenta": "805"} if "cuenta" in f["parameters"]["properties"] else {"curva": "cer"}
            return AIMessage(content="", usage_metadata=uso(900, 20),
                             tool_calls=[{"name": f["name"], "args": args, "id": "c1", "type": "tool_call"}])
        dicho = "TX28 rinde 11.0" if self.tarea == "asistente_mercado" else "total 3926.8"
        return AIMessage(content=json.dumps({"respuesta": f"[{self.tarea}] {dicho}", "falta": None}),
                         usage_metadata=uso(1200, 40))


def _proveedor(despacho: str):
    """Reemplaza `modelos.modelo`: devuelve el ChatModel falso de esa tarea."""
    def fake(tarea, *, usuario=None, sesion=None, esquema=None, traza=None):
        if traza is not None:
            traza.ids.append(1)
        return _Falso(despacho, tarea, esquema)

    return fake


_TOOLS = {
    "cobros_futuros": lambda **a: {"total": {"USD": 3926.8}},
    "tenencia_actual": lambda **a: {"total": 3926.8, "posiciones": [], "_tabla": {"campo": "posiciones"}},
    "curva": lambda **a: {"instrumentos": [{"ticker": "TX28", "tea_pct": 11.0}], "cuantos": 1},
    "ficha_bono": lambda **a: {"ticker": "AL30"},
    "ficha_cliente": lambda **a: {"cuenta": a["cuenta"], "titular": {"denominacion": "NOMBRE 805"}},
}


def _correr(pregunta, despacho="mercado", **kw):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", _proveedor(despacho)), \
         patch.object(grafo, "_ejecutar", lambda ag, n, a: _TOOLS[n](**a) if n in ag.por_nombre
                      else {"error": "no existe"}):
        return grafo.preguntar(pregunta, usuario="t", **kw)


def test_una_pregunta_de_un_mundo_corre_solo_ese_mundo(permiso):
    r = _correr("¿qué es el AL30?", despacho="mercado")
    assert r["mundos"] == ["mercado"] and r["error"] is None
    assert r["respuesta"].startswith("[asistente_mercado]")
    tipos = [(e["tipo"], e.get("agente")) for e in r["eventos"]]
    assert tipos == [("pregunta", None), ("despacho", "despacho"), ("vuelta", "mercado"),
                     ("pide", "mercado"), ("resultado", "mercado"), ("vuelta", "mercado"),
                     ("texto", "mercado")]
    assert [m["role"] for m in r["mensajes"]] == ["user", "assistant", "tool", "assistant"]
    assert r["estado"] == {}, "el mercado no deja nada en foco"
    assert r["vueltas"] == 3 and r["tokens_in"] == 50 + 900 + 1200 and r["llamadas"] == [1, 1, 1]
    assert r["control"]["ok"] is True, "11.0 y TX28 están en los datos del mercado"


def test_el_mundo_cuenta_aprende_el_foco_y_la_sesion_se_conserva(permiso):
    r = _correr("¿cuánto cobro de la 805?", despacho="cartera", sesion="a" * 32)
    assert r["mundos"] == ["cartera"] and r["estado"] == {"cuenta": "805"} and r["sesion"] == "a" * 32
    assert any(e["tipo"] == "estado" and e["estado"] == {"cuenta": "805"} for e in r["eventos"])
    assert r["control"]["ok"], "el total está en los datos"
    r2 = _correr("¿y en dólares?", despacho="cartera", historial=r["mensajes"],
                 estado=r["estado"], sesion=r["sesion"])
    assert r2["sesion"] == r["sesion"] and r2["estado"] == {"cuenta": "805"}
    assert [m["role"] for m in r2["mensajes"]][:4] == ["user", "assistant", "tool", "assistant"]
    assert len(r2["mensajes"]) == 8


def test_una_pregunta_cruzada_corre_los_dos_mundos_y_la_junta_redacta(permiso):
    r = _correr("¿qué bono CER rinde más que los que tengo?", despacho="cartera, mercado")
    assert r["mundos"] == ["cartera", "mercado"] and r["error"] is None
    assert r["respuesta"].startswith("[asistente_cartera]"), "la junta corre con la tarea de cartera"
    agentes = [e.get("agente") for e in r["eventos"] if e["tipo"] != "estado"]
    assert "junta" in agentes and agentes.count("cartera") == agentes.count("mercado")
    roles = [m["role"] for m in r["mensajes"]]
    assert roles == ["user", "assistant", "tool", "assistant", "assistant", "tool", "assistant", "assistant"]
    assert roles.count("user") == 1, "la entrada de la junta no va al historial"
    # 5 y no 6: la pregunta la despachó una regla, sin llamar al modelo.
    assert r["vueltas"] == 5 and r["estado"] == {"cuenta": "805"}
    assert next(e for e in r["eventos"] if e["tipo"] == "despacho")["motivo"].startswith("regla")


def test_cada_mundo_ve_del_historial_solo_lo_suyo(permiso):
    """Lo que trajo el mundo cartera (tenencias, plata) no puede llegar al
    proveedor del mundo mercado en la pregunta siguiente."""
    from asistente import grafo, memoria
    from core import modelos

    r = _correr("¿qué bono CER rinde más que los que tengo?", despacho="cartera, mercado")
    assert {m.get("mundo") for m in r["mensajes"]} == {None, "cartera", "mercado"}
    assert [m["mundo"] for m in r["mensajes"] if m["role"] == "tool"] == ["cartera", "mercado"]
    assert r["mensajes"][-1]["mundo"] == "cartera", "la junta queda como cuenta"

    visto: dict[str, list] = {}
    fake = _proveedor("cuenta, mercado")

    def espia(tarea, **kw):
        m = fake(tarea, **kw)
        original = m.invoke

        def invoke(mensajes):
            visto.setdefault(tarea, []).append(memoria.a_dicts(mensajes))
            return original(mensajes)
        m.invoke = invoke
        return m

    with patch.object(modelos, "modelo", espia), \
         patch.object(grafo, "_ejecutar", lambda ag, n, a: _TOOLS[n](**a)):
        grafo.preguntar("¿y qué bono CER rinde más que lo que tengo a 12 meses?", usuario="t",
                        historial=r["mensajes"], estado=r["estado"], sesion=r["sesion"])
    primera_mercado = visto["asistente_mercado"][0]
    assert "3926.8" not in json.dumps(primera_mercado), "la tenencia viajó al mercado"
    assert all("mundo" not in m for m in primera_mercado), "la marca no viaja al proveedor"
    assert [m["role"] for m in primera_mercado] == ["system", "user", "assistant", "tool",
                                                    "assistant", "user"]
    primera_cuenta = visto["asistente_cartera"][0]
    assert "3926.8" in json.dumps(primera_cuenta) and "TX28" not in json.dumps(primera_cuenta)


def test_si_el_despacho_no_se_entiende_van_todos_los_mundos(permiso):
    from asistente.mundos import MUNDOS

    for raro in ("no sé", "no hace falta la cuenta, alcanza con mercado", ""):
        r = _correr("hola", despacho=raro)
        assert r["mundos"] == list(MUNDOS), raro
        assert "van todos" in next(e for e in r["eventos"] if e["tipo"] == "despacho")["motivo"]
    assert _correr("hola", despacho="Mercado.")["mundos"] == ["mercado"]
    assert _correr("hola", despacho="cartera y mercado")["mundos"] == ["cartera", "mercado"]


def test_al_tope_de_vueltas_ningun_pedido_queda_sin_su_tool(permiso):
    """Si el modelo pide herramientas sin parar, se corta; pero cada pedido
    pendiente se cierra con un `tool` de error: un `assistant` con `tool_calls`
    sin respuesta rompe ese mundo en todas las preguntas siguientes."""
    from langchain_core.messages import AIMessage

    from asistente import grafo
    from core import modelos

    class Insistente(_Falso):
        def invoke(self, mensajes):
            if self.tarea == "asistente_despacho":
                return AIMessage(content="mercado")
            return AIMessage(content="", tool_calls=[
                {"name": "curva", "args": {"curva": "cer"}, "id": f"c{len(mensajes)}", "type": "tool_call"}])

    with patch.object(modelos, "modelo", lambda tarea, **kw: Insistente("mercado", tarea, None)), \
         patch.object(grafo, "_ejecutar", lambda ag, n, a: _TOOLS[n](**a)):
        r = grafo.preguntar("¿qué hay?", usuario="t")
    assert r["error"] and "vueltas" in r["error"]
    pedidos = {tc["id"] for m in r["mensajes"] for tc in m.get("tool_calls") or []}
    respondidos = {m["tool_call_id"] for m in r["mensajes"] if m["role"] == "tool"}
    assert pedidos and pedidos == respondidos, "quedó un pedido sin su tool"
    assert r["mensajes"][-1]["role"] == "tool" and "tope" in r["mensajes"][-1]["content"]


def test_una_herramienta_desconocida_o_un_error_vuelven_como_dato(permiso):
    from asistente import grafo
    from asistente.agente import Agente
    from asistente.mundos import MUNDOS

    assert "error" in grafo._ejecutar(MUNDOS["mercado"], "cobros_futuros", {"cuenta": "805"})
    assert "error" in grafo._ejecutar(MUNDOS["cartera"], "cobros_futuros", None)
    assert "999" in grafo._ejecutar(MUNDOS["cartera"], "cobros_futuros", {"cuenta": "999"})["error"]
    def rota(curva: str) -> dict:
        """Revienta."""
        return 1 / 0

    roto = Agente(nombre="x", tarea=MUNDOS["mercado"].tarea, describe="", instruccion=lambda f: "",
                     herramientas=(rota,))
    assert "ZeroDivisionError" in grafo._ejecutar(roto, "rota", {"curva": "cer"})["error"]


def test_sin_clave_no_rompe_y_deja_la_sesion(permiso):
    from asistente import grafo
    from asistente.mundos import MUNDOS
    from core import modelos

    with patch.object(modelos, "configurado", return_value=False):
        r = grafo.preguntar("hola", usuario="t", sesion="b" * 32)
    assert r["error"] and r["respuesta"] is None and r["sesion"] == "b" * 32
    assert r["mundos"] == list(MUNDOS), "sin despacho, van todos"


def test_la_sesion_se_valida_por_forma():
    from asistente import grafo

    nuevo = grafo.sesion_valida(None)
    assert grafo._SESION_RE.fullmatch(nuevo) and grafo.sesion_valida(nuevo.upper()) == nuevo
    for raro in ("abc", "805; DROP TABLE", nuevo + "x"):
        assert grafo.sesion_valida(raro) != raro


def test_el_router_solo_recibe_pregunta_y_sesion_y_delega_en_sesiones():
    from api.routers import agente as R
    from asistente import sesiones

    with patch.object(sesiones, "preguntar", return_value={"respuesta": "ok"}) as p:
        r = R.lab_preguntar(R.Preguntar(pregunta="x", sesion=""), email="e")
    assert r == {"respuesta": "ok"} and p.call_args.kwargs == {"usuario": "e", "sesion": None}
    assert set(R.Preguntar.model_fields) == {"pregunta", "sesion"}, "la memoria vive en la base"


# ── sesiones ────────────────────────────────────────────────────────────────


class _Base:
    """`ia.conversaciones` en memoria: lo que `sesiones` lee y escribe."""

    def __init__(self):
        self.filas: dict[str, dict] = {}

    def cargar(self, sesion, usuario):
        f = self.filas.get(sesion)
        return (dict(f) if f and f["usuario"] == usuario else None), None

    def guardar(self, sesion, usuario, *, titulo, memoria, foco, turnos):
        self.filas[sesion] = {"sesion": sesion, "usuario": usuario, "titulo": titulo,
                              "memoria": memoria, "foco": foco, "turnos": turnos}
        return True


def _conversar(base, pregunta, sesion=None, despacho="cartera", usuario="t"):
    from asistente import grafo, panel, sesiones
    from core import modelos

    with patch.object(sesiones, "cargar", base.cargar), patch.object(sesiones, "guardar", base.guardar), \
         patch.object(panel, "conversacion", lambda s: {"id": s, "llamadas": 2}), \
         patch.object(modelos, "modelo", _proveedor(despacho)), \
         patch.object(grafo, "_ejecutar", lambda ag, n, a: _TOOLS[n](**a)):
        return sesiones.preguntar(pregunta, usuario=usuario, sesion=sesion)


def test_una_conversacion_se_guarda_con_su_dueno_y_se_retoma_por_sesion(permiso):
    """La memoria vive en la base: la segunda pregunta ve la primera sin que el
    navegador mande nada más que el id. Los turnos quedan enteros y el título
    es la primera pregunta."""
    base = _Base()
    r1 = _conversar(base, "¿qué tengo en la 805?")
    sid = r1["sesion"]["id"]
    assert r1["guardada"] and r1["titulo"] == "¿qué tengo en la 805?"
    fila = base.filas[sid]
    assert fila["usuario"] == "t" and fila["foco"] == {"cuenta": "805"}
    assert [x["pregunta"] for x in fila["turnos"]] == ["¿qué tengo en la 805?"]
    assert fila["turnos"][0]["mundos"] == ["cartera"] and fila["turnos"][0]["respuesta"]

    r2 = _conversar(base, "¿y en dólares?", sesion=sid)
    assert r2["sesion"]["id"] == sid and r2["titulo"] == r1["titulo"]
    assert len(base.filas[sid]["turnos"]) == 2
    assert base.filas[sid]["memoria"][0]["content"] == "¿qué tengo en la 805?", "la memoria acumula"

    # Otro usuario con el mismo id no la ve: arranca una conversación nueva.
    r3 = _conversar(base, "hola", sesion=sid, usuario="otro")
    assert r3["sesion"]["id"] != sid and base.filas[sid]["usuario"] == "t"


def test_si_la_base_no_contesta_la_pregunta_igual_sale_y_lo_dice(permiso):
    from asistente import grafo, panel, sesiones
    from core import modelos

    def roto(*a, **k):
        return None, "no pude leer la conversación guardada"

    with patch.object(sesiones, "cargar", roto), patch.object(sesiones, "guardar", lambda *a, **k: False), \
         patch.object(panel, "conversacion", lambda s: {"id": s}), \
         patch.object(modelos, "modelo", _proveedor("cuenta")), \
         patch.object(grafo, "_ejecutar", lambda ag, n, a: _TOOLS[n](**a)):
        r = sesiones.preguntar("¿qué tengo?", usuario="t", sesion="a" * 32)
    assert r["respuesta"] and r["guardada"] is False and "no pude leer" in r["aviso"]


def test_abrir_listar_y_borrar_validan_la_sesion_por_forma():
    from asistente import sesiones

    assert sesiones.es_valida("a" * 32) and not sesiones.es_valida("a; DROP TABLE")
    assert sesiones.cargar("raro", "t") == (None, None)
    assert sesiones.borrar("raro", "t")["ok"] is False
    assert sesiones._titulo("  x  " * 60).endswith("…") and len(sesiones._titulo("hola")) == 4


# ── historial por mundo, esquema y herramientas ─────────────────────────────


def test_una_pregunta_de_un_solo_mundo_no_le_llega_al_otro_despues(permiso):
    """«¿la tenencia de la 805?» fue solo a cuenta. En la pregunta siguiente,
    mercado no la recibe: sin este filtro la tomaba como pendiente y salía a
    buscar la ficha del bono «805»."""
    from asistente import memoria

    r = _correr("¿la tenencia de la 805?", despacho="cartera")
    pregunta = r["mensajes"][0]
    assert pregunta["role"] == "user" and pregunta["mundos"] == ["cartera"]
    assert memoria.de_mundo(r["mensajes"], "mercado") == []
    assert [m["role"] for m in memoria.de_mundo(r["mensajes"], "cartera")][:2] == ["user", "assistant"]
    # Una pregunta sin marca (historial viejo) la ven todos.
    assert memoria.de_mundo([{"role": "user", "content": "x"}], "mercado") == [{"role": "user", "content": "x"}]
    assert all(k not in memoria.a_dicts(memoria.desde_dicts(r["mensajes"]))[0] for k in memoria.MARCAS)


def test_un_mundo_que_no_pudo_contestar_cierra_su_turno(permiso):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", side_effect=modelos.SinClave("falta OPENAI_API_KEY")):
        r = grafo.preguntar("¿qué tengo?", usuario="t")
    cierres = [m for m in r["mensajes"] if m["role"] == "assistant"]
    assert cierres and all(m["content"].startswith("No pude contestar") for m in cierres)


def test_leer_entiende_el_renglon_falta_y_la_instruccion_lo_pide():
    from asistente import esquema as ESQ
    from asistente.mundos import MUNDOS

    assert ESQ.leer("Tenés 2 bonos.\nFalta: los cupones, la herramienta falló") == \
        {"respuesta": "Tenés 2 bonos.", "falta": "los cupones, la herramienta falló"}
    assert ESQ.leer("Falta: todo") == {"respuesta": None, "falta": "todo"}
    assert ESQ.leer("no falta nada") == {"respuesta": "no falta nada", "falta": None}
    # Solo el último renglón: un «Falta:» en el medio es texto y no se come lo de abajo.
    medio = "Tenés AL30.\nFalta: el precio.\nY te falta abonar el cupón de mayo."
    assert ESQ.leer(medio) == {"respuesta": medio, "falta": None}
    assert ESQ.leer("Hola.\n\nFalta: x\n\n") == {"respuesta": "Hola.", "falta": "x"}
    assert ESQ.FALTA_MARCA in MUNDOS["cartera"].instruccion({}) and ESQ.FALTA_MARCA in MUNDOS["mercado"].instruccion({})


def test_con_herramientas_nunca_viaja_el_esquema_y_sin_ellas_si():
    """OpenAI con `response_format` exige herramientas `strict` y las nuestras
    no lo son (medido: «Only `strict` function tools can be auto-parsed»)."""
    from asistente import despacho as DESP
    from asistente import esquema as ESQ
    from asistente import grafo
    from asistente import junta as JU
    from asistente.mundos import MUNDOS
    from core import modelos

    pedidos = []

    def fake(tarea, *, usuario=None, sesion=None, esquema=None, traza=None):
        pedidos.append((tarea, esquema))
        return _Falso("cuenta", tarea, esquema)

    with patch.object(modelos, "modelo", fake):
        grafo._modelo_de(MUNDOS["cartera"], {}, None, esquema=ESQ.FORMATO)
        grafo._modelo_de(JU.JUNTA, {}, None, esquema=ESQ.FORMATO)
        grafo._modelo_de(DESP.DESPACHO, {}, None)
    assert pedidos == [("asistente_cartera", None), ("asistente_cartera", ESQ.FORMATO),
                       ("asistente_despacho", None)]


def test_la_ficha_de_una_tarea_dice_lo_declarado_para_el_panel():
    from core import modelos

    with patch.object(modelos, "ajustes", return_value={}):
        f = modelos.ficha_de("asistente_cartera")
    assert f["declarado"] == {"proveedor": "openai", "tier": "pro"} and f["elegido"] is False


# ── panel ───────────────────────────────────────────────────────────────────


def test_el_costo_cobra_lo_cacheado_a_precio_de_cache():
    from asistente import panel

    t = (1.0, 0.1, 2.0)
    assert panel.costo(t, cache_hit=800_000, cache_miss=200_000, tokens_in=1_000_000,
                       tokens_out=100_000) == pytest.approx(0.48)
    assert panel.costo(t, cache_hit=0, cache_miss=0, tokens_in=1_000_000,
                       tokens_out=0) == pytest.approx(1.0), "sin telemetría, precio lleno"


def test_el_costo_de_una_conversacion_sigue_las_reglas_del_gasto():
    from asistente import panel

    filas = [("m-caro", 3, 1_000_000, 100_000, 800_000, 200_000), ("m-sin", 1, 10, 10, 0, 0)]
    cur = MagicMock()
    cur.fetchall.return_value = filas
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    tarifas = {panel.CLAVE_PRECIO.format(modelo="m-caro"): "1/0.1/2"}
    with patch.object(panel, "get_pool", return_value=pool), \
         patch.object(panel.modelos, "ajustes", return_value=tarifas):
        r = panel.conversacion("s1")
    assert r["llamadas"] == 4 and r["cache_pct"] == 80.0
    assert r["usd"] is None and r["sin_precio"] == ["m-sin"]
    tarifas[panel.CLAVE_PRECIO.format(modelo="m-sin")] = "1/1/1"
    with patch.object(panel, "get_pool", return_value=pool), \
         patch.object(panel.modelos, "ajustes", return_value=tarifas):
        assert panel.conversacion("s1")["usd"] == round(0.48 + 20 / 1e6, 4)


def test_elegir_un_modelo_prueba_antes_de_guardar_y_solo_exige_tools_a_quien_las_usa():
    from asistente import panel
    from core import modelos

    cuerpo = inspect.getsource(panel.elegir_modelo)
    assert cuerpo.index("probar(") < cuerpo.index("INSERT INTO"), "la prueba vive antes del INSERT"
    with patch.object(modelos, "ajustes", return_value={}):
        assert modelos.ficha_de("asistente_cartera")["usa_herramientas"] is True
        assert modelos.ficha_de("asistente_despacho")["usa_herramientas"] is False


def test_cada_tarea_del_asistente_dice_para_que_es():
    from core import modelos

    with patch.object(modelos, "ajustes", return_value={}):
        for t in modelos.tareas():
            assert modelos.ficha_de(t)["para_que"]


# ── el registro y el despacho por reglas ────────────────────────────────────


def test_todo_mundo_esta_registrado_y_declarado():
    """Un archivo en `asistente/mundos/` es un mundo: tiene que estar en
    `MUNDOS`, con tarea en `core/modelos.TAREAS`, señales y herramientas."""
    import importlib

    from asistente.mundos import MUNDOS
    from core import modelos

    archivos = sorted(p.stem for p in (RAIZ / "asistente" / "mundos").glob("*.py") if p.stem != "__init__")
    assert archivos, "no hay mundos"
    for nombre in archivos:
        mod = importlib.import_module(f"asistente.mundos.{nombre}")
        assert hasattr(mod, "AGENTE"), f"asistente/mundos/{nombre}.py no declara AGENTE"
        assert MUNDOS.get(mod.AGENTE.nombre) is mod.AGENTE, f"{nombre} no está en MUNDOS"
        assert mod.AGENTE.tarea in modelos.TAREAS, f"{nombre}: tarea sin declarar"
        assert mod.AGENTE.senales, f"{nombre}: sin señales, el despacho no lo encontraría"
    assert set(MUNDOS) == set(archivos)


def test_las_reglas_del_despacho_deciden_lo_obvio_y_dejan_el_resto_al_modelo(permiso):
    from asistente import despacho as D

    assert D.por_reglas("hola, ¿cómo va?", {}) is None
    meta = D.por_reglas("¿Qué sabés hacer?", {})
    assert meta.mundos == () and "cobros_futuros" in meta.respuesta and "curva" in meta.respuesta
    assert D.por_reglas("cuál es la tenencia de la 805", {}).mundos == ("cartera",)
    assert D.por_reglas("quién es el titular de la 805", {}).mundos == ("cliente",)
    assert D.por_reglas("qué se operó hoy en AL30", {}).mundos == ("operaciones",)
    # Con cuenta en foco o nombrada pero sin señales, tres mundos la atienden: decide el modelo.
    assert D.por_reglas("¿y en dólares?", {"cuenta": "805"}) is None
    assert D.por_reglas("dame la 805", {}) is None
    assert "cuenta = 805" in D.DESPACHO.instruccion({"cuenta": "805"})
    assert D.por_reglas("qué bonos CER conocés", {}).mundos == ("mercado",)
    cruzada = D.por_reglas("qué bono CER rinde más que los que tengo en la 805", {})
    assert cruzada.mundos == ("cartera", "mercado") and cruzada.motivo.startswith("regla: señales")
    assert D.por_reglas("cuánto rinde el TX28", {"cuenta": "805"}).mundos == ("mercado",)
    assert D.por_reglas("quién atiende la 805 y qué tenencia tiene", {}).mundos == ("cartera", "cliente")
    # Palabra entera, no raíz: «cerca» y «cerrá» no son CER; «tealdi» no es TEA.
    assert D.por_reglas("cerrá la posición", {}).mundos == ("cartera",)
    assert D.por_reglas("qué hay cerca del vencimiento", {}) is None
    assert D.por_reglas("qué tasa tiene la ON de tealdi", {}).motivo == "regla: señales (mercado: on)"
    assert D.por_reglas("tenés alguna ON?", {}).mundos == ("mercado",)
    # Meta es la pregunta entera, no una palabra adentro.
    assert D.por_reglas("hola, ¿qué sabés hacer vos?", {}).mundos == ()
    assert D.por_reglas("necesito ayuda con la 805", {}) is None
    assert D.leer_eleccion("cartera, mercado") == ["cartera", "mercado"]
    assert D.leer_eleccion("no hace falta la cuenta") == []


def test_una_regla_que_contesta_no_llama_a_ningun_modelo(permiso):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", side_effect=AssertionError("no tenía que llamar")):
        r = grafo.preguntar("¿qué sabés hacer?", usuario="t")
    assert r["mundos"] == [] and "cuenta" in r["respuesta"] and r["error"] is None
    assert r["tokens_in"] == 0 and [e["tipo"] for e in r["eventos"]] == ["pregunta", "despacho", "texto"]
    assert r["mensajes"][-1] == {"role": "assistant", "content": r["respuesta"], "mundo": "despacho"}
    assert r["mensajes"][0]["mundos"] == []
    # En el turno siguiente, ningún mundo recibe esa pregunta: la contestó una regla.
    from asistente import memoria
    assert memoria.de_mundo(r["mensajes"], "cartera") == [] and memoria.de_mundo(r["mensajes"], "mercado") == []
    r2 = _correr("cuánto tengo en la 805", despacho="cartera", historial=r["mensajes"], sesion=r["sesion"])
    assert [m["role"] for m in memoria.de_mundo(r2["mensajes"], "cartera")][:2] == ["user", "assistant"]


def test_una_pregunta_sin_senales_va_al_modelo_de_despacho(permiso):
    r = _correr("hola, ¿cómo va?", despacho="mercado")
    ev = next(e for e in r["eventos"] if e["tipo"] == "despacho")
    assert ev["motivo"] == "eligió el modelo" and r["mundos"] == ["mercado"]


# ── lo que salió de la primera conversación real ────────────────────────────


def test_cobros_acepta_una_fecha_limite_y_la_pisa_sobre_dias(permiso):
    from datetime import date

    from asistente.mundos import cartera as MC

    cur = MagicMock()
    cur.fetchall.side_effect = [[], [], [], []]
    cur.fetchone.return_value = (None, None)
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    limite = (date.today().replace(day=1) + __import__("datetime").timedelta(days=200)).isoformat()
    with patch.object(MC, "get_pool", return_value=pool):
        r = MC.cobros_futuros("805", dias=5, hasta=limite)
        assert "error" not in r and r["ventana"]["hasta"] == limite, r
        assert "error" in MC.cobros_futuros("805", hasta="ayer")
        assert "error" in MC.cobros_futuros("805", hasta="2020-01-01")


def test_la_tenencia_trae_lo_que_el_mercado_dice_de_cada_titulo(permiso):
    """«¿Cuánto rinden los bonos que tengo?» lo contesta cuenta sola: el cruce
    con el mercado lo hace el código, por ticker, y lo que no está se dice."""
    from api.services import curvas_vista as CV
    from api.services import valuaciones_sql as VS
    from asistente.mundos import cartera as MC

    vista = {"bonos": [{"ticker_corto": "AO28", "pill": "hard_dolar", "emisor": "Argentina",
                        "vencimiento": "2028-10-31", "tasa_ruido": False,
                        "metrics": {"last_price": 71.5, "TEA": 0.112, "TEM": None,
                                    "paridad": 0.8, "duration": 1.9, "total_nominals": 10}}]}
    pos = {"fecha": "2026-09-14", "total": 100.0, "posiciones": [
        {"ticker": "AO28", "emisor": "Argentina", "cantidad": 10, "precio": 70, "valuacion": 50, "share": 0.5},
        {"ticker": "YFCOO", "emisor": "YPF Luz", "cantidad": 10, "precio": 5, "valuacion": 50, "share": 0.5}]}
    with patch.object(CV, "get_curvas_vista", return_value=vista), \
         patch.object(VS, "posiciones_actuales", return_value=pos):
        r = MC.tenencia_actual("805")
    assert r["posiciones"][0]["tea_pct"] == 11.2 and r["posiciones"][0]["vencimiento"] == "2028-10-31"
    assert r["posiciones"][1]["tea_pct"] is None and r["sin_mercado"] == ["YFCOO"]
    assert "tea_pct" in r["_tabla"]["columnas"] and r["mercado_error"] is None
    with patch.object(CV, "get_curvas_vista", side_effect=RuntimeError("caída")), \
         patch.object(VS, "posiciones_actuales", return_value=pos):
        r = MC.tenencia_actual("805")
    assert r["total"] == 100.0 and "caída" in r["mercado_error"], "sin mercado, la tenencia igual sale"


def test_el_system_lleva_la_fecha_de_hoy_al_final_y_pide_texto_plano(permiso):
    from datetime import date

    from asistente import agente as AGT
    from asistente.mundos import MUNDOS

    s = AGT.sistema(MUNDOS["mercado"], {})
    assert s.rstrip().endswith(f"Hoy es {date.today().isoformat()}.") and "sin asteriscos" in s


def test_un_turno_guardado_lleva_las_tablas_que_declararon_las_herramientas():
    from asistente import sesiones

    eventos = [{"tipo": "pide"}, {"tipo": "resultado", "resultado": {
        "total": 9, "posiciones": [{"a": 1}] * 3, "_tabla": {"campo": "posiciones", "columnas": ["a"], "total": "total", "moneda": "ARS"}}},
        {"tipo": "resultado", "resultado": {"error": "x"}}, {"tipo": "resultado", "resultado": "texto"}]
    assert sesiones.tablas_de(eventos) == [{"columnas": ["a"], "filas": [{"a": 1}] * 3, "cuantas": 3,
                                            "total": 9, "moneda": "ARS"}]


# ── cliente, operaciones y el dato personal ─────────────────────────────────


def test_la_ficha_del_cliente_recorta_el_documento_y_lleva_el_permiso(permiso):
    from datetime import date

    from asistente.mundos import cliente as MCL

    fila = ("[805] NOMBRE", "Persona Física", "MINORISTA", "DNI", "30123456", "x@y.com", "11-5555",
            "Santa Fe", "Rosario", "Juan Operador", "juan@aca.com", "PRODUCTORES", "AGRO", None,
            "ALTO", "Activa", "ACTIVA", date(2020, 1, 2), "MODERADO", "BAJO", None, None)
    cur = MagicMock()
    cur.fetchone.return_value = fila
    cur.fetchall.return_value = [("Mesa Rosario",)]
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    with patch.object(MCL, "get_pool", return_value=pool):
        r = MCL.ficha_cliente("805")
    assert r["titular"]["documento"] == "DNI …456" and r["titular"]["email"] == "x@y.com"
    assert r["comercial"]["operador"] == "Juan Operador" and r["comercial"]["segmento"] == ["PRODUCTORES", "AGRO"]
    assert r["grupos"] == ["Mesa Rosario"] and r["sin_legajo"] is False
    assert "error" in MCL.ficha_cliente("999")
    fuente = (RAIZ / "asistente" / "mundos" / "cliente.py").read_text(encoding="utf-8")
    assert "{permitido.FILTRO_SQL}" in fuente


def test_el_dato_personal_nunca_sale_a_quien_entrena_y_no_deja_texto_en_la_traza():
    from core import modelos
    from core import traza as TR

    t = modelos.resolver("asistente_cliente")
    assert t.datos == "personal" and t.datos_negocio and t.datos_personales
    with patch.object(modelos, "clave", return_value="k"), \
         patch("config.IA_PERMITE_PROVEEDOR_QUE_ENTRENA", True):
        modelos.permitido_salir(t)                      # openai: sale
        inseguro = modelos.Tarea(**{**t.__dict__, "proveedor": "deepseek"})
        with pytest.raises(modelos.RuteoInseguro):
            modelos.permitido_salir(inseguro)           # el flag no alcanza
    # Por el camino real: la traza que arma el grafo para el mundo cliente.
    from asistente import grafo
    from asistente.mundos import MUNDOS
    tr = grafo._traza(MUNDOS["cliente"], {"usuario": "u", "sesion": "s"})
    assert isinstance(tr, TR.Traza) and tr.guardar_texto is False
    assert grafo._traza(MUNDOS["cartera"], {}).guardar_texto is True
    cur = MagicMock()
    cur.fetchone.return_value = (7,)
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    with patch("core.postgres.get_pool", return_value=pool):
        tr._escribir(ok=True, error=None, tokens_in=1, tokens_out=1, cache_hit=None, cache_miss=None,
                     latencia_ms=1, detalle="quién es la 805", respuesta="Es Fulano", modelo="gpt-x")
    escrito = cur.execute.call_args.args[1]
    assert escrito[8] is None and escrito[9] is None, "ni pedido ni respuesta en el libro"


def test_un_mundo_sin_herramientas_no_llama_al_modelo_y_lo_dice(permiso):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", side_effect=AssertionError("no tenía que llamar")):
        r = grafo.preguntar("qué se operó hoy", usuario="t")
    assert r["mundos"] == ["operaciones"] and r["respuesta"] is None
    assert "Todavía no puedo consultar operaciones" in r["error"]
    assert r["mensajes"][-1]["mundo"] == "operaciones" and r["tokens_in"] == 0
