"""El alta de CEDEARs del AV AGENT (`docs/AGENT.md` §0.dl).

Lo que se congela: que la identidad sea la FICHA y no el nombre, que la
calibración salga de lo que ya tenemos, que sin ficha no se afirme nada, que
lo que manda el navegador se re-verifique, y que el motor sume sin reiniciar.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agente import alta_cedear, arreglos, catalogo, tipos
from agente.detectores import mercado

RAIZ = Path(__file__).resolve().parents[2]


def _sim(tc: str, plazo: str = "24hs") -> str:
    return f"MERV - XMEV - {tc} - {plazo}"


def _master(*tcs: str, activo: bool = True) -> list[dict]:
    return [{"ticker": _sim(t), "ticker_corto": t, "underlying": t, "activo": activo,
             "sin_cedear": False} for t in tcs]


def _ficha(tc: str, cfi: str = "EMXXXX", moneda: str = "ARS", plazo: str = "24hs") -> dict:
    return {"simbolo": _sim(tc, plazo), "cficode": cfi, "moneda": moneda,
            "subyacente": tc, "segmento": "MERV", "vencimiento": ""}


PROPIOS = ("AAPL", "MSFT", "NVDA", "AMZN")


def test_la_ficha_se_calibra_con_lo_que_ya_tenemos():
    """El cficode de un CEDEAR NO está escrito en ningún lado: se lee de los
    que ya tenemos. Los que Primary lista con ESA ficha y no están son los
    candidatos; GGAL (otra ficha) no lo es aunque tenga la misma forma."""
    fichas = [_ficha(t) for t in PROPIOS] + [_ficha("META"), _ficha("GOOGL"),
                                              _ficha("GGAL", cfi="ESXXXX")]
    c = alta_cedear.candidatos(_master(*PROPIOS), fichas)
    assert c["cficodes"] == ["EMXXXX"]
    assert c["plazos"] == ["24hs"] and c["monedas"] == ["ARS"]
    assert [f["unidad"] for f in c["filas"]] == ["GOOGL", "META"]
    assert c["reconocidos"] == 4 and c["propios"] == 4


def test_otra_pata_del_mismo_cedear_no_es_un_candidato():
    """`NVDAD` (dólar MEP) o el plazo CI comparten cficode con la pata en pesos:
    la calibración por moneda y plazo los deja afuera. Sin eso, cada CEDEAR
    que tenemos aparecería como «faltante» por sus propias patas."""
    fichas = [_ficha(t) for t in PROPIOS] + [
        _ficha("NVDAD", moneda="USD"), _ficha("NVDA", plazo="CI"), _ficha("META")]
    c = alta_cedear.candidatos(_master(*PROPIOS), fichas)
    assert [f["unidad"] for f in c["filas"]] == ["META"]


def test_sin_ficha_reconocida_no_se_afirma_nada():
    """Menos de `min_propios` con la misma ficha = no hay calibración. El
    detector levanta `SinDatos`: «no reconocí la ficha» no es «no falta nada»
    (invariante 1)."""
    c = alta_cedear.candidatos(_master("AAPL"), [_ficha("AAPL"), _ficha("META")])
    assert c["cficodes"] == [] and c["filas"] == []


def test_el_detector_levanta_sin_datos_cuando_no_puede_mirar(monkeypatch):
    from agente import fuentes
    monkeypatch.setattr(fuentes, "cedears_master", lambda: _master(*PROPIOS))
    monkeypatch.setattr(fuentes, "fichas_primary", lambda: None)
    with pytest.raises(tipos.SinDatos):
        mercado.cedear_faltante({})
    monkeypatch.setattr(fuentes, "fichas_primary", lambda: [_ficha("AAPL")])
    with pytest.raises(tipos.SinDatos):
        mercado.cedear_faltante({"min_propios": 3})


def test_un_hallazgo_por_familia_y_uno_por_cada_activo_que_no_cotiza(monkeypatch):
    """300 CEDEARs que la mesa no pidió son UN hallazgo (se tilda en ENCONTRÓ).
    Un activo nuestro que Primary NO lista es un problema por título: cada uno
    se corrige o se apaga por separado, y no tiene botón a propósito."""
    from agente import fuentes
    ya_descartado = {"ticker": _sim("DESCARTADO"), "ticker_corto": "DESCARTADO",
                     "underlying": "DESCARTADO", "activo": False,
                     "sin_cedear": False, "data": {"descartado": True}}
    master = _master(*PROPIOS) + _master("FANTASMA") + [ya_descartado]
    fichas = [_ficha(t) for t in PROPIOS] + [_ficha("META"), _ficha("GOOGL")]
    monkeypatch.setattr(fuentes, "cedears_master", lambda: master)
    monkeypatch.setattr(fuentes, "fichas_primary", lambda: fichas)
    monkeypatch.setattr(fuentes, "primary_fecha", lambda: None)
    hs = mercado.cedear_faltante({})
    familia = [h for h in hs if h.regla == "no_esta_en_master"]
    assert len(familia) == 1 and familia[0].sujeto == alta_cedear.FAMILIA
    assert familia[0].evidencia["cantidad"] == 2
    # Lo que YA se descartó por ticker (§0.eh) sigue sin ofrecerse, pero se
    # cuenta: el número del aviso no puede mentir sobre lo que hay atrás.
    assert familia[0].evidencia["ya_descartados"] == 1
    assert "1 ya descartado" in familia[0].problema
    muertos = [h for h in hs if h.regla == "no_cotiza_en_primary"]
    assert [h.sujeto for h in muertos] == ["FANTASMA"]
    h = catalogo.HABILIDADES["cedear_faltante"]
    assert h.arreglo_de("no_esta_en_master") == "alta_cedear"
    assert h.arreglo_de("no_cotiza_en_primary") == ""


def test_el_detector_no_lee_el_catalogo_de_primary_por_su_cuenta():
    """REGLA #9: un solo lector de la foto (`core/instrumentos_validos`)."""
    src = inspect.getsource(mercado.cedear_faltante) + inspect.getsource(alta_cedear)
    assert "pyrofex_instruments" not in src
    assert "fichas_primary" in inspect.getsource(mercado.cedear_faltante)


def test_no_se_escribe_lo_que_manda_el_navegador(monkeypatch):
    """Cada ticker tildado se vuelve a simular: si Primary no lo lista, no se
    escribe — y no frena a los demás."""
    escritos = []

    def _simular(tc, und=None):
        ok = tc != "NOEXISTE"
        return {"ok": True, "ticker_corto": tc, "simbolo": _sim(tc), "underlying": und or tc,
                "puede_aplicar": ok, "veredicto": "ok" if ok else "NO: Primary no lo lista",
                "foto_vieja": False, "existia": False, "pasos": []}

    from core import cedears_sql
    monkeypatch.setattr(alta_cedear, "simular", _simular)
    monkeypatch.setattr(cedears_sql, "alta", lambda tc, **kw: (escritos.append(tc) or {
        "antes": "no estaba", "despues": tc, "existia": False}))
    monkeypatch.setattr(alta_cedear.libro, "registrar", lambda **kw: None)
    import jobs.adr_live as adr
    import jobs.precios_acciones_daily as pad
    monkeypatch.setattr(pad, "backfill_ticker", lambda t: (10, None))
    monkeypatch.setattr(adr, "upsert_uno", lambda t: (True, f"{t} c=1"))

    r = alta_cedear.aplicar_varios([{"unidad": "meta", "valor": ""},
                                    {"unidad": "NOEXISTE"}, {"unidad": ""}], actor="t")
    assert escritos == ["META"]
    assert len(r["escritos"]) == 1 and r["escritos"][0]["velas"] == 10
    assert r["errores"] == ["NOEXISTE: NO: Primary no lo lista"]


def test_sin_confirmar_que_primary_lo_lista_no_se_aplica(monkeypatch):
    """`conocido=None` («no pude preguntar») NO habilita el alta: un CEDEAR que
    no cotiza es una fila que el motor pide y el WS filtra para siempre."""
    from agente import alta
    monkeypatch.setattr(alta, "estado_simbolo", lambda s: {"conocido": None, "nota": "?"})
    monkeypatch.setattr(alta_cedear, "listado", lambda: {"ok": False, "error": "sin foto"})
    monkeypatch.setattr(alta_cedear, "_motor", lambda: {"estado": "info", "detalle": ""})
    sim = alta_cedear.simular("META")
    assert sim["puede_aplicar"] is False and "no pude confirmar" in sim["veredicto"]


def test_el_arreglo_entra_por_la_puerta_unica_y_el_script_tambien():
    """`mercado.cedears` tiene UN alta: `core.cedears_sql.alta`. El agente y el
    script la usan; ninguno lleva su propio INSERT del CEDEAR con pata."""
    assert "cedears_sql.alta(" in inspect.getsource(alta_cedear.aplicar)
    script = (RAIZ / "scripts" / "add_cedear.py").read_text()
    assert "cedears_sql.alta(" in script
    a = arreglos.ARREGLOS["alta_cedear"]
    assert a.pide_datos and a.donde == "mercado.cedears" and not a.inmediato


def test_el_motor_relee_el_master_y_no_pide_reinicio():
    """Hasta el 2026-09-04 el alta terminaba con «⚠️ reiniciá el motor», que
    en rueda es cortarle el feed a la mesa. El motor suma lo nuevo solo, y el
    alta promete «en ≤ N s» con el MISMO número que usa el motor."""
    src = (RAIZ / "engines" / "motor_cedears.py").read_text()
    assert "def _master_watcher" in src and "agregar_suscripciones" in src
    assert "lanzar_hilo_vital(_master_watcher" in src
    assert "relee_master=True" in src
    from engines import motor_cedears
    assert motor_cedears.RELECTURA_MASTER_S == alta_cedear.RELECTURA_MOTOR_S
    assert "reiniciá motor_cedears" not in (RAIZ / "scripts" / "add_cedear.py").read_text()
