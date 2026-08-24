"""EL CÍRCULO SE CIERRA: ¿el arreglo FUNCIONÓ, o solo se escribió bien?

El user (2026-08-19, y de nuevo al pedir el paso siguiente del eval set):

    *«Necesito que este agente entienda cuándo hizo algo bien, no solamente
     porque yo le puse "acertó", sino porque al otro día o durante unos días
     puede detectar que los cambios realmente tuvieron consistencia.»*

`av_agent_seguimiento` hace exactamente eso desde el 2026-08-19 — y **nunca
recibió un caso de las 8 acciones**. Se alimenta desde
`av_agent_acciones.registrar`, y `av_agent_hacer._aplicar_una` (el camino que
usan TODAS, incluidos los botones de fila) no lo llamaba nunca. Medido: cero
apariciones de `registrar` en ese archivo.

    Verificar que la escritura entró no dice si el arreglo era el correcto:
    un símbolo mal puesto se escribe igual de bien que uno bien puesto.

Nada fallaba. El libro no registraba y el seguimiento se quedaba vacío.
"""
from __future__ import annotations

import inspect

from api.services import av_agent_hacer as h

# ── que el camino de aplicar ALIMENTE al libro ───────────────────────────────

def test_aplicar_registra_en_el_LIBRO():
    """Sin esto ninguna de las 8 acciones aparece en la tab HIZO, y `CLAUDE.md`
    es explícito: una escritura automática sin libro no se puede auditar."""
    src = inspect.getsource(h._aplicar_una)
    assert "registrar(" in src and "av_agent_acciones" in src


def test_solo_se_registra_lo_que_QUEDO():
    """Poner en seguimiento algo que falló mediría un arreglo que no existe — y
    a los 5 días lo cantaría como «volvió», culpando al diagnóstico de un
    problema que fue de la escritura."""
    src = inspect.getsource(h._aplicar_una)
    i_if = src.index("if quedo:\n        try:")
    i_reg = src.index("acc.registrar(")
    assert i_if < i_reg, "el registro tiene que estar adentro del `if quedo`"


def test_registrar_va_DESPUES_de_sellar_y_no_puede_tumbar_la_accion():
    """La escritura real ya pasó: no se deshace por un problema de medición."""
    src = inspect.getsource(h._aplicar_una)
    assert src.index("_sellar(") < src.index("acc.registrar(")
    assert "except Exception" in src.split("acc.registrar(")[1][:400]


def test_el_LIBRO_no_mide_nada_mas_que_el_libro():
    """⚠️ Acá se exigía lo contrario: que `registrar` anotara en
    `av_agent_seguimiento`. Se dio vuelta el 2026-08-24 (Fase 2) porque ese
    segundo medidor **solo podía decir «aguantó»** — armaba la clave
    `accion:objetivo:regla` y la comparaba contra `tipo:sujeto:regla`.

    El libro registra QUÉ se hizo. Quién dice si FUNCIONÓ es el detector, y
    después el reloj de los hitos. Un solo camino."""
    from api.services import av_agent_acciones as acc
    src = inspect.getsource(acc.registrar)
    assert "seg.anotar(" not in src
    assert "av_agent_evals" not in src


# ── la CAUSA: la clave que tiene que coincidir con el voto humano ────────────

def test_toda_accion_declara_una_causa_resoluble():
    for a in h.ACCIONES.values():
        assert h._causa_de(a), f"«{a.id}» no resuelve ninguna causa"


def test_la_causa_de_apuntar_pata_es_la_QUE_VOTA_UN_HUMANO():
    """⚠️ **El desalineo que habría trabado la compuerta para siempre.** El
    control se llama `patas_equivocadas` (plural) y el detector emite
    `pata_equivocada` (singular). Son los MISMOS bonos: con la clave del control,
    los 17 votos humanos de los BOPREALes y los derivados de sus arreglos se
    contarían por separado y ninguno llegaría nunca al mínimo."""
    from api.services.av_agent_skills import _REGLAS_DETECTOR
    a = h.ACCIONES["mercado.apuntar_pata"]
    assert h._causa_de(a) == "pata_equivocada"
    assert h._causa_de(a) in _REGLAS_DETECTOR["precio_moneda"]
    assert h._causa_de(a) != a.sobre, "la causa NO es el id del control"


def test_las_acciones_de_RUEDA_votan_causas_que_un_detector_emite():
    """Las tres de rueda tienen detector espejo, así que su causa tiene que ser
    una regla real. Una causa inventada acumula votos que nadie puede cruzar."""
    from api.services.av_agent_skills import _REGLAS_DETECTOR
    reales = {r for rs in _REGLAS_DETECTOR.values() for r in rs}
    for aid in ("mercado.apuntar_pata", "mercado.pedir_pata", "mercado.pata_dolar"):
        c = h._causa_de(h.ACCIONES[aid])
        assert c in reales, f"«{aid}» vota una causa que ningún detector emite: {c}"


def test_las_de_CONTROL_usan_el_control_como_causa():
    """No tienen detector espejo: ahí el control ES la causa, y es coherente
    que se midan en su propio espacio."""
    for aid in ("assets.cartera", "assets.fci", "contrapartes.alta"):
        a = h.ACCIONES[aid]
        assert h._causa_de(a) == a.sobre


# ── el libro sabe DÓNDE escribió cada acción, sin una segunda lista ──────────

def test_el_destino_de_las_acciones_NUEVAS_no_dice_interrogante():
    """Copiar los 8 ids a `DESTINOS` a mano es cómo se consigue un libro que
    dice «?» el día que alguien suma una acción."""
    from api.services.av_agent_acciones import _destino
    for a in h.ACCIONES.values():
        assert _destino(a.id) != "?", f"«{a.id}» escribiría con destino «?»"


def test_una_accion_desconocida_SIGUE_diciendo_interrogante():
    """No se inventa un destino: no saberlo es un dato."""
    from api.services.av_agent_acciones import _destino
    assert _destino("no.existe") == "?"


def test_el_destino_de_apuntar_pata_nombra_la_tabla_real():
    from api.services.av_agent_acciones import _destino
    assert "mercado.curvas" in _destino("mercado.apuntar_pata")


# ── el seguimiento, que es lo que vale ───────────────────────────────────────

def test_una_APROBACION_no_es_un_voto():
    """⚠️ **El invariante** (2026-08-24). `_votar_derivado` escribía en el eval
    set un ✔ con `acierta=True` FIJO cada vez que alguien aprobaba una acción.
    Medido en prod: **106 votos, 106 ✔ — 100% por construcción**, más de la mitad
    de la tabla. Un número que no puede bajar no mide nada.

    Aprobar es decir «dale», no «tu diagnóstico era correcto», y el «dale» ya
    queda anotado en el libro de acciones."""
    from api.services import av_agent_acciones as acc
    assert not hasattr(acc, "_votar_derivado")
    assert "av_agent_evals" not in inspect.getsource(acc)


def test_la_APROBACION_pone_el_objeto_EN_CURSO_y_ahi_arranca_el_reloj():
    """Lo que sí vale es el tiempo, y el camino es UNO: la acción mueve el
    objeto a `en_curso`, el DETECTOR lo cierra cuando deja de verlo, y recién ahí
    corren los hitos. La acción no califica su propio trabajo."""
    from api.services import av_agent_hacer
    src = inspect.getsource(av_agent_hacer._mover_item)
    assert "EN_CURSO" in src and "RESUELTO" not in src


def test_todavia_no_volvio_NO_es_aguanto():
    """Sin la espera estaríamos premiando un arreglo de hace una hora. Solo entra
    a `aguantaron` el que pasó el ÚLTIMO hito (30 días hábiles)."""
    from api.services import av_agent_items
    from core import ciclo
    src = inspect.getsource(av_agent_items.cerrar_hitos)
    assert "tope = max(ciclo.HITOS_DIAS)" in src
    assert "if d < tope:" in src and "continue" in src
    assert max(ciclo.HITOS_DIAS) == 30


# ── una causa PROBADA deja de preguntar ──────────────────────────────────────

def _confianza(regla, medicion):
    """La confianza se sella dentro de `_hallazgos_ultima_corrida`, así que se
    prueba ahí: mockear esa función sería mockear justo lo que se quiere medir."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from api.services import av_agent_vista as v

    class _Cur:
        def __init__(self):
            self.q = ""

        def execute(self, sql, *a, **k):
            self.q = " ".join(str(sql).split())

        def fetchone(self):
            return (datetime.now(UTC),) if "max(corrida_at)" in self.q else None

        def fetchall(self):
            if "FROM agente.av_agent_hallazgos" in self.q:
                return [("precio_moneda", "BPOA7", regla, "media", "x", {})]
            return []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Pool:
        def connection(self):
            return _Conn()

    with patch.object(v.av_agent_evals, "precision_por_causa", lambda: medicion), \
         patch.object(v.av_agent, "simbolos_primary", set), \
         patch.object(v.av_agent, "tickers_ignorados", set), \
         patch.object(v, "get_pool", lambda: _Pool()):
        hs, _ = v._hallazgos_ultima_corrida()
    assert hs, "el hallazgo de prueba se cayó por otra razón"
    return hs[0]["confianza"]


def test_una_causa_17_de_17_queda_PROBADA():
    """`ya_votado` corta la repetición por CASO y no alcanza: con
    `pata_equivocada` en 17/17, un BOPREAL nuevo pedía el voto 18."""
    c = _confianza("pata_equivocada", {"pata_equivocada": (17, 17, 20)})
    assert c["probada"] is True and c["suficiente"] is True


def test_un_solo_ERROR_la_saca_de_probada():
    """No es irreversible: una causa que empieza a fallar vuelve a preguntar
    sola, que es justo la señal de que el agente empeoró."""
    c = _confianza("pata_equivocada", {"pata_equivocada": (17, 16, 20)})
    assert c["probada"] is False


def test_pocos_votos_NO_alcanzan_aunque_sean_todos_aciertos():
    """2 de 2 no es «100% de acierto», es «casi no hay evidencia»."""
    c = _confianza("sin_punta", {"sin_punta": (2, 2, 2)})
    assert c["probada"] is False and c["suficiente"] is False


def test_probada_usa_EL_MISMO_umbral_que_la_compuerta_de_autonomia():
    """Si la pantalla usara otro criterio, diría «probada» sobre algo que el
    tablero todavía llama «sin evidencia»."""
    import inspect

    from api.services import av_agent_evals, av_agent_vista
    src = inspect.getsource(av_agent_vista._hallazgos_ultima_corrida)
    assert "av_agent_evals.MIN_VOTOS" in src
    assert av_agent_evals.MIN_VOTOS >= 10


def test_si_no_se_pudo_MEDIR_no_se_afirma_que_esta_probada():
    """`None` = no se pudo medir, distinto de «nadie votó»."""
    assert _confianza("pata_equivocada", None) is None
