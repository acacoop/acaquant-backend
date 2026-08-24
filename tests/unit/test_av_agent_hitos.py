"""EL TIEMPO, CONVERTIDO EN EVIDENCIA — y el reloj que no tenía cuerda.

`en_seguimiento()` y el escalonado 1·2·3·7·14·30 estaban construidos y **no los
llamaba nadie**. Es la misma enfermedad que este proyecto ya bautizó: *una tarea
existe solo si alguien lee su salida* (§0.l). Construir el reloj y no darle
cuerda es tenerlo de adorno.

Y lo que mide es lo más valioso que tiene el sistema, porque **no es la opinión
de nadie**: el problema volvió o no volvió, y el agente no controla eso.
"""
from __future__ import annotations

from core import ciclo

from ._fuente import codigo

# ── ESTÁ ENCHUFADO ──────────────────────────────────────────────────────────

def test_el_JOB_DIARIO_corre_los_hitos():
    """El cron que le da cuerda al reloj. Desde la Fase 2 es lo ÚNICO que hace:
    el medidor viejo (`av_agent_seguimiento`) se borró."""
    import jobs.seguimiento as j
    src = codigo(j.main)
    assert "cerrar_hitos()" in src
    assert "seg.revisar" not in src and "_claves_abiertas" not in src


def test_la_PANTALLA_lo_muestra():
    """Una medición que no se ve no existe."""
    from api.services import av_agent_vista as v
    assert '"seguimiento": _seguimiento_corto()' in codigo(v.vista)


def test_el_cron_del_seguimiento_existe():
    import pathlib
    cron = pathlib.Path("deploy/crontab.txt").read_text(encoding="utf-8")
    assert "jobs.seguimiento" in cron


# ── QUÉ vota, y qué NO ──────────────────────────────────────────────────────

def test_TODAVIA_NO_VOLVIO_no_es_AGUANTO():
    """Solo vota el que pasó el ÚLTIMO hito. Premiar a los tres días sería
    exactamente lo que el escalonado vino a evitar."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert "d < tope" in src and "max(ciclo.HITOS_DIAS)" in src


def test_el_reloj_VUELVE_a_votar_pero_SOLO_sobre_cierres_por_ACCION():
    """⚠️⚠️ **El invariante que reemplaza al de §0.de** (Fase 2, 2026-08-24).

    El voto se había apagado porque `resuelto` no significaba lo que el voto
    afirmaba: podía ser «alguien lo arregló» o «el detector no lo vio en esta
    corrida». Con las dos cosas en el mismo saco, el ✖ acusaba a arreglos que
    nadie había hecho — 11 negativos en una sola corrida, y como la compuerta no
    tolera un solo negativo, esas causas quedaban descalificadas para siempre.

    §0.de dejó la condición escrita: *«vuelve a votar cuando el objeto registre
    CÓMO se cerró»*. `resuelto_como` es ese campo. Ahora vota — **y `ciclo.vota`
    es la única puerta**: sin cierre declarado, no hay voto."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert 'origen="verificado"' in src, "el voto tiene que estar de vuelta"
    assert "ciclo.vota(it.resuelto_como)" in src, "sin la guarda no puede volver"
    # y la guarda corta ANTES de cada votar()
    for tramo in src.split("av_agent_evals.votar(")[:-1]:
        assert "juzgable" in tramo, "hay un votar() que no pasa por la guarda"


def test_un_cierre_POR_AUSENCIA_no_vota_NI_a_favor_NI_en_contra():
    """El 100% de los votos falsos venían de acá. Y no alcanza con no votar el
    ✔: un `volvio` sobre algo que se cerró por ausencia tampoco prueba que un
    arreglo haya fallado — no hubo arreglo."""
    from core import ciclo
    assert ciclo.vota(ciclo.POR_ACCION) is True
    assert ciclo.vota(ciclo.POR_AUSENCIA) is False
    # Ante la duda, NO se vota: es el lado que no fabrica señal.
    assert ciclo.vota("") is False and ciclo.vota(None) is False
    assert ciclo.vota("cualquier_cosa") is False


def test_lo_que_NO_se_juzgo_se_CUENTA():
    """Un cierre por ausencia que no vota es correcto; que no aparezca en ningún
    lado es truncar en silencio."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert '"sin_juzgar": sin_juzgar' in src


def test_el_voto_del_tiempo_usa_EL_MISMO_dominio_que_el_humano():
    """Estaba clavado en «bono» para todo. El tablero agrupa por
    (dominio, causa), así que los votos del tiempo sobre un motor caían en
    `bono` y los humanos de la misma causa en `sistema`: dos filas y ninguna
    llegaba a los 10 que abren la compuerta."""
    from api.services import av_agent, av_agent_items
    src = codigo(av_agent_items.cerrar_hitos)
    assert 'dominio="bono"' not in src
    assert "_dominio_de(it)" in src
    assert "dominio_eval" in codigo(av_agent_items._dominio_de)
    assert av_agent.dominio_eval("motor_caido") == "sistema"


def test_al_VOLVER_no_se_pierde_cuanto_habia_aguantado():
    """La otra condición de §0.de. `resuelto_at` se limpia al reabrir (si no, el
    reloj le seguiría contando hitos a un arreglo que falló), y sin guardarlo
    antes se perdía la diferencia entre fallar al día 1 y fallar al día 20."""
    from api.services import av_agent_items
    src = codigo(av_agent_items.ver)
    assert "aguanto_hasta" in src
    assert "reaperturas" in src, "volver una vez y volver cinco no es lo mismo"


def test_el_reloj_SIGUE_contando_hitos():
    """No votar no es dejar de mirar: la pantalla ¿AGUANTAN? se dibuja con
    esto. Lo que se apagó es el JUICIO, no la medición."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert "aguantaron.append" in src and "volvieron.append" in src
    assert "max(ciclo.HITOS_DIAS)" in src


def test_si_no_se_puede_LEER_no_se_vota_nada():
    """Dar todo por bueno porque no pudimos mirar sería premiar el silencio."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    i_except = src.index("except Exception")
    assert 'return {"ok": False' in src[i_except:i_except + 200]


# ── el escalonado en sí ─────────────────────────────────────────────────────

def test_lo_que_VOLVIO_pierde_todo_lo_acumulado():
    it = ciclo.Item(clave="x", tipo="hallazgo", estado=ciclo.VOLVIO,
                    resuelto_at="2026-07-01T00:00:00+00:00")
    assert it.confianza_del_arreglo == 0.0


def test_el_primer_hito_da_la_senal_RAPIDA():
    assert ciclo.HITOS_DIAS[0] == 1
    assert ciclo.proximo_hito(0) == 1


def test_y_el_reloj_sigue_corriendo_un_mes():
    assert max(ciclo.HITOS_DIAS) == 30
    assert ciclo.confianza(30) == 1.0
