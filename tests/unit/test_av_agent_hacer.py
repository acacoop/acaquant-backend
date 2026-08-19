"""LO QUE EL AGENTE SABE HACER — `api/services/av_agent_hacer.py`.

Lo que congelan estos tests NO es que las reglas den un valor u otro: es el
CONTRATO de seguridad del ciclo, que es lo único que hace aprobable una
propuesta sin leerla entera.

  · el valor propuesto sale de una lista CERRADA (nada libre llega al catálogo);
  · el modelo NO decide nada que la regla ya haya resuelto;
  · `con_ia=False` no gasta un token, y ninguna acción puede saltearse el
    interruptor;
  · aplicar y VERIFICAR es un solo paso — y si la verificación no confirma, la
    propuesta queda `fallida`, jamás `aplicada`.
"""
from __future__ import annotations

import pytest

from api.services import av_agent_hacer as hacer


@pytest.fixture(autouse=True)
def _sin_modelo():
    """Ningún test toca el LLM. Un test que llame al modelo mide la red, no el
    código, y falla distinto según el día."""
    tok = hacer._SIN_IA.set(True)
    yield
    hacer._SIN_IA.reset(tok)


def _casos(*items):
    return [{"item": i} for i in items]


# ── El vocabulario cerrado ──────────────────────────────────────────────────

def test_toda_propuesta_de_cartera_esta_en_el_vocabulario():
    """Una cartera inventada rompe el divisor del AuM EN SILENCIO (el papel
    queda sin clasificar y la valuación se calcula con otra regla). Por eso el
    valor no puede ser libre nunca."""
    props = hacer.AccionCartera().proponer(_casos(
        "[42932] OTC SOJ. 2026", "TRI. MAIZ", "[1] DLR092026",
        "X29E7 LETRA CER", "[28902] CAFCI1910-6461 - Fondo", "[3] TX26 TAMAR"))
    assert props
    assert all(p.propuesto in hacer.CARTERAS for p in props)


def test_aplicar_rechaza_una_cartera_fuera_del_vocabulario():
    """La guarda está también en `aplicar`, no solo al proponer: entre proponer
    y aplicar hay una fila en la base que alguien podría editar."""
    p = hacer.Propuesta(sujeto="X", campo="CARTERA", propuesto="Derivados",
                        porque="")
    with pytest.raises(ValueError):
        hacer.AccionCartera().aplicar(p)


@pytest.mark.parametrize(("unidad", "esperado"), [
    ("[42932] OTC SOJ. 2026", "DERIVADOS"),
    ("[7] TRI. MAIZ DIC26", "DERIVADOS"),
    ("[1] DLR092026", "DERIVADOS"),
    ("[9] X29E7 - LETRA CER", "ARS"),
    ("[28902] CAFCI1910-6461 - Fondo - Clase B", "FCI"),
    ("[5] TX26 @TAMAR", "ARS"),
])
def test_los_patrones_que_marco_el_user(unidad, esperado):
    """*«OTC TRI DLR… son siempre derivados. El X29E7 tiene la palabra CER en el
    nombre»* — el user, y de ahí salieron las reglas."""
    props = hacer.AccionCartera().proponer(_casos(unidad))
    assert [p.propuesto for p in props] == [esperado]


def test_el_id_de_especie_no_tapa_el_patron():
    """Regresión: `[42932] OTC SOJ.` no matcheaba porque el patrón estaba
    anclado al principio de la UNIDAD y adelante va el id de Aunesa. Y ese id
    **cambia** cuando rebautizan el instrumento, así que no puede participar de
    ninguna decisión."""
    assert hacer._nombre("[42932]  OTC SOJ. 2026") == "OTC SOJ. 2026"
    assert hacer.AccionCartera().proponer(_casos("[42932] OTC SOJ."))


def test_girsa_no_es_un_contrato_de_camara():
    """`GIR.` vale como PREFIJO del nombre. Buscarlo en cualquier posición
    convertiría en derivado a cualquier emisor que lo tenga adentro."""
    assert hacer.AccionCartera().proponer(_casos("[7] ON BONO GIRSA SA")) == []


def test_lo_que_ninguna_regla_sabe_no_se_propone_a_ciegas():
    """Sin modelo disponible, un nombre que no matchea **no genera propuesta**.
    Inventar un valor plausible es peor que no proponer: alguien lo aprueba."""
    assert hacer.AccionCartera().proponer(_casos("ALGO QUE NO DICE NADA")) == []


# ── El interruptor del modelo ───────────────────────────────────────────────

def test_ninguna_accion_puede_saltearse_el_interruptor(monkeypatch):
    """El flag se chequea DENTRO de `_proponer_con_ia`, que es el único camino
    al modelo. Si cada acción tuviera que acordarse de mirarlo, la que se olvide
    gasta tokens con el modelo apagado y nadie se entera hasta la factura."""
    llamadas = []
    monkeypatch.setattr("core.ai.disponible",
                        lambda t: llamadas.append(t) or True)
    hacer._proponer_con_ia(["X"], campo="CARTERA", opciones=("ARS",), que="")
    assert llamadas == []          # ni siquiera preguntó si hay credencial


def test_el_modelo_no_puede_meter_un_valor_fuera_de_la_lista(monkeypatch):
    """La lista cerrada es la mitad del diseño: «Derivados» es plausible y está
    mal escrito, y eso rompe los filtros que comparan exacto sin dar error."""
    hacer._SIN_IA.set(False)      # este test SÍ mira la rama del modelo
    monkeypatch.setattr("core.ai.disponible", lambda t: True)
    monkeypatch.setattr("core.ai.completar", lambda *a, **k: (
        '[{"sujeto":"A","valor":"Derivados","porque":"x"},'
        ' {"sujeto":"A","valor":"DERIVADOS","porque":"ok"}]'))
    out = hacer._proponer_con_ia(["A"], campo="CARTERA",
                                 opciones=hacer.CARTERAS, que="")
    assert [p.propuesto for p in out] == ["DERIVADOS"]
    assert out[0].fuente == "ia"


def test_el_modelo_no_puede_inventar_un_sujeto(monkeypatch):
    """Si contesta sobre un asset que nadie le pasó, se descarta: la propuesta
    escribiría sobre una fila que no estaba en revisión."""
    hacer._SIN_IA.set(False)      # este test SÍ mira la rama del modelo
    monkeypatch.setattr("core.ai.disponible", lambda t: True)
    monkeypatch.setattr("core.ai.completar", lambda *a, **k:
                        '[{"sujeto":"OTRO","valor":"ARS","porque":"x"}]')
    assert hacer._proponer_con_ia(["A"], campo="CARTERA",
                                  opciones=hacer.CARTERAS, que="") == []


def test_una_respuesta_que_no_es_json_no_rompe_nada(monkeypatch):
    hacer._SIN_IA.set(False)      # este test SÍ mira la rama del modelo
    monkeypatch.setattr("core.ai.disponible", lambda t: True)
    monkeypatch.setattr("core.ai.completar", lambda *a, **k: "perdón, no sé")
    assert hacer._proponer_con_ia(["A"], campo="CARTERA",
                                  opciones=hacer.CARTERAS, que="") == []


# ── El FCI: copiar de un hermano es un hecho, no una inferencia ─────────────

def test_el_fci_se_completa_desde_su_hermano(monkeypatch):
    monkeypatch.setattr("api.services.assets_sql.assets_rows", lambda f: [
        {"unidad": "[1] CAFCI1781-6039 - Fondo - Clase A", "emisor": "BAVSA"},
        {"unidad": "[2] CAFCI1781-6040 - Fondo - Clase B", "emisor": ""},
    ])
    props = hacer.AccionFci().proponer(
        _casos("[2] CAFCI1781-6040 - Fondo - Clase B"))
    assert [(p.campo, p.propuesto) for p in props] == [("EMISOR", "BAVSA")]


def test_si_los_hermanos_no_coinciden_no_se_propone(monkeypatch):
    """Dos emisores distintos para el mismo código CAFCI es un dato ROTO, no una
    ambigüedad que se resuelva eligiendo uno. Elegir taparía el problema."""
    monkeypatch.setattr("api.services.assets_sql.assets_rows", lambda f: [
        {"unidad": "[1] CAFCI1781-1 - A", "emisor": "BAVSA"},
        {"unidad": "[2] CAFCI1781-2 - B", "emisor": "OTRO SA"},
        {"unidad": "[3] CAFCI1781-3 - C", "emisor": ""},
    ])
    assert hacer.AccionFci().proponer(_casos("[3] CAFCI1781-3 - C")) == []


# ── Avisar a una persona ────────────────────────────────────────────────────

def test_el_ping_es_uno_por_control_y_no_uno_por_caso():
    """200 pings de «esta cuenta no tiene nivel_1» no son 200 avisos: son un
    aviso ignorado."""
    props = hacer.AccionAvisar().proponer(_casos(*[str(i) for i in range(200)]))
    assert len(props) == 1
    assert props[0].extra["n"] == 200
    assert props[0].propuesto == ""      # el destinatario lo elige el humano


def test_no_se_avisa_a_alguien_que_no_es_usuario(monkeypatch):
    monkeypatch.setattr(hacer, "_usuario_existe", lambda e: False)
    p = hacer.Propuesta(sujeto="comitentes_sin_nivel1", campo="para",
                        propuesto="nadie@ejemplo.com", porque="")
    with pytest.raises(ValueError):
        hacer.AccionAvisar().aplicar(p)


def test_sin_destinatario_no_se_aplica():
    p = hacer.Propuesta(sujeto="comitentes_sin_nivel1", campo="para",
                        propuesto="", porque="")
    with pytest.raises(ValueError):
        hacer.AccionAvisar().aplicar(p)


# ── El ciclo: aplicar y verificar son UN paso ──────────────────────────────

class _AccionFalsa:
    id = "test.falsa"
    titulo = "t"
    sobre = "control_falso"
    campo = "CAMPO"
    donde = "ningún lado"

    def __init__(self, *, quedo=True, revienta=False):
        self.quedo, self.revienta, self.escrituras = quedo, revienta, []

    def proponer(self, casos):
        return []

    def aplicar(self, p):
        if self.revienta:
            raise RuntimeError("no se pudo escribir")
        self.escrituras.append(p.propuesto)

    def verificar(self, p):
        return self.quedo, "detalle"


def _fila(pid=1, propuesto="ARS"):
    return {"id": pid, "accion": "test.falsa", "sujeto": "X", "campo": "CAMPO",
            "antes": "", "propuesto": propuesto, "fuente": "regla",
            "porque": "", "extra": {}}


def test_verificar_que_no_confirma_deja_la_propuesta_fallida(monkeypatch):
    """**El invariante central.** Marcar `aplicada` algo que no se pudo
    comprobar es exactamente cómo un tablero termina en verde con el dato roto
    — el incidente que dio origen a SALUD."""
    sellos = {}
    a = _AccionFalsa(quedo=False)
    monkeypatch.setitem(hacer.ACCIONES, "test.falsa", a)
    monkeypatch.setattr(hacer, "_sellar",
                        lambda pid, **kw: sellos.update(kw))
    r = hacer._aplicar_una(_fila(), por="yo", valores={})
    assert r["ok"] is False
    assert sellos["estado"] == "fallida"
    assert sellos["verificado"] is False


def test_aplicar_verificado_sella_aplicada(monkeypatch):
    sellos = {}
    a = _AccionFalsa(quedo=True)
    monkeypatch.setitem(hacer.ACCIONES, "test.falsa", a)
    monkeypatch.setattr(hacer, "_sellar", lambda pid, **kw: sellos.update(kw))
    r = hacer._aplicar_una(_fila(), por="yo", valores={})
    assert r["ok"] is True
    assert sellos["estado"] == "aplicada" and sellos["verificado"] is True
    assert a.escrituras == ["ARS"]


def test_una_que_falla_no_arrastra_a_las_otras(monkeypatch):
    """Aprobar diez y que se caigan las diez porque la séptima tenía un dato
    roto es la forma más rápida de que nadie vuelva a apretar el botón."""
    monkeypatch.setitem(hacer.ACCIONES, "test.falsa", _AccionFalsa())
    monkeypatch.setitem(hacer.ACCIONES, "test.rota",
                        _AccionFalsa(revienta=True))
    monkeypatch.setattr(hacer, "_sellar", lambda pid, **kw: None)
    rota = {**_fila(2), "accion": "test.rota"}
    monkeypatch.setattr(hacer, "_filas", lambda *a, **k: [_fila(1), rota])
    r = hacer.aplicar([1, 2], por="yo")
    assert (r["aplicadas"], r["fallidas"]) == (1, 1)


def test_el_humano_puede_corregir_el_valor_antes_de_aplicar(monkeypatch):
    """*«O que yo escriba lo que tiene que hacer y él lo haga»*. Sin esto, ante
    una sugerencia casi buena solo queda descartarla e ir a Manager a mano."""
    a = _AccionFalsa()
    monkeypatch.setitem(hacer.ACCIONES, "test.falsa", a)
    monkeypatch.setattr(hacer, "_sellar", lambda pid, **kw: None)
    hacer._aplicar_una(_fila(7, "ARS"), por="yo", valores={"7": "HD"})
    assert a.escrituras == ["HD"]


def test_aplicar_sin_ids_no_escribe_nada():
    assert hacer.aplicar([])["ok"] is False
    assert hacer.rechazar([])["ok"] is False


# ── Proponer: sobre lo que pasa HOY, no sobre la foto de ayer ──────────────

def test_proponer_vuelve_a_correr_el_control(monkeypatch):
    """*«Todo lo que figura en encontró tiene que ser porque realmente está y
    sigue pasando»* — proponer sobre la última corrida del cron sería sugerir
    arreglos para casos que ya se resolvieron."""
    corridas = []
    monkeypatch.setattr(hacer, "_casos_frescos",
                        lambda cid: (corridas.append(cid) or ([], "")))
    monkeypatch.setattr(hacer, "_guardar", lambda a, p: len(p))
    monkeypatch.setattr(hacer, "pendientes", lambda a: [])
    hacer.proponer("assets.cartera")
    assert corridas == ["assets_sin_cartera"]


def test_proponer_una_accion_que_no_existe():
    assert hacer.proponer("no.existe")["ok"] is False


def test_el_resumen_mide_acierto_sobre_lo_DECIDIDO(monkeypatch):
    """Las pendientes no cuentan: todavía no dijeron nada sobre si el agente
    acierta. Meterlas en el denominador haría bajar la nota por proponer."""
    monkeypatch.setattr(hacer, "_filas", lambda *a, **k: [
        {"pendientes": 10, "aplicadas": 3, "rechazadas": 1, "fallidas": 0,
         "de_ia": 2}])
    assert hacer.resumen()["acierto"] == 0.75


def test_sin_nada_decidido_el_acierto_es_None_y_no_cero(monkeypatch):
    """Cero acierto y «todavía no sabemos» son cosas distintas: la primera
    frenaría la autonomía por una medición que nunca se hizo."""
    monkeypatch.setattr(hacer, "_filas", lambda *a, **k: [
        {"pendientes": 4, "aplicadas": 0, "rechazadas": 0, "fallidas": 0,
         "de_ia": 0}])
    assert hacer.resumen()["acierto"] is None


def test_cada_control_con_accion_apunta_a_una_accion_real():
    """`POR_CONTROL` lo deriva el registro, así que no puede quedar viejo — pero
    si alguien lo escribe a mano, esto lo caza."""
    assert set(hacer.POR_CONTROL.values()) <= set(hacer.ACCIONES)
    from api.services.av_agent_salud import CONTROLES
    assert set(hacer.POR_CONTROL) <= set(CONTROLES)


def test_toda_propuesta_tiene_motivo():
    """Una propuesta sin motivo no se puede aprobar con criterio, solo con fe —
    y aprobar con fe a escala es lo que este ciclo viene a evitar."""
    props = hacer.AccionCartera().proponer(_casos("[42932] OTC SOJ."))
    assert all(p.porque.strip() for p in props)
