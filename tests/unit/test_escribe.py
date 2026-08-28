"""UNA TABLA DE EVENTOS NO TIENE CADENCIA: TIENE OCASIONES.

La primera medición de cobertura (§0.ap) puso a `tabla_quieta · sin_escribir` como
la pared más cara —8 casos— y sus ejemplos fueron `ia.trazas`,
`manager.role_audit` y `manager.salud_eventos`. Ninguna tiene un job atrás:

    manager.role_audit ← `core/roles.py`, cuando alguien CAMBIA un rol

(`ia.trazas` era el otro ejemplo y se borró el 2026-08-28 con el gateway de IA.
El invariante no cambió: cambió el ejemplo.)

Están quietas porque no pasó nada. Ponerle un botón «relanzar» a esa pared habría
sido construir una puerta a ninguna parte — peor que no tenerla, porque promete.
"""
from __future__ import annotations

from unittest.mock import patch

from core import escribe

# ── contra el repo REAL, que es la única prueba que vale ─────────────────────

def test_las_de_EVENTO_se_reconocen():
    """Las escribe `core/`: las dispara una request o una acción, no un reloj."""
    assert escribe.la_dispara("manager.role_audit") == escribe.EVENTO
    assert escribe.quien_escribe("manager.role_audit") == ["core.roles"]


def test_las_de_RELOJ_tambien_y_dicen_QUE_RELANZAR():
    """`portafolio.tenencia` la escribe un job diario: su silencio SÍ es un
    problema, y el nombre del módulo es lo que la puerta va a necesitar."""
    assert escribe.la_dispara("portafolio.tenencia") == escribe.RELOJ
    assert escribe.que_relanzar("portafolio.tenencia") == "jobs.portafolio_backfill"


def test_una_tabla_de_EVENTO_no_tiene_que_relanzar_nada():
    """**Es el punto entero.** Si devolviera un módulo, el botón prometería algo
    que no existe."""
    assert escribe.que_relanzar("manager.role_audit") == ""


def test_NO_SE_no_es_EVENTO():
    """⚠️ Ante la duda se SIGUE EXIGIENDO frescura. Dejar de mirar una tabla
    porque no encontramos su escritor es cómo se pierde una señal de verdad — y
    sería exactamente el bug contrario al que esto arregla."""
    assert escribe.la_dispara("no.existe_esta_tabla") == escribe.NO_SE
    assert escribe.NO_SE != escribe.EVENTO


def test_el_mapa_encuentra_las_que_pasan_por_PG_MIRROR():
    """Medido: con solo el regex de `INSERT INTO`, 6 de 8 tablas conocidas daban
    `no_se` — todas escriben por `core/pg_mirror`, que recibe la tabla como
    PARÁMETRO. Un detector que mide una sola forma de hacer la cosa ve el 12% y
    no se queja."""
    m = escribe._mapa()
    assert len(m) > 40, f"solo {len(m)} tablas mapeadas: ¿se rompió el parseo?"
    # Una que SOLO aparece vía `pg_mirror`, nunca en un INSERT literal.
    assert escribe.quien_escribe("mercado.cedears_snapshot")


# ── la regla de desempate ────────────────────────────────────────────────────

def test_si_la_escriben_LOS_DOS_gana_el_RELOJ():
    """Un job Y una request. Hay algo que debería estar corriendo, así que su
    ausencia sigue siendo un problema: exigir de más es preferible a callar."""
    with patch.object(escribe, "quien_escribe",
                      lambda t: ["api.services.x", "jobs.y"]):
        assert escribe.la_dispara("cualquiera") == escribe.RELOJ
        assert escribe.que_relanzar("cualquiera") == "jobs.y"


def test_scripts_NO_cuenta_como_escritor():
    """Un one-shot que alguien corrió a mano no es el escritor habitual de nada,
    y tomarlo como tal haría que una siembra vieja defina la cadencia."""
    assert "scripts" not in escribe._QUIEN_DISPARA


# ── y que el detector lo use ─────────────────────────────────────────────────

def test_el_detector_SALTEA_las_de_evento():
    import inspect

    from agente import tablas as ctx
    src = inspect.getsource(ctx.detectar_tablas)
    assert "escribe.EVENTO" in src and "continue" in src
    # Y deja lo que la puerta va a necesitar.
    assert "que_relanzar" in src
