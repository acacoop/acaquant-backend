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


def test_el_reloj_NO_ESCRIBE_EN_EL_EVAL_SET():
    """⚠️ **El invariante nuevo** (2026-08-24). Esta pasada votaba ✔/✖ como
    `verificado`, que pesa igual que un click humano en la compuerta de
    autonomía. Pero juzgaba sobre dos estados que no significan lo que dicen:
    `resuelto` es «el detector no lo vio» (no «lo arreglaron») y `volvio` es
    «lo volvió a ver» (no «el arreglo falló»). Resultado medido: 11 ✖ en una
    sola corrida contra arreglos que nadie hizo, y como la compuerta no tolera
    un solo negativo, esas causas quedaban descalificadas para siempre.

    Vuelve a votar el día que el objeto registre CÓMO se cerró. Hasta entonces
    no inventa señal — y este test es lo que impide que vuelva por descuido."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert "votar(" not in src
    assert 'origen="verificado"' not in src
    assert "av_agent_evals" not in src.split('"""')[-1]


def test_el_reloj_SIGUE_contando_hitos():
    """No votar no es dejar de mirar: la pantalla ¿AGUANTAN? se dibuja con
    esto. Lo que se apagó es el JUICIO, no la medición."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert "aguantaron.append" in src and "volvieron.append" in src
    assert "max(ciclo.HITOS_DIAS)" in src


def test_votos_sale_en_CERO_a_proposito():
    """El campo se mantiene para no romper a quien lo lee (`jobs/seguimiento`),
    pero vale 0 siempre. Un 0 explicado es honesto; sacar la clave rompería al
    que la consume sin avisar."""
    from api.services.av_agent_items import cerrar_hitos
    src = codigo(cerrar_hitos)
    assert "votos = 0" in src
    assert "votos +=" not in src


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
