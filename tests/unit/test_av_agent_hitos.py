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
    """Lo que faltaba: el cron que le da cuerda al reloj."""
    import jobs.seguimiento as j
    assert "_hitos(jr)" in codigo(j.main)
    assert "cerrar_hitos()" in codigo(j._hitos)


def test_el_job_viejo_y_el_nuevo_CONVIVEN_sin_tumbarse():
    """Miden lo mismo por caminos distintos hasta que el viejo se apague. Que
    uno falle no puede dejar al otro sin correr."""
    import jobs.seguimiento as j
    cola = codigo(j.main).split("_hitos(jr)")[1][:300]
    assert "except Exception" in cola


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


def test_vota_como_VERIFICADO_que_pesa_como_un_humano():
    """`verificado` cuenta a la par de un voto humano para la compuerta de
    autonomía; `derivado` (alguien diciendo «dale») no. La diferencia es que
    esto no lo dice nadie: lo dice el mundo."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert src.count('origen="verificado"') == 2      # el ✔ y el ✖
    from api.services import av_agent_evals
    assert "'humano','verificado'" in codigo(av_agent_evals.precision_por_causa)


def test_es_IDEMPOTENTE_o_en_un_mes_hay_30_votos_del_mismo_arreglo():
    """El job corre todos los días y los que aguantaron siguen aguantando."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert 'ref=f"aguanto:{it.clave}"' in src
    assert 'ref=f"volvio:{it.clave}"' in src


def test_lo_que_VOLVIO_vota_NEGATIVO_con_motivo():
    """Un ✖ del eval set necesita motivo (si no se rechaza), y acá el motivo es
    un hecho: «se arregló y volvió a pasar»."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert "acierta=False" in src and "nota=" in src


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
