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
    from asistente.agentes import cartera as MC

    fake = {"cuentas": [{"id_cuenta": c, "nombre": f"NOMBRE {c}"} for c in CUENTAS],
            "cuantas": len(CUENTAS)}
    with patch.object(permitido, "cuentas", return_value=list(CUENTAS)), \
         patch.object(MC, "cuentas_disponibles", return_value=fake):
        yield


# ── agentes ─────────────────────────────────────────────────────────────────


def test_cada_agente_es_un_objeto_con_su_tarea_declarada_en_el_ruteo():
    from asistente import junta as JU
    from asistente import ruteo as RUT
    from asistente.agentes import AGENTES
    from core import modelos

    for a in AGENTES.values():
        assert a.tarea in modelos.tareas(), f"`{a.nombre}` rutea por una tarea que no existe"
        assert a.instruccion({}).strip()
    assert AGENTES["cartera"].tarea != AGENTES["renta_fija"].tarea, "cada agente tiene su propio ruteo"
    assert AGENTES["cartera"].tarea == JU.TAREA, "la junta ve datos del negocio"
    # El ruteo y la junta NO son agentes: no tienen herramientas ni bucle, son
    # una tarea y una función. Un `Agente` sin herramientas sería un agente falso.
    for mod in (RUT, JU):
        assert mod.TAREA in modelos.tareas() and not hasattr(mod, "AGENTE")
        assert mod.instruccion({}).strip()


def test_solo_el_agente_cuenta_ve_datos_del_negocio_y_aprende_el_foco():
    from asistente import ruteo as RUT
    from asistente.agentes import AGENTES
    from core import modelos

    assert modelos.resolver(AGENTES["cartera"].tarea).datos_negocio
    assert not modelos.resolver(AGENTES["renta_fija"].tarea).datos_negocio
    assert not modelos.resolver(RUT.TAREA).datos_negocio
    assert AGENTES["cartera"].foco == ("cuenta",) and AGENTES["renta_fija"].foco == ("ticker",)


def test_el_agente_de_cada_herramienta_se_declara_y_los_dos_se_excluyen():
    from asistente import herramientas as H
    from asistente.agentes import AGENTES

    assert set(H.TODAS) == {f for a in AGENTES.values() for f in a.herramientas}
    assert not set(AGENTES["cartera"].herramientas) & set(AGENTES["renta_fija"].herramientas)
    assert len(H.POR_NOMBRE) == len(H.TODAS), "dos herramientas con el mismo nombre"
    for fn in AGENTES["cartera"].herramientas:
        p = inspect.signature(fn).parameters.get("cuenta")
        assert p is not None and p.default is inspect._empty, f"`{fn.__name__}`: cuenta obligatoria"
    for fn in AGENTES["renta_fija"].herramientas:
        assert "cuenta" not in inspect.signature(fn).parameters, (
            f"`{fn.__name__}` es del mercado y recibe `cuenta`: alcance sin permiso")


def test_la_instruccion_de_cuenta_lleva_las_cuentas_y_el_foco_al_final(permiso):
    from asistente.agentes import AGENTES

    sin = AGENTES["cartera"].instruccion({})
    con = AGENTES["cartera"].instruccion({"cuenta": "805"})
    assert "805" in sin and "NOMBRE 805" in sin
    assert con.startswith(sin) and con.rstrip().endswith("cuenta = 805.")
    assert "preguntale cuál quiere" in sin
    assert "cuenta = 805" not in AGENTES["renta_fija"].instruccion({"cuenta": "805"})


def test_el_ruteo_describe_cada_agente_por_su_nombre():
    from asistente import ruteo as RUT
    from asistente.agentes import AGENTES

    txt = RUT.instruccion({})
    for n, a in AGENTES.items():
        assert f"{n}: {a.describe}" in txt


# ── herramientas ────────────────────────────────────────────────────────────


def test_el_esquema_sale_de_la_firma_con_enum_y_tipos_reales():
    from asistente import herramientas as H
    from asistente.agentes import cartera as MC
    from asistente.agentes import renta_fija as MM
    from core import curvas_ejes as ce

    props = H.ficha(MM.curva)["function"]["parameters"]["properties"]
    assert props["curva"]["enum"] == list(MM.Curva.__args__)
    assert props["ordenar_por"]["enum"] == list(MM.OrdenCurva.__args__)
    assert props["limit"]["type"] == "integer"
    assert H.ficha(MC.cobros_futuros)["function"]["parameters"]["properties"]["dias"]["type"] == "integer"
    assert H.ficha(MC.cobros_futuros)["function"]["parameters"]["required"] == ["cuenta"]
    assert set(MM.Curva.__args__) == set(ce.PILLS)


# Las órdenes que `agente.COMUN` ya le da a TODO agente, en el system. Repetirlas
# adentro de una ficha es tener la misma instrucción en dos lugares: el día que
# cambie el criterio en COMUN, la ficha sigue diciendo lo viejo y no falla nada
# (REGLA #9, ahora sobre instrucciones en vez de datos).
ORDENES_DE_COMUN = r"\b(no|nunca) (conviertas|inventes|uses|sumes|calcules|redondees)\b|no l[oa]s? vuelvas a calcular|no l[oa] calcules"


def test_una_ficha_no_repite_una_orden_que_el_system_ya_da():
    """La ficha dice QUÉ hay y qué devuelve; el system dice CÓMO comportarse.
    Una orden específica de la herramienta («si preguntan por un extremo,
    ordená por ese campo») SÍ va en la ficha: lo que no va es repetir COMUN."""
    import re

    from asistente.agentes import AGENTES

    repetidas = [(f.__name__, l.strip())
                 for a in AGENTES.values() for f in a.herramientas
                 for l in (inspect.getdoc(f) or "").splitlines()
                 if re.search(ORDENES_DE_COMUN, l, re.I)]
    assert not repetidas, (
        "estas fichas repiten una orden que `agente.COMUN` ya da en el system:\n  "
        + "\n  ".join(f"{n}: {l}" for n, l in repetidas)
        + "\n\nDecí QUÉ trae el campo («ya viene calculado», «separado por moneda») "
          "y dejá la orden en COMUN, una sola vez.")


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


def test_lo_que_empieza_con_guion_bajo_no_viaja_al_modelo_pero_el_aviso_si():
    """Los DATOS de la tabla no viajan (ya están en el resultado) pero el HECHO
    de que se dibujó sí: sin eso, «no la enumeres» le pide al modelo evaluar una
    condición sobre lo único que se le oculta, y enumera igual."""
    from asistente import herramientas as H
    from asistente import pantalla

    r = {"total": 1, "posiciones": [{"a": 1}],
         "_tabla": pantalla.tabla("posiciones", ["a"], "Tenencia de la 805")}
    visto = H.para_el_modelo(r)
    assert set(visto) == {"total", "posiciones", "se_muestra"}, "_tabla no, se_muestra sí"
    assert "Tenencia de la 805" in visto["se_muestra"] and "1 fila" in visto["se_muestra"]
    assert H.para_el_modelo("texto") == "texto"
    # sin tabla, o con una tabla sin filas, no se le anuncia nada
    assert H.para_el_modelo({"total": 1}) == {"total": 1}
    vacia = {"posiciones": [], "_tabla": pantalla.tabla("posiciones", ["a"], "Vacía")}
    assert H.para_el_modelo(vacia) == {"posiciones": []}


def test_toda_tabla_declara_de_que_es():
    """Una tabla sin sujeto es un dato sin dueño: con dos herramientas en un
    turno salen dos tablas pegadas y no se sabe cuál es de qué. El título va
    por FIRMA para que no se pueda olvidar, y ninguna herramienta arma el dict
    a mano."""
    from asistente import pantalla

    with pytest.raises(ValueError):
        pantalla.tabla("x", ["a"], "")
    for nombre in ("cartera", "renta_fija", "dolares", "cliente", "operaciones"):
        fuente = (RAIZ / "asistente" / "agentes" / f"{nombre}.py").read_text(encoding="utf-8")
        assert '"_tabla": {' not in fuente, (
            f"{nombre}.py arma la tabla a mano: usá `pantalla.tabla(...)`, que exige el título")


def test_toda_consulta_a_datos_de_cuentas_lleva_el_permiso():
    from asistente import permitido

    fuente = (RAIZ / "asistente" / "agentes" / "cartera.py").read_text(encoding="utf-8")
    consultas = re.findall(r'sql_\w+ = f"""(.*?)"""', fuente, re.S)
    assert consultas
    for sql in consultas:
        assert "{permitido.FILTRO_SQL}" in sql, "una consulta de cuentas sin el permiso"
    assert permitido.FILTRO_SQL.startswith("t.id_cuenta = ANY(")


def test_sin_cuentas_habilitadas_no_se_muestra_nada():
    from asistente import permitido
    from asistente.agentes import cartera as MC

    with patch.object(permitido, "cuentas", return_value=[]):
        for fn, args in ((MC.cobros_futuros, {"cuenta": "805"}),
                         (MC.tenencia_actual, {"cuenta": "805"})):
            r = fn(**args)
            assert "error" in r and "habilitada" in r["error"]


def _posiciones_805():
    """La cuenta 805 real: 5 bonos en dos carteras (HD) y tres saldos de caja.
    CP41O es el caso que importa — es un bono sin datos de mercado hoy, y sigue
    siendo un bono: lo dice su cartera, no si el master de curvas lo encontró."""
    def p(tk, cartera, val, tea=None):
        return {"ticker": tk, "emisor": "x", "clase_activo": cartera, "cartera": cartera,
                "cantidad": 1, "precio": 1, "valuacion": val, "share": 1, "tea_pct": tea}
    return [p("AO28", "HD", 13027902.8, 9.17), p("YFCOO", "HD", 11478600, 4.4),
            p("AO29", "HD", 8885280, 10.12), p("YM38O", "HD", 8778412, 3.51),
            p("CP41O", "HD", 1555000), p("USDC", "MONEDAS", 797.73),
            p("ARS", "MONEDAS", -646079.25), p("USD", "MONEDAS", -10654489.42)]


def _tenencia(cuenta="805", **kw):
    """Corre `tenencia_actual` con la 805 real y sin base."""
    from api.services import valuaciones_sql
    from asistente.agentes import cartera as MC

    with patch.object(valuaciones_sql, "posiciones_actuales",
                      return_value={"posiciones": _posiciones_805(), "total": 32425423.87,
                                    "fecha": "2026-09-15"}), \
         patch.object(MC, "metricas_por_ticker", return_value={}):
        return MC.tenencia_actual(cuenta, **kw)


def test_el_nombre_de_una_cartera_se_escribe_en_UN_solo_lugar():
    """`core/cartera.py` es la fuente. Estaban en cuatro archivos: ahí, en
    `core/clase_activo.py`, en `agente/clase.py` y en `jobs/assets_autofill.py`
    — cuatro copias sin árbitro, que es el modo de falla que no falla: el día
    que se agregue una cartera de bonos, una mitad se entera y la otra no."""
    from agente import clase
    from asistente.agentes.cartera import TIPOS
    from core import cartera as CART
    from core import clase_activo as CA

    assert CART.BONOS == (CART.HD, CART.ARS, CART.DL)
    # todos los consumidores apuntan al MISMO objeto, no a un string igual
    assert TIPOS["bonos"] is CART.BONOS and TIPOS["fondos"] is CART.FCI
    assert CA.CARTERA_DERIVADOS is CART.DERIVADOS and CA.CARTERA_ARS is CART.ARS
    assert CA.RENTA_VARIABLE is CART.RENTA_VARIABLE
    assert clase.CARTERAS_FCI is CART.FCI
    # y nadie vuelve a DECLARAR uno. Se busca la asignación (`= "HD"`) y la
    # tupla (`("HD"`), no la comparación: `core/clase_activo.py` compara el
    # SUBYACENTE de Primary contra «RENTA VARIABLE» y eso NO es la cartera —
    # se escriben igual y son dos cosas distintas, que es la REGLA #9 al revés.
    literales = ('"HD"', '"DL"', '"RENTA VARIABLE"', '"DERIVADOS"', '"CARTERA FCI"')
    for rel in ("asistente/agentes/cartera.py", "core/clase_activo.py",
                "agente/clase.py", "jobs/assets_autofill.py"):
        codigo = "\n".join(l for l in (RAIZ / rel).read_text(encoding="utf-8").splitlines()
                           if not l.lstrip().startswith("#"))
        for lit in literales:
            declara = re.search(rf"(?<![=!<>]) = {re.escape(lit)}|\({re.escape(lit)}", codigo)
            assert not declara, f"{rel} declara {lit}: importalo de `core/cartera.py`"


def test_preguntar_por_bonos_no_devuelve_la_cuenta_entera(permiso):
    """«Qué bonos tengo» traía los 8 renglones, con los saldos de caja adentro y
    el total de toda la cuenta abajo. Un saldo en USD no es un bono."""
    r = _tenencia(tipo="bonos")
    assert [p["ticker"] for p in r["posiciones"]] == ["AO28", "YFCOO", "AO29", "YM38O", "CP41O"]
    assert "CP41O" in [p["ticker"] for p in r["posiciones"]], (
        "un bono sin mercado hoy sigue siendo un bono: lo dice su cartera")
    assert r["cuantas"] == 5 and r["cuantas_en_la_cuenta"] == 8
    # el total de la tabla es el de lo pedido, no el de la cuenta con la caja adentro
    assert r["total_tipo"] == 43725194.8 and r["total"] == 32425423.87
    assert r["_tabla"]["total"] == "total_tipo"
    assert r["_tabla"]["titulo"].startswith("Bonos de la 805")
    # y la caja es el complemento exacto
    assert [p["ticker"] for p in _tenencia(tipo="caja")["posiciones"]] == ["USDC", "ARS", "USD"]


def test_sin_tipo_viene_todo_y_el_total_sigue_siendo_el_de_la_cuenta(permiso):
    r = _tenencia()
    assert r["cuantas"] == 8 and r["tipo"] is None and r["total_tipo"] is None
    assert r["_tabla"]["total"] == "total" and r["_tabla"]["titulo"].startswith("Tenencia de la 805")
    assert r["carteras"] == [{"cartera": "HD", "posiciones": 5},
                             {"cartera": "MONEDAS", "posiciones": 3}]


def test_un_tipo_que_la_cuenta_no_tiene_dice_que_si_tiene(permiso):
    """Una tabla vacía haría creer que se miró y no había. Se contesta con el
    dato que destraba: qué carteras SÍ hay."""
    r = _tenencia(tipo="acciones")
    assert "error" in r and "acciones" in r["error"]
    assert r["carteras_en_la_cuenta"] == [{"cartera": "HD", "posiciones": 5},
                                          {"cartera": "MONEDAS", "posiciones": 3}]
    assert "posiciones" not in r
    assert "error" in _tenencia(tipo="cripto"), "un tipo que no existe no corre"


def test_una_cuenta_no_habilitada_vuelve_como_error(permiso):
    """TODA herramienta de cartera corta antes de tocar la base si la cuenta no
    está habilitada. Los demás argumentos se rellenan desde la firma: una
    herramienta nueva entra a este test sola, no hay que acordarse."""
    from asistente.agentes import AGENTES

    for fn in AGENTES["cartera"].herramientas:
        obligatorios = {n: "x" for n, p in inspect.signature(fn).parameters.items()
                        if p.default is inspect._empty and n != "cuenta"}
        r = fn(cuenta="999", **obligatorios)
        assert "error" in r and "preguntale" in r["que_hacer"].lower(), fn.__name__


# ── rotar: el cruce cuenta × mercado que ningún modelo puede hacer ──────────


def _mercado_rotar():
    """El master de curvas como lo ve `metricas_por_ticker`: dos ONs de YPF y
    tres de Vista en hard dollar, más una de Vista en CER que NO tiene que
    aparecer (comparar la TEA de un CER con la de un hard dollar no es
    comparar), y una de Vista que vence en días (`tasa_ruido`)."""
    def b(tk, emisor, tea, dur, curva="hard_dolar", ruido=False, tipo="corporativo"):
        return {"ticker": tk, "emisor": emisor, "emisor_tipo": tipo, "curva": curva,
                "tea_pct": tea, "duration": dur, "paridad_pct": 95.0,
                "vencimiento": "2031-01-01", "tasa_ruido": ruido}
    return {m["ticker"]: m for m in [
        b("YMCXO", "YPF S.A.", 8.9, 4.2), b("YMCHO", "YPF S.A.", 9.6, 3.1),
        b("VSCPO", "VISTA ENERGY", 10.7, 3.8), b("VSCAO", "VISTA ENERGY", 9.2, 2.0),
        b("VSCZO", "VISTA ENERGY", 11.4, 6.5),
        b("VSCER", "VISTA ENERGY", 25.0, 3.0, curva="cer"),
        b("VSC01", "VISTA ENERGY", 99.0, 0.01, ruido=True)]}


def _tenencia_rotar(*tickers):
    return {"posiciones": [{"ticker": tk, "emisor": "YPF SA", "tea_pct": t, "duration": d,
                            "paridad_pct": 95.0, "vencimiento": "2031-01-01", "tasa_ruido": False}
                           for tk, t, d in tickers]}


def test_rotar_compara_contra_el_que_menos_rinde_y_resta_el_codigo(permiso):
    """El modelo tiene los dos lados desde siempre y no puede restarlos. Acá
    los deltas vienen hechos, contra el título tuyo que MENOS rinde — que es el
    candidato natural a salir, y se dice cuál es en vez de dejarlo deducir."""
    from asistente.agentes import cartera as MC

    with patch.object(MC, "tenencia_actual", return_value=_tenencia_rotar(("YMCXO", 8.9, 4.2),
                                                                         ("YMCHO", 9.6, 3.1))), \
         patch.object(MC, "metricas_por_ticker", return_value=_mercado_rotar()):
        r = MC.opciones_para_rotar("805", desde_emisor="YPF", hacia_emisor="Vista")
    assert r["referencia"]["ticker"] == "YMCXO", "el que menos rinde de los míos"
    assert r["curva"] == "hard_dolar" and [t["ticker"] for t in r["tenes"]] == ["YMCXO", "YMCHO"]
    alt = {a["ticker"]: a for a in r["alternativas"]}
    assert list(alt) == ["VSCZO", "VSCPO", "VSCAO"], "de mayor a menor delta de TEA"
    assert alt["VSCPO"]["delta_tea_pp"] == 1.8 and alt["VSCPO"]["delta_duration"] == -0.4
    assert alt["VSCZO"]["delta_tea_pp"] == 2.5 and alt["VSCZO"]["delta_duration"] == 2.3
    assert alt["VSCAO"]["delta_tea_pp"] == 0.3, "una que rinde poco más también entra"
    assert "VSCER" not in alt, "un CER no compara con un hard dollar"
    assert "VSC01" not in alt, "tasa_ruido: vence en días, su TEA no compara"
    # nada de plata: la respuesta habla de rendimientos y de nada más
    crudo = json.dumps(r, ensure_ascii=False)
    assert "valuacion" not in crudo and "nominales" not in crudo and "cantidad" not in crudo


def test_rotar_dice_que_falta_en_vez_de_devolver_una_tabla_vacia(permiso):
    """Los tres «no hay» se contestan con el dato que destraba: qué emisores
    tenés, o qué emisores hay en esa curva. Una lista vacía haría creer que se
    miró y no había nada."""
    from asistente.agentes import cartera as MC

    with patch.object(MC, "tenencia_actual", return_value=_tenencia_rotar(("YMCXO", 8.9, 4.2))), \
         patch.object(MC, "metricas_por_ticker", return_value=_mercado_rotar()):
        # no tengo nada de ese emisor → los que sí tengo
        sin_mio = MC.opciones_para_rotar("805", desde_emisor="TENARIS", hacia_emisor="Vista")
        assert "error" in sin_mio and sin_mio["emisores_en_la_cuenta"] == ["YPF SA"]
        # el destino no tiene nada en esa curva → los emisores que sí hay ahí
        sin_destino = MC.opciones_para_rotar("805", desde_emisor="YPF", hacia_emisor="TENARIS")
        assert "error" in sin_destino and "hard_dolar" in sin_destino["error"]
        assert "VISTA ENERGY" in sin_destino["emisores_en_esa_curva"]
        assert sin_destino["referencia"] == "YMCXO"
        assert "alternativas" not in sin_mio and "alternativas" not in sin_destino
    # tengo el título pero sin tasa hoy: no es que no haya alternativas
    with patch.object(MC, "tenencia_actual",
                      return_value={"posiciones": [{"ticker": "YMCXO", "emisor": "YPF SA",
                                                    "tea_pct": None, "duration": None}]}), \
         patch.object(MC, "metricas_por_ticker", return_value=_mercado_rotar()):
        r = MC.opciones_para_rotar("805", desde_emisor="YPF", hacia_emisor="Vista")
    assert "error" in r and "tasa comparable" in r["error"] and r["tenes"]


def test_rotar_saca_EL_TITULO_QUE_NOMBRO_EL_USUARIO(permiso):
    """«rotar mi YFCOO» tiene que sacar el YFCOO. Sin `ticker`, la herramienta
    elegía por su cuenta el que menos rinde del emisor: en el LAB el usuario
    pidió rotar YFCOO y la referencia salió YM38O."""
    from asistente.agentes import cartera as MC

    with patch.object(MC, "tenencia_actual", return_value=_tenencia_rotar(("YMCXO", 8.9, 4.2),
                                                                         ("YMCHO", 9.6, 3.1))), \
         patch.object(MC, "metricas_por_ticker", return_value=_mercado_rotar()):
        r = MC.opciones_para_rotar("805", ticker="YMCHO", hacia_emisor="Vista")
    assert r["referencia"]["ticker"] == "YMCHO", "el que nombró, no el que menos rinde"
    assert r["referencia"]["por_que"] == "lo nombró el usuario"
    assert r["sale"] == "YMCHO"
    # los deltas son contra ÉSE (TEA 9.6): VSCZO 11.4 → +1.8
    assert {a["ticker"]: a["delta_tea_pp"] for a in r["alternativas"]}["VSCZO"] == 1.8


def test_rotar_acepta_un_TIPO_de_emisor_como_destino(permiso):
    """«rotar a un corporativo» no es un emisor. Sin este argumento el modelo
    metía «corporativo HD» en `hacia_emisor` y la herramienta fallaba — pasó
    en el LAB, y el agente terminó improvisando con `curva` a mano."""
    from asistente.agentes import cartera as MC

    mercado = _mercado_rotar()
    mercado["GD30"] = {"ticker": "GD30", "emisor": "REPUBLICA ARGENTINA",
                       "emisor_tipo": "soberano", "curva": "hard_dolar", "tea_pct": 12.0,
                       "duration": 3.0, "paridad_pct": 95.0, "vencimiento": "2030-01-01",
                       "tasa_ruido": False}
    with patch.object(MC, "tenencia_actual", return_value=_tenencia_rotar(("YMCXO", 8.9, 4.2))), \
         patch.object(MC, "metricas_por_ticker", return_value=mercado):
        r = MC.opciones_para_rotar("805", ticker="YMCXO", hacia_tipo="corporativo")
        assert "GD30" not in [a["ticker"] for a in r["alternativas"]], "un soberano no entra"
        assert r["hacia"] == "corporativo" and r["referencia"]["ticker"] == "YMCXO"
        assert "YMCXO" not in [a["ticker"] for a in r["alternativas"]], "no se sugiere a sí mismo"
        sob = MC.opciones_para_rotar("805", ticker="YMCXO", hacia_tipo="soberano")
        assert [a["ticker"] for a in sob["alternativas"]] == ["GD30"]
        # las combinaciones que no se pueden resolver se dicen, no se adivinan
        assert "error" in MC.opciones_para_rotar("805", hacia_emisor="Vista")
        assert "error" in MC.opciones_para_rotar("805", ticker="YMCXO")
        assert "error" in MC.opciones_para_rotar("805", ticker="YMCXO",
                                                 hacia_emisor="Vista", hacia_tipo="soberano")
        assert "error" in MC.opciones_para_rotar("805", ticker="YMCXO", hacia_tipo="cooperativa")
        falta = MC.opciones_para_rotar("805", ticker="NOTENGO", hacia_tipo="corporativo")
        assert "error" in falta and "YMCXO" in falta["tickers_en_la_cuenta"]


def test_rotar_encuentra_el_titulo_aunque_el_emisor_se_escriba_distinto(permiso):
    """El emisor de la cuenta y el del master de curvas son dos strings que
    pueden no coincidir. Perder un título por eso sería contestar que no tenés
    algo que tenés (REGLA #9)."""
    from asistente.agentes import cartera as MC

    # en la cuenta figura como «PETROLERA X» y en curvas como «YPF S.A.»
    tenencia = {"posiciones": [{"ticker": "YMCXO", "emisor": "PETROLERA X", "tea_pct": 8.9,
                                "duration": 4.2, "tasa_ruido": False}]}
    with patch.object(MC, "tenencia_actual", return_value=tenencia), \
         patch.object(MC, "metricas_por_ticker", return_value=_mercado_rotar()):
        r = MC.opciones_para_rotar("805", desde_emisor="YPF", hacia_emisor="Vista")
    assert r["referencia"]["ticker"] == "YMCXO", "lo encontró por el emisor del master"


def test_el_total_de_cobros_no_sale_de_la_lista_recortada(permiso):
    from asistente.agentes import cartera as MC

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
    from asistente.agentes import cartera as MC

    fake = {"fecha": "2026-09-12", "total": 100.0,
            "posiciones": [{"ticker": "TX26", "cantidad": 1, "valuacion": 100.0, "share": 1.0}]}
    with patch.object(valuaciones_sql, "posiciones_actuales", return_value=fake) as p:
        r = MC.tenencia_actual("805", horizonte="t0")
    assert p.call_args.kwargs["con_pnl"] is False and p.call_args.kwargs["horizonte"] == "t0"
    assert r["total"] == 100.0 and r["_tabla"]["campo"] == "posiciones"
    for c in r["_tabla"]["columnas"]:
        assert c in r["posiciones"][0] or c == "emisor"
    assert "error" in MC.tenencia_actual("805", horizonte="t9")


def _vista_emisores():
    """Una curva hard dollar con tres emisores y un soberano, más una letra con
    TNA: lo mínimo para probar filtro, lista de emisores y la TNA de pantalla."""
    def b(tk, emisor, tipo, pill="hard_dolar", tea=0.10, vto="2030-01-01", tna=None):
        return {"ticker_corto": tk, "pill": pill, "emisor": emisor, "emisor_tipo": tipo,
                "vencimiento": vto, "tasa_ruido": False,
                "metrics": {"last_price": 100.0, "TEA": tea, "TEM": 0.008, "TNA": tna,
                            "paridad": 95.0, "duration": 3.0, "total_nominals": 10}}
    return {"bonos": [
        b("YMCXO", "YPF S.A.", "corporativo", tea=0.09, vto="2033-01-01"),
        b("YMCHO", "YPF S.A.", "corporativo", tea=0.08, vto="2031-01-01"),
        b("PNDCO", "PAN AMERICAN ENERGY", "corporativo", tea=0.07),
        b("GD30", "REPUBLICA ARGENTINA", "soberano", tea=0.12),
        b("S30J6", "REPUBLICA ARGENTINA", "soberano", pill="tasa_fija", tea=0.40, tna=0.3412),
    ]}


def test_la_curva_filtra_por_emisor_por_pedazo_y_nunca_devuelve_vacio_en_silencio():
    """«la curva de YPF»: el usuario escribe la sigla y en la base dice «YPF
    S.A.». Y si el emisor no está, la respuesta trae los que sí — que el modelo
    adivine el nombre exacto es que lo invente."""
    from api.services import curvas_vista as CV
    from asistente.agentes import renta_fija as RF

    with patch.object(CV, "get_curvas_vista", return_value=_vista_emisores()):
        r = RF.curva("hard_dolar", emisor="YPF")
        assert [i["ticker"] for i in r["instrumentos"]] == ["YMCXO", "YMCHO"], "por pedazo"
        assert r["emisor"] == "YPF" and r["cuantos"] == 2
        assert {i["emisor"] for i in r["instrumentos"]} == {"YPF S.A."}
        assert all(i["emisor_tipo"] == "corporativo" for i in r["instrumentos"])
        # minúsculas y espacios de más no cambian nada
        assert len(RF.curva("hard_dolar", emisor="  ypf  ")["instrumentos"]) == 2
        # la lista de emisores viaja SIEMPRE, ordenada por cantidad
        assert RF.curva("hard_dolar")["emisores"] == [
            {"emisor": "YPF S.A.", "bonos": 2},
            {"emisor": "PAN AMERICAN ENERGY", "bonos": 1},
            {"emisor": "REPUBLICA ARGENTINA", "bonos": 1}]
        # un emisor que no está: error CON la salida, no una lista vacía
        fallo = RF.curva("hard_dolar", emisor="TENARIS")
        assert "error" in fallo and "TENARIS" in fallo["error"]
        assert [e["emisor"] for e in fallo["emisores"]] and "instrumentos" not in fallo
        assert "error" in RF.curva("hard_dolar", emisor="   ")


def test_la_tna_es_la_que_publico_el_backend_y_nunca_se_deriva_acá():
    """La TNA llega por DOS vías de `curvas_vista`: `_tna_de` la calcula para
    `tasa_fija` (convención 1816 plazo-rem, medida contra su API), y la rama
    `manda_1816` la copia del proveedor para CUALQUIER curva cuando 1816 manda.

    Donde ninguna aplica, `tna_pct` queda None y la herramienta NO la deriva,
    aunque la pantalla ahí muestre un TEM×12 (`bonos-table.tsx`): esa
    convención no está medida para bonos que amortizan. Un número plausible
    calculado con la fórmula de otro instrumento es el error que no falla.
    """
    from api.services import curvas_vista as CV
    from asistente.agentes import renta_fija as RF

    vista = _vista_emisores()
    # un hard dollar al que 1816 SÍ le publica TNA: viaja tal cual, sin tocarla
    vista["bonos"][0]["metrics"]["TNA"] = 0.0812
    with patch.object(CV, "get_curvas_vista", return_value=vista):
        letra = RF.curva("tasa_fija")["instrumentos"][0]
        assert letra["tna_pct"] == 34.12 and letra["tea_pct"] == 40.0
        hd = {i["ticker"]: i for i in RF.curva("hard_dolar")["instrumentos"]}
    assert hd["YMCXO"]["tna_pct"] == 8.12, "hard dollar CON TNA de 1816: se respeta"
    assert hd["GD30"]["tna_pct"] is None, "sin TNA publicada NO se inventa una"
    assert hd["GD30"]["tea_pct"] == 12.0, "la TEA está siempre: es la comparable"


def test_los_tipos_de_emisor_son_los_de_core():
    """El `Literal` viaja como `enum` en la ficha, así que es lo único que el
    modelo puede pedir. Si se separa de `core.curvas_ejes.EMISORES`, el filtro
    rechaza un tipo que la base sí tiene, y al revés."""
    from typing import get_args

    from asistente.agentes.renta_fija import EmisorTipo
    from core.curvas_ejes import EMISORES

    assert set(get_args(EmisorTipo)) == set(EMISORES)


def test_la_curva_filtra_por_tipo_de_emisor_ANTES_de_recortar():
    """El orden es lo único que importa acá. Medido en el LAB el 2026-09-16:
    «un corporativo HD para rotar» ordenó 125 por TEA, cortó en 15, y de esos
    solo 4 eran corporativos — se recomendó entre 4 de ~110, y los mejores
    podían estar en el puesto 40 sin que nadie los mirara. La respuesta salía
    fundamentada y mal, que es el modo de falla que no falla."""
    from api.services import curvas_vista as CV
    from asistente.agentes import renta_fija as RF

    # 3 soberanos que rinden más, y 2 corporativos abajo: con `limit=3` y sin
    # filtro, los corporativos NO entran.
    def b(tk, tipo, tea):
        return {"ticker_corto": tk, "pill": "hard_dolar", "emisor": tk[:3], "emisor_tipo": tipo,
                "vencimiento": "2030-01-01", "tasa_ruido": False,
                "metrics": {"last_price": 100.0, "TEA": tea, "TEM": 0.01, "paridad": 95.0,
                            "duration": 3.0, "total_nominals": 10}}
    vista = {"bonos": [b("AL1", "soberano", 0.12), b("AL2", "soberano", 0.11),
                       b("AL3", "soberano", 0.10), b("CP1", "corporativo", 0.09),
                       b("CP2", "corporativo", 0.08)]}
    with patch.object(CV, "get_curvas_vista", return_value=vista):
        sin_filtro = RF.curva("hard_dolar", limit=3)
        assert [i["ticker"] for i in sin_filtro["instrumentos"]] == ["AL1", "AL2", "AL3"]
        con_filtro = RF.curva("hard_dolar", limit=3, emisor_tipo="corporativo")
        assert [i["ticker"] for i in con_filtro["instrumentos"]] == ["CP1", "CP2"], (
            "los corporativos salen de TODOS los corporativos, no de los 3 primeros")
        # y el resumen también es de lo filtrado, no de la curva entera
        assert con_filtro["resumen"]["cuantos"] == 2
        assert con_filtro["resumen"]["rinde_mas"]["ticker"] == "CP1"
        assert con_filtro["cuantos"] == 2 and not con_filtro["truncado"]
        # un tipo que no existe en esa curva se dice, con los emisores que hay
        vacio = RF.curva("hard_dolar", emisor_tipo="bcra")
        assert "error" in vacio and "bcra" in vacio["error"] and vacio["emisores"]
        assert "error" in RF.curva("hard_dolar", emisor_tipo="cooperativa")


def test_la_curva_no_le_manda_al_modelo_notas_para_que_las_repita():
    """El `aviso` en prosa («hay 125 y estás viendo los primeros 15…») estaba
    escrito PARA el modelo y viajaba por el canal de los DATOS. El modelo se lo
    repitió al usuario: «la curva está truncada: se ven 15 de 125». La
    instrucción vive en el docstring, que es su canal; en el resultado queda el
    dato estructurado (`truncado`, `cuantos`) y nada más."""
    import inspect

    from api.services import curvas_vista as CV
    from asistente.agentes import renta_fija as RF

    with patch.object(CV, "get_curvas_vista", return_value=_vista_emisores()):
        r = RF.curva("hard_dolar", limit=2)
    assert r["truncado"] is True and r["cuantos"] == 4, "el dato, estructurado"
    frases = [v for v in r.values() if isinstance(v, str) and len(v) > 60]
    assert not frases, f"hay prosa en el resultado y el modelo la va a repetir: {frases}"
    assert "truncado" in inspect.getdoc(RF.curva), "la instrucción va en el docstring"


def test_los_tres_dolares_se_distinguen_y_las_brechas_las_calcula_el_codigo():
    """El modelo tiene prohibido calcular: si la brecha no viene hecha, no la
    puede decir. Y un dólar que la fuente no trajo se nombra en `faltan`, no se
    estima con los otros."""
    from api.services import argy
    from asistente.agentes import dolares as D

    filas = [
        {"label": "DOLAR MEP", "value": 1531.0, "ret_day": 0.42, "ts": "2026-09-15T15:18:00", "source": "live"},
        {"label": "DOLAR CCL", "value": 1593.0, "ret_day": 0.51, "ts": "2026-09-15T15:18:00", "source": "live"},
        {"label": "DOLAR OFICIAL", "value": 1506.5, "ret_day": -0.10, "ts": None, "source": "mae"},
        {"label": "CANJE", "value": 4.09, "ret_day": None},
        {"label": "CAUCION ARS", "value": 19.98, "ret_day": None},
    ]
    with patch.object(argy, "get_argy_with_returns", return_value=filas):
        r = D.tipos_de_cambio()
    por = {d["nombre"]: d for d in r["dolares"]}
    assert set(por) == {"mep", "ccl", "oficial"}, "los tres, cada uno con su nombre"
    assert (por["mep"]["valor"], por["ccl"]["valor"], por["oficial"]["valor"]) == (1531.0, 1593.0, 1506.5)
    assert por["mep"]["variacion_dia_pct"] == 0.42 and por["oficial"]["variacion_dia_pct"] == -0.10
    assert r["canje_pct"] == 4.09 and r["faltan"] == []
    assert r["brecha_mep_oficial_pct"] == 1.63 and r["brecha_ccl_oficial_pct"] == 5.74
    assert r["_tabla"]["campo"] == "dolares"
    # un dólar sin valor se declara faltante y no se estima
    with patch.object(argy, "get_argy_with_returns", return_value=[f for f in filas
                                                                  if f["label"] != "DOLAR OFICIAL"]):
        r2 = D.tipos_de_cambio()
    assert r2["faltan"] == ["oficial"] and r2["brecha_mep_oficial_pct"] is None
    assert {d["nombre"] for d in r2["dolares"]} == {"mep", "ccl"}
    # la fuente caída vuelve como dato, nunca como excepción
    with patch.object(argy, "get_argy_with_returns", side_effect=RuntimeError("caída")):
        assert "error" in D.tipos_de_cambio()


def test_la_curva_lee_la_vista_de_curvas_y_ordena_de_verdad():
    from datetime import date

    from api.services import curvas_vista as CV
    from asistente.agentes import renta_fija as MM

    def bono(i, tea, pill="cer", ruido=False):
        return {"ticker_corto": f"T{i}", "pill": pill, "emisor": "Tesoro", "vencimiento": "2027-01-01",
                "tasa_ruido": ruido,
                "metrics": {"last_price": 100.0 + i, "TEA": tea, "TEM": 0.0254,
                            "paridad": 98.7, "duration": 1.234 + i, "total_nominals": 1000 * i}}

    vista = {"bonos": [bono(i, 0.10 + i / 100) for i in range(60)]
             + [bono(99, 9.99, ruido=True), bono(98, None), bono(97, 0.5, pill="tasa_fija")]}
    with patch.object(CV, "get_curvas_vista", return_value=vista), \
         patch("asistente.agentes.renta_fija.date", wraps=date) as d:
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
    from asistente.agentes import renta_fija as MM

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
        m = modelos.resolver("asistente_renta_fija")
        assert (m.proveedor, m.datos_negocio) == (modelos.PROVEEDOR_DEFAULT, False)
        assert not t.elegido
    with patch.object(modelos, "ajustes", return_value={"tarea:asistente_renta_fija": "openai/gpt-x"}):
        e = modelos.resolver("asistente_renta_fija")
        assert (e.proveedor, e.modelo, e.elegido) == ("openai", "gpt-x", True)
    with patch.object(modelos, "ajustes", return_value={"tarea:asistente_renta_fija": "nadie/x"}):
        assert not modelos.resolver("asistente_renta_fija").elegido, "elección inválida → default"
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
    """Un ChatModel falso: el ruteo contesta `ruteo`; un agente con
    herramientas pide la primera con argumentos razonables y después redacta."""

    def __init__(self, ruteo: str, tarea: str, esquema: dict | None, pide: str | None = None):
        self.ruteo, self.tarea, self.esquema, self.tools = ruteo, tarea, esquema, []
        # Qué herramienta pide (por nombre); sin esto, la primera del agente.
        self.pide = pide

    def bind_tools(self, tools):
        self.tools = list(tools)
        return self

    def invoke(self, mensajes):
        from langchain_core.messages import AIMessage

        ultimo = mensajes[-1]
        uso = lambda i, o: {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}  # noqa: E731
        if self.tarea == "asistente_ruteo":
            return AIMessage(content=self.ruteo, usage_metadata=uso(50, 3))
        if ultimo.type == "human" and self.tools and not ultimo.content.startswith("Pregunta:"):
            from langchain_core.utils.function_calling import convert_to_openai_tool

            fs = [convert_to_openai_tool(x)["function"] for x in self.tools]
            f = next((x for x in fs if x["name"] == self.pide), fs[0])
            props = f["parameters"]["properties"]
            args = ({"cuenta": "805"} if "cuenta" in props else {"ticker": "al30"} if "ticker" in props
                    else {"curva": "cer"})
            return AIMessage(content="", usage_metadata=uso(900, 20),
                             tool_calls=[{"name": f["name"], "args": args, "id": "c1", "type": "tool_call"}])
        dicho = "TX28 rinde 11.0" if self.tarea == "asistente_renta_fija" else "total 3926.8"
        return AIMessage(content=json.dumps({"respuesta": f"[{self.tarea}] {dicho}", "falta": None}),
                         usage_metadata=uso(1200, 40))


def _proveedor(ruteo: str, pide: str | None = None):
    """Reemplaza `modelos.modelo`: devuelve el ChatModel falso de esa tarea."""
    def fake(tarea, *, usuario=None, sesion=None, esquema=None, traza=None):
        if traza is not None:
            traza.ids.append(1)
        return _Falso(ruteo, tarea, esquema, pide)

    return fake


_TOOLS = {
    "cobros_futuros": lambda **a: {"total": {"USD": 3926.8}},
    "tenencia_actual": lambda **a: {"total": 3926.8, "posiciones": [], "_tabla": {"campo": "posiciones"}},
    "curva": lambda **a: {"instrumentos": [{"ticker": "TX28", "tea_pct": 11.0}], "cuantos": 1},
    "ficha_bono": lambda **a: {"ticker": "AL30"},
    "ficha_cliente": lambda **a: {"cuenta": a["cuenta"], "titular": {"denominacion": "NOMBRE 805"}},
    "tipos_de_cambio": lambda **a: {"dolares": [{"nombre": "mep", "valor": 1531.0}]},
}


def _correr(pregunta, ruteo="renta_fija", pide=None, **kw):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", _proveedor(ruteo, pide)), \
         patch.object(grafo, "_ejecutar", lambda ag, n, a: _TOOLS[n](**a) if n in ag.por_nombre
                      else {"error": "no existe"}):
        return grafo.preguntar(pregunta, usuario="t", **kw)


def test_una_pregunta_de_un_agente_corre_solo_ese_agente(permiso):
    r = _correr("¿qué es el AL30?", ruteo="renta_fija")
    assert r["agentes"] == ["renta_fija"] and r["error"] is None
    assert r["respuesta"].startswith("[asistente_renta_fija]")
    tipos = [(e["tipo"], e.get("agente")) for e in r["eventos"]]
    assert tipos == [("pregunta", None), ("ruteo", "ruteo"), ("vuelta", "renta_fija"),
                     ("pide", "renta_fija"), ("resultado", "renta_fija"), ("vuelta", "renta_fija"),
                     ("texto", "renta_fija")]
    ruteo = next(e for e in r["eventos"] if e["tipo"] == "ruteo")
    assert ruteo["elegidos"] == ["renta_fija"] and ruteo["motivo"]
    assert [m["role"] for m in r["mensajes"]] == ["user", "assistant", "tool", "assistant"]
    assert r["estado"] == {}, "el mercado no deja nada en foco"
    assert r["vueltas"] == 3 and r["tokens_in"] == 50 + 900 + 1200 and r["llamadas"] == [1, 1, 1]
    assert r["control"]["ok"] is True, "11.0 y TX28 están en los datos del mercado"


def test_el_agente_cuenta_aprende_el_foco_y_la_sesion_se_conserva(permiso):
    r = _correr("¿cuánto cobro de la 805?", ruteo="cartera", sesion="a" * 32)
    assert r["agentes"] == ["cartera"] and r["estado"] == {"cuenta": "805"} and r["sesion"] == "a" * 32
    assert any(e["tipo"] == "estado" and e["estado"] == {"cuenta": "805"} for e in r["eventos"])
    assert r["control"]["ok"], "el total está en los datos"
    r2 = _correr("¿y en dólares?", ruteo="cartera", historial=r["mensajes"],
                 estado=r["estado"], sesion=r["sesion"])
    assert r2["sesion"] == r["sesion"] and r2["estado"] == {"cuenta": "805"}
    assert [m["role"] for m in r2["mensajes"]][:4] == ["user", "assistant", "tool", "assistant"]
    assert len(r2["mensajes"]) == 8


def test_una_pregunta_cruzada_corre_los_dos_agentes_y_la_junta_redacta(permiso):
    r = _correr("¿qué bono CER rinde más que los que tengo?", ruteo="cartera, renta_fija")
    assert r["agentes"] == ["cartera", "renta_fija"] and r["error"] is None
    assert r["respuesta"].startswith("[asistente_cartera]"), "la junta corre con la tarea de cartera"
    agentes = [e.get("agente") for e in r["eventos"] if e["tipo"] != "estado"]
    assert "junta" in agentes and agentes.count("cartera") == agentes.count("renta_fija")
    roles = [m["role"] for m in r["mensajes"]]
    assert roles == ["user", "assistant", "tool", "assistant", "assistant", "tool", "assistant", "assistant"]
    assert roles.count("user") == 1, "la entrada de la junta no va al historial"
    # 5 y no 6: la pregunta la despachó una regla, sin llamar al modelo.
    assert r["vueltas"] == 5 and r["estado"] == {"cuenta": "805"}
    assert next(e for e in r["eventos"] if e["tipo"] == "ruteo")["motivo"].startswith("regla")


def test_cada_agente_ve_del_historial_solo_lo_suyo(permiso):
    """Lo que trajo el agente cartera (tenencias, plata) no puede llegar al
    proveedor del agente mercado en la pregunta siguiente."""
    from asistente import grafo, memoria
    from core import modelos

    r = _correr("¿qué bono CER rinde más que los que tengo?", ruteo="cartera, renta_fija")
    assert {m.get("agente") for m in r["mensajes"]} == {None, "cartera", "renta_fija"}
    assert [m["agente"] for m in r["mensajes"] if m["role"] == "tool"] == ["cartera", "renta_fija"]
    assert r["mensajes"][-1]["agente"] == "cartera", "la junta queda como cuenta"

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
    primera_mercado = visto["asistente_renta_fija"][0]
    assert "3926.8" not in json.dumps(primera_mercado), "la tenencia viajó al mercado"
    assert all("agente" not in m for m in primera_mercado), "la marca no viaja al proveedor"
    assert [m["role"] for m in primera_mercado] == ["system", "user", "assistant", "tool",
                                                    "assistant", "user"]
    primera_cuenta = visto["asistente_cartera"][0]
    assert "3926.8" in json.dumps(primera_cuenta) and "TX28" not in json.dumps(primera_cuenta)


def test_si_el_ruteo_no_se_entiende_van_todos_los_agentes(permiso):
    from asistente.agentes import AGENTES

    for raro in ("no sé", "no hace falta la cuenta, alcanza con mercado", ""):
        r = _correr("hola", ruteo=raro)
        assert r["agentes"] == list(AGENTES), raro
        assert "van todos" in next(e for e in r["eventos"] if e["tipo"] == "ruteo")["motivo"]
    assert _correr("hola", ruteo="Renta_fija.")["agentes"] == ["renta_fija"]
    assert _correr("hola", ruteo="cartera y renta_fija")["agentes"] == ["cartera", "renta_fija"]


def test_al_tope_de_vueltas_ningun_pedido_queda_sin_su_tool(permiso):
    """Si el modelo pide herramientas sin parar, se corta; pero cada pedido
    pendiente se cierra con un `tool` de error: un `assistant` con `tool_calls`
    sin respuesta rompe ese agente en todas las preguntas siguientes."""
    from langchain_core.messages import AIMessage

    from asistente import grafo
    from core import modelos

    class Insistente(_Falso):
        def invoke(self, mensajes):
            if self.tarea == "asistente_ruteo":
                return AIMessage(content="renta_fija")
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
    from asistente.agentes import AGENTES

    assert "error" in grafo._ejecutar(AGENTES["renta_fija"], "cobros_futuros", {"cuenta": "805"})
    assert "error" in grafo._ejecutar(AGENTES["cartera"], "cobros_futuros", None)
    assert "999" in grafo._ejecutar(AGENTES["cartera"], "cobros_futuros", {"cuenta": "999"})["error"]
    def rota(curva: str) -> dict:
        """Revienta."""
        return 1 / 0

    roto = Agente(nombre="x", tarea=AGENTES["renta_fija"].tarea, describe="", instruccion=lambda f: "",
                     herramientas=(rota,))
    assert "ZeroDivisionError" in grafo._ejecutar(roto, "rota", {"curva": "cer"})["error"]


def test_sin_clave_no_rompe_y_deja_la_sesion(permiso):
    from asistente import grafo
    from asistente.agentes import AGENTES
    from core import modelos

    with patch.object(modelos, "configurado", return_value=False):
        r = grafo.preguntar("hola", usuario="t", sesion="b" * 32)
    assert r["error"] and r["respuesta"] is None and r["sesion"] == "b" * 32
    assert r["agentes"] == list(AGENTES), "sin ruteo, van todos"


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


def _conversar(base, pregunta, sesion=None, ruteo="cartera", usuario="t"):
    from asistente import grafo, panel, sesiones
    from core import modelos

    with patch.object(sesiones, "cargar", base.cargar), patch.object(sesiones, "guardar", base.guardar), \
         patch.object(panel, "conversacion", lambda s: {"id": s, "llamadas": 2}), \
         patch.object(modelos, "modelo", _proveedor(ruteo)), \
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
    assert fila["turnos"][0]["agentes"] == ["cartera"] and fila["turnos"][0]["respuesta"]

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


# ── historial por agente, esquema y herramientas ─────────────────────────────


def test_una_pregunta_de_un_solo_agente_no_le_llega_al_otro_despues(permiso):
    """«¿la tenencia de la 805?» fue solo a cuenta. En la pregunta siguiente,
    mercado no la recibe: sin este filtro la tomaba como pendiente y salía a
    buscar la ficha del bono «805»."""
    from asistente import memoria

    r = _correr("¿la tenencia de la 805?", ruteo="cartera")
    pregunta = r["mensajes"][0]
    assert pregunta["role"] == "user" and pregunta["agentes"] == ["cartera"]
    assert memoria.de_agente(r["mensajes"], "renta_fija") == []
    assert [m["role"] for m in memoria.de_agente(r["mensajes"], "cartera")][:2] == ["user", "assistant"]
    # Una pregunta sin marca (historial viejo) la ven todos.
    assert memoria.de_agente([{"role": "user", "content": "x"}], "renta_fija") == [{"role": "user", "content": "x"}]
    assert all(k not in memoria.a_dicts(memoria.desde_dicts(r["mensajes"]))[0] for k in memoria.MARCAS)


def test_un_agente_que_no_pudo_contestar_cierra_su_turno(permiso):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", side_effect=modelos.SinClave("falta OPENAI_API_KEY")):
        r = grafo.preguntar("¿qué tengo?", usuario="t")
    cierres = [m for m in r["mensajes"] if m["role"] == "assistant"]
    assert cierres and all(m["content"].startswith("No pude contestar") for m in cierres)


def test_leer_entiende_el_renglon_falta_y_la_instruccion_lo_pide():
    from asistente import esquema as ESQ
    from asistente.agentes import AGENTES

    assert ESQ.leer("Tenés 2 bonos.\nFalta: los cupones, la herramienta falló") == \
        {"respuesta": "Tenés 2 bonos.", "falta": "los cupones, la herramienta falló"}
    assert ESQ.leer("Falta: todo") == {"respuesta": None, "falta": "todo"}
    assert ESQ.leer("no falta nada") == {"respuesta": "no falta nada", "falta": None}
    # Solo el último renglón: un «Falta:» en el medio es texto y no se come lo de abajo.
    medio = "Tenés AL30.\nFalta: el precio.\nY te falta abonar el cupón de mayo."
    assert ESQ.leer(medio) == {"respuesta": medio, "falta": None}
    assert ESQ.leer("Hola.\n\nFalta: x\n\n") == {"respuesta": "Hola.", "falta": "x"}
    assert ESQ.FALTA_MARCA in AGENTES["cartera"].instruccion({}) and ESQ.FALTA_MARCA in AGENTES["renta_fija"].instruccion({})


def test_con_herramientas_nunca_viaja_el_esquema_y_sin_ellas_si():
    """OpenAI con `response_format` exige herramientas `strict` y las nuestras
    no lo son (medido: «Only `strict` function tools can be auto-parsed»)."""
    from asistente import esquema as ESQ
    from asistente import grafo
    from asistente import junta as JU
    from asistente import ruteo as RUT
    from asistente.agentes import AGENTES
    from core import modelos

    pedidos = []

    def fake(tarea, *, usuario=None, sesion=None, esquema=None, traza=None):
        pedidos.append((tarea, esquema))
        return _Falso("cuenta", tarea, esquema)

    with patch.object(modelos, "modelo", fake):
        grafo._modelo_de(AGENTES["cartera"], {}, None, esquema=ESQ.FORMATO)
        grafo._modelo(JU.TAREA, {}, None, esquema=ESQ.FORMATO)
        grafo._modelo(RUT.TAREA, {}, None)
    assert pedidos == [("asistente_cartera", None), ("asistente_cartera", ESQ.FORMATO),
                       ("asistente_ruteo", None)]


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
        assert modelos.ficha_de("asistente_ruteo")["usa_herramientas"] is False


def test_cada_tarea_del_asistente_dice_para_que_es():
    from core import modelos

    with patch.object(modelos, "ajustes", return_value={}):
        for t in modelos.tareas():
            assert modelos.ficha_de(t)["para_que"]


# ── el registro y el ruteo por reglas ────────────────────────────────────


def test_todo_agente_esta_registrado_y_declarado():
    """Un archivo en `asistente/agentes/` es un agente: tiene que estar en
    `AGENTES`, con tarea en `core/modelos.TAREAS`, señales y herramientas."""
    import importlib

    from asistente.agentes import AGENTES
    from core import modelos

    archivos = sorted(p.stem for p in (RAIZ / "asistente" / "agentes").glob("*.py") if p.stem != "__init__")
    assert archivos, "no hay agentes"
    for nombre in archivos:
        mod = importlib.import_module(f"asistente.agentes.{nombre}")
        assert hasattr(mod, "AGENTE"), f"asistente/agentes/{nombre}.py no declara AGENTE"
        assert AGENTES.get(mod.AGENTE.nombre) is mod.AGENTE, f"{nombre} no está en AGENTES"
        assert mod.AGENTE.tarea in modelos.TAREAS, f"{nombre}: tarea sin declarar"
        assert mod.AGENTE.senales, f"{nombre}: sin señales, el ruteo no lo encontraría"
    assert set(AGENTES) == set(archivos)


def test_las_reglas_del_ruteo_deciden_lo_obvio_y_dejan_el_resto_al_modelo(permiso):
    from asistente import ruteo as R
    from asistente.agentes import AGENTES

    assert R.por_reglas("hola, ¿cómo va?") is None
    meta = R.por_reglas("¿Qué sabés hacer?")
    assert meta.tipo == "contesta" and meta.agentes == ()
    assert "cobros_futuros" in meta.respuesta and "curva" in meta.respuesta
    assert R.por_reglas("cuál es la tenencia de la 805").agentes == ("cartera",)
    assert R.por_reglas("quién es el titular de la 805").agentes == ("cliente",)
    assert R.por_reglas("qué se operó hoy en AL30").agentes == ("operaciones",)
    # Sin señales, aunque nombre una cuenta, no decide ninguna regla: va el modelo.
    assert R.por_reglas("¿y en dólares?") is None
    assert R.por_reglas("dame la 805") is None
    assert "cuenta = 805" in R.instruccion({"cuenta": "805"})
    assert R.por_reglas("qué bonos CER conocés").agentes == ("renta_fija",)
    cruzada = R.por_reglas("qué bono CER rinde más que los que tengo en la 805")
    assert cruzada.tipo == "van" and cruzada.agentes == ("cartera", "renta_fija")
    assert cruzada.motivo.startswith("regla: señales")
    # «rinde» es de la familia mercado, no de un agente: el modelo elige entre los seis.
    fam = R.por_reglas("cuánto rinde el TX28")
    assert fam.tipo == "elige_el_modelo"
    assert fam.agentes == tuple(n for n, a in AGENTES.items() if a.familia == "mercado")
    assert "cartera" not in R.instruccion({}, fam.agentes) and "renta_fija" in R.instruccion({}, fam.agentes)
    assert R.leer_eleccion("renta_fija", fam.agentes) == ["renta_fija"]
    assert R.leer_eleccion("cartera", fam.agentes) == [], "un candidato de afuera no cuenta"
    assert R.por_reglas("cómo cotiza YPF").tipo == "elige_el_modelo"
    assert R.por_reglas("a cuánto está el MEP").agentes == ("dolares",)
    assert R.por_reglas("cuánto rinde la caución a 7 días").agentes == ("financiamiento",)
    assert R.por_reglas("quién atiende la 805 y qué tenencia tiene").agentes == ("cartera", "cliente")
    # Palabra entera, no raíz: «cerca» y «cerrá» no son CER; «tealdi» no es TEA.
    assert R.por_reglas("cerrá la posición").agentes == ("cartera",)
    assert R.por_reglas("qué hay cerca del vencimiento").tipo == "elige_el_modelo"
    assert R.por_reglas("qué tasa tiene la ON de tealdi").motivo == "regla: señales (renta_fija: on)"
    assert R.por_reglas("tenés alguna ON?").agentes == ("renta_fija",)
    # Meta es la pregunta entera, no una palabra adentro.
    assert R.por_reglas("hola, ¿qué sabés hacer vos?").tipo == "contesta"
    assert R.por_reglas("necesito ayuda con la 805") is None
    assert R.leer_eleccion("cartera, renta_fija") == ["cartera", "renta_fija"]
    assert R.leer_eleccion("no hace falta la cuenta") == []


def test_el_eval_de_ruteo_es_valido_y_ninguna_pregunta_pierde_un_agente(permiso):
    """El eval (`evals/ruteo.yaml`) son preguntas REALES de la mesa con los
    agentes que tienen que atenderlas. Acá corre su capa 1, sin proveedor: es
    gratis y corta la regresión en CI. El informe completo, con el modelo de la
    capa 2, sale de `python -m scripts.eval_ruteo`."""
    from scripts.eval_ruteo import ARCHIVO, cargar, correr

    filas = cargar(ARCHIVO)          # valida que todo agente nombrado exista
    assert len(filas) >= 20, "un eval con menos de 20 preguntas no mide nada"
    perdidas = [s for s in correr(filas, usar_modelo=False) if s["faltan"]]
    assert not perdidas, "\n".join(
        f"«{s['q']}» no llega a {', '.join(s['faltan'])} ({s['motivo']})" for s in perdidas)


def test_una_regla_que_contesta_no_llama_a_ningun_modelo(permiso):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", side_effect=AssertionError("no tenía que llamar")):
        r = grafo.preguntar("¿qué sabés hacer?", usuario="t")
    assert r["agentes"] == [] and "cuenta" in r["respuesta"] and r["error"] is None
    assert r["tokens_in"] == 0 and [e["tipo"] for e in r["eventos"]] == ["pregunta", "ruteo", "texto"]
    assert r["mensajes"][-1] == {"role": "assistant", "content": r["respuesta"], "agente": "ruteo"}
    assert r["mensajes"][0]["agentes"] == []
    # En el turno siguiente, ningún agente recibe esa pregunta: la contestó una regla.
    from asistente import memoria
    assert memoria.de_agente(r["mensajes"], "cartera") == [] and memoria.de_agente(r["mensajes"], "renta_fija") == []
    r2 = _correr("cuánto tengo en la 805", ruteo="cartera", historial=r["mensajes"], sesion=r["sesion"])
    assert [m["role"] for m in memoria.de_agente(r2["mensajes"], "cartera")][:2] == ["user", "assistant"]


def test_una_pregunta_sin_senales_va_al_modelo_de_ruteo(permiso):
    r = _correr("hola, ¿cómo va?", ruteo="renta_fija")
    ev = next(e for e in r["eventos"] if e["tipo"] == "ruteo")
    assert ev["motivo"] == "eligió el modelo" and r["agentes"] == ["renta_fija"]


# ── lo que salió de la primera conversación real ────────────────────────────


def test_cobros_acepta_una_fecha_limite_y_la_pisa_sobre_dias(permiso):
    from datetime import date

    from asistente.agentes import cartera as MC

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
    from asistente.agentes import cartera as MC

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
    from asistente.agentes import AGENTES

    s = AGT.sistema(AGENTES["renta_fija"].instruccion, {})
    assert s.rstrip().endswith(f"Hoy es {date.today().isoformat()}.") and "sin asteriscos" in s


def test_un_turno_guardado_lleva_las_tablas_que_declararon_las_herramientas():
    from asistente import sesiones

    eventos = [{"tipo": "pide"}, {"tipo": "resultado", "resultado": {
        "total": 9, "posiciones": [{"a": 1}] * 3, "_tabla": {"campo": "posiciones", "columnas": ["a"], "total": "total", "moneda": "ARS"}}},
        {"tipo": "resultado", "resultado": {"error": "x"}}, {"tipo": "resultado", "resultado": "texto"}]
    assert sesiones.tablas_de(eventos) == [{"titulo": "", "columnas": ["a"], "filas": [{"a": 1}] * 3,
                                            "cuantas": 3, "total": 9, "moneda": "ARS"}]


# ── cliente, operaciones y el dato personal ─────────────────────────────────


def test_la_ficha_del_cliente_recorta_el_documento_y_lleva_el_permiso(permiso):
    from datetime import date

    from asistente.agentes import cliente as MCL

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
    fuente = (RAIZ / "asistente" / "agentes" / "cliente.py").read_text(encoding="utf-8")
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
    # Por el camino real: la traza que arma el grafo para el agente cliente.
    from asistente import grafo
    from asistente.agentes import AGENTES
    tr = grafo._traza(AGENTES["cliente"].tarea, {"usuario": "u", "sesion": "s"})
    assert isinstance(tr, TR.Traza) and tr.guardar_texto is False
    assert grafo._traza(AGENTES["cartera"].tarea, {}).guardar_texto is True
    cur = MagicMock()
    cur.fetchone.return_value = (7,)
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cur
    with patch("core.postgres.get_pool", return_value=pool):
        tr._escribir(ok=True, error=None, tokens_in=1, tokens_out=1, cache_hit=None, cache_miss=None,
                     latencia_ms=1, detalle="quién es la 805", respuesta="Es Fulano", modelo="gpt-x")
    escrito = cur.execute.call_args.args[1]
    assert escrito[8] is None and escrito[9] is None, "ni pedido ni respuesta en el libro"


def test_un_agente_sin_herramientas_no_llama_al_modelo_y_lo_dice(permiso):
    from asistente import grafo
    from core import modelos

    with patch.object(modelos, "modelo", side_effect=AssertionError("no tenía que llamar")):
        r = grafo.preguntar("qué se operó hoy", usuario="t")
    assert r["agentes"] == ["operaciones"] and r["respuesta"] is None
    assert "Todavía no puedo consultar operaciones" in r["error"]
    assert r["mensajes"][-1]["agente"] == "operaciones" and r["tokens_in"] == 0


# ── la familia mercado y el foco por ticker ─────────────────────────────────


def test_la_familia_mercado_agrupa_a_sus_agentes_y_el_modelo_elige_entre_ellos(permiso):
    from asistente import grafo
    from asistente import ruteo as R
    from asistente.agentes import AGENTES, FAMILIAS, presentacion
    from core import modelos

    mercado = [n for n, a in AGENTES.items() if a.familia == "mercado"]
    assert mercado == ["renta_fija", "renta_variable", "fondos", "derivados", "financiamiento", "dolares"]
    assert set(FAMILIAS) == {"mercado"} and "rinde" in FAMILIAS["mercado"].senales
    for n in mercado:
        assert not (set(AGENTES[n].senales) & set(FAMILIAS["mercado"].senales)), f"{n} repite una señal genérica"
    assert "[mercado]" in presentacion() or "mercado:" in presentacion()
    assert R.por_reglas("cómo cotiza YPF").agentes == tuple(mercado)
    # Con «cotiza» sola, el ruteo llama al modelo con los candidatos acotados.
    visto = {}

    def fake(tarea, *, usuario=None, sesion=None, esquema=None, traza=None):
        assert tarea == "asistente_ruteo"
        m = _Falso("renta_variable", tarea, esquema)
        original = m.invoke

        def invoke(mensajes):
            visto["system"] = mensajes[0].content
            return original(mensajes)
        m.invoke = invoke
        return m

    with patch.object(modelos, "modelo", fake):
        r = grafo.preguntar("¿cómo cotiza YPF?", usuario="t")
    assert r["agentes"] == ["renta_variable"] and "cartera" not in visto["system"]
    ev = next(e for e in r["eventos"] if e["tipo"] == "ruteo")
    assert ev["motivo"].startswith("regla: familia mercado") and ev["motivo"].endswith("eligió el modelo")


def test_el_ticker_queda_en_foco_y_lo_leen_los_agentes_que_lo_declaran(permiso):
    from asistente import estado as EST
    from asistente.agentes import AGENTES

    assert EST.sanear({"ticker": " al30 ", "cuenta": "805"}) == {"cuenta": "805", "ticker": "AL30"}
    assert EST.sanear({"ticker": "no-es; un ticker"}) == {}
    assert EST.aprender({}, {"ticker": "tx26"}, {"ok": 1}, claves=("ticker",)) == {"ticker": "TX26"}
    assert EST.aprender({}, {"ticker": "tx26"}, {"ok": 1}, claves=("cuenta",)) == {}
    assert AGENTES["operaciones"].foco == ("cuenta", "ticker") and AGENTES["dolares"].foco == ()
    r = _correr("¿qué es el AL30?", ruteo="renta_fija", pide="ficha_bono")
    assert r["agentes"] == ["renta_fija"] and r["estado"] == {"ticker": "AL30"}
