"""NO SE VOTA CUARENTA VECES LO MISMO.

El user, con los BOPREALes en 17/17 y los botones ¿ACERTÓ? intactos otra vez
(2026-08-20):

    *«¡Otra vez lo mismo, ya lo completé 40 veces y sigue apareciendo! No puede
    ser que no haya un detector o algo de lo que ya hice.»*

El hallazgo reaparece cada rueda **y eso está bien** —el problema sigue— pero el
VOTO no: mide al agente, no al día. Repetir el mismo juicio sobre el mismo par
no agrega un dato, infla el denominador y convierte la pantalla en un formulario
que hay que volver a llenar todas las mañanas.
"""
from __future__ import annotations

from unittest.mock import patch

from api.services import av_agent_evals as ev


def _sin_base(previo):
    """Fija el voto previo y captura si se llegó a insertar."""
    escrito = []

    class _Cur:
        description = ()

        def execute(self, *a, **k):
            escrito.append(a[0])

        def fetchone(self):
            return (1,)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    return escrito, patch.multiple(
        ev, _voto_previo=lambda c, ca: previo, get_pool=lambda: _Pool())


def test_el_MISMO_voto_sobre_el_MISMO_par_no_se_guarda_de_nuevo():
    escrito, ctx = _sin_base(previo=True)
    with ctx:
        r = ev.votar(caso="BPOA7", dominio="bono", causa="pata_equivocada",
                     acierta=True, por="x@y.com")
    assert r["duplicado"] is True
    assert not escrito, "no tiene que tocar la base"


def test_pero_CAMBIAR_DE_OPINION_sí_entra():
    """Si antes dijo que acertó y ahora dice que no, eso es una corrección y es
    justamente el dato que sirve."""
    escrito, ctx = _sin_base(previo=True)
    with ctx:
        r = ev.votar(caso="BPOA7", dominio="bono", causa="pata_equivocada",
                     acierta=False, nota="la pata sugerida tampoco cotiza",
                     por="x@y.com")
    assert not r.get("duplicado")
    assert escrito, "una corrección SÍ se guarda"


def test_y_si_el_agente_cambia_de_CAUSA_es_un_par_nuevo():
    """El par que se vota es (caso, causa). Otro diagnóstico sobre el mismo bono
    es una afirmación nueva y merece su propio juicio."""
    escrito, ctx = _sin_base(previo=None)      # nunca se votó ESA causa
    with ctx:
        r = ev.votar(caso="BPOA7", dominio="bono", causa="no_suscripto",
                     acierta=True, por="x@y.com")
    assert not r.get("duplicado") and escrito


def test_un_voto_DERIVADO_no_pasa_por_el_freno():
    """Los derivados ya tienen su propia idempotencia por `ref`; meterlos acá
    los bloquearía por un voto humano que mide otra cosa."""
    escrito, ctx = _sin_base(previo=True)
    with ctx:
        r = ev.votar(caso="BPOA7", dominio="bono", causa="pata_equivocada",
                     acierta=True, origen="derivado", ref="accion:1")
    assert not r.get("duplicado") and escrito


def test_si_no_se_puede_MIRAR_el_voto_previo_NO_se_bloquea():
    """Perder un voto es peor que repetirlo: ante la duda, se guarda."""
    class _Boom:
        def connection(self):
            raise RuntimeError("base caída")
    with patch.object(ev, "get_pool", lambda: _Boom()):
        assert ev._voto_previo("BPOA7", "pata_equivocada") is None


# ── y la pantalla no lo vuelve a preguntar ───────────────────────────────────

def test_la_vista_MARCA_lo_ya_votado():
    """Sin esto el backend dedupea pero el botón sigue ahí, y el que lo aprieta
    recibe un «duplicado» — que se lee como que el sistema no lo escuchó."""
    from api.services import av_agent_vista as v

    hall = [{"tipo": "precio_moneda", "ticker": "BPOA7",
             "regla": "pata_equivocada", "motivo": "x", "evidencia": {}},
            {"tipo": "precio_moneda", "ticker": "GD46",
             "regla": "pata_equivocada", "motivo": "x", "evidencia": {}}]
    with patch.object(v, "_hallazgos_ultima_corrida", lambda: (hall, None)), \
         patch("api.services.av_agent_evals.ya_votados",
               lambda: {("BPOA7", "pata_equivocada"): True}), \
         patch("api.services.av_agent_preguntas.abiertas", list), \
         patch("api.services.av_agent_preguntas.pendientes_de_aplicar", list), \
         patch("api.services.av_agent_acciones.listar", list), \
         patch.object(v, "avisos", list), patch.object(v, "mensajes", list), \
         patch.object(v, "_decididas", list), patch.object(v, "_ignorados", list):
        r = v.vista()
    porticker = {h["ticker"]: h for h in r["hallazgos"]}
    assert porticker["BPOA7"]["ya_votado"] is True
    assert porticker["BPOA7"]["voto"] is True
    assert "ya_votado" not in porticker["GD46"], "el no votado sigue preguntando"
