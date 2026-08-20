"""TRES AVISOS, UN SOLO PROBLEMA — la correlación por dependencias.

*«Los jobs, ¿a dónde apuntan? Ej: a Aunesa… ¿Aunesa está caído? Listo, avisar que
dio error PORQUE está caído Aunesa. Adelantarte: que el aviso sea con más
contexto»* (user, 2026-08-20).

Lo que se congela acá es la línea fina: **relacionar cuando la dependencia está
escrita, y NO inventar una causa cuando no lo está.** Atribuir de más es peor que
no atribuir — un job que falla por su propio bug, marcado como «culpa de Aunesa»,
es un bug que nadie va a arreglar nunca.
"""
from __future__ import annotations

from api.services.av_agent_causas import correlacionar
from core import dependencias as dep


def _caido(prov="aunesa"):
    return {"tipo": "proveedor_caido", "ticker": prov, "severidad": "alta",
            "motivo": f"{prov} no responde", "evidencia": {"texto": "500"}}


def _job(nombre, sev="alta"):
    return {"tipo": "salud", "ticker": nombre, "severidad": sev,
            "motivo": f"{nombre}: la última corrida falló", "evidencia": {"texto": "x"}}


def test_el_job_que_DEPENDE_del_caido_dice_por_que_fallo():
    """Es todo el pedido: el aviso con la causa adentro, para no salir a buscar
    un bug que no existe."""
    hs = correlacionar([_caido(), _job("job:control_saldos")])
    assert "porque AUNESA está caído" in hs[1]["motivo"]
    assert hs[1]["por_culpa_de"] == "aunesa"


def test_el_que_NO_depende_queda_INTACTO():
    """La guarda que sostiene todo lo demás. Si la correlación atribuyera de más,
    un bug propio quedaría archivado como culpa ajena y nadie lo arreglaría."""
    hs = correlacionar([_caido(), _job("job:bcra")])
    assert "porque" not in hs[1]["motivo"]
    assert hs[1]["severidad"] == "alta" and "por_culpa_de" not in hs[1]


def test_la_consecuencia_BAJA_pero_no_se_apaga():
    """Apagarla sería mentir —el dato falta igual— pero dejarla en ALTA al lado
    de la causa hace que la pantalla muestre tres incendios donde hay uno."""
    hs = correlacionar([_caido(), _job("job:control_saldos")])
    assert hs[1]["severidad"] == "media"


def test_la_CAUSA_dice_a_cuantos_explica():
    """Ese número ES el impacto, y es lo que decide si se llama al custodio ahora
    o se espera."""
    hs = correlacionar([_caido(), _job("job:control_saldos"),
                        _job("job:portafolio_backfill")])
    assert "arrastra 2" in hs[0]["motivo"]
    assert set(hs[0]["evidencia"]["explica"]) == {
        "job:control_saldos", "job:portafolio_backfill"}


def test_un_BONO_nunca_es_consecuencia_de_un_proveedor():
    """Que a un bono le falte el eje no lo causa Aunesa. Relacionarlos sería la
    atribución de más que arruina la confianza en todas las demás."""
    bono = {"tipo": "tasa_sospechosa", "ticker": "AL30", "severidad": "alta",
            "motivo": "TEA fuera de rango", "evidencia": {}}
    hs = correlacionar([_caido(), bono])
    assert "por_culpa_de" not in hs[1] and hs[1]["severidad"] == "alta"


def test_sin_nada_caido_no_toca_nada():
    original = _job("job:control_saldos")
    hs = correlacionar([dict(original)])
    assert hs[0]["motivo"] == original["motivo"]


def test_un_fallo_correlacionando_NO_borra_los_hallazgos(monkeypatch):
    """Corre al final del relevamiento: una excepción acá no puede llevarse lo
    que ya se juntó."""
    monkeypatch.setattr(dep, "de_quien_depende",
                        lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
    hs = correlacionar([_caido(), _job("job:control_saldos")])
    assert len(hs) == 2


# ── el grafo se DERIVA, no se escribe ────────────────────────────────────────

def test_la_dependencia_sale_del_CODIGO_y_no_de_una_lista():
    """Un job que le pega a Aunesa lo dice en su `import` o en la URL: las dos
    están escritas. Una lista a mano se queda vieja el día que alguien agrega un
    job y no se acuerda — **y no avisa**, que es el modo de falla que esto tapa."""
    assert "aunesa" in dep.proveedores_de("jobs.control_saldos")   # por import
    assert "aunesa" in dep.proveedores_de("jobs.aum")              # por la URL
    assert "bcra" in dep.proveedores_de("jobs.bcra")


def test_los_MODULOS_del_catalogo_EXISTEN():
    """`core.bcra` no existía (es `bcra_api`) y por eso la dependencia del BCRA
    no se detectaba nunca. **Un catálogo que nombra un módulo inexistente no da
    error, da silencio** — que es la peor forma de estar roto."""
    import importlib.util
    for modulo in dep.CLIENTE_DE:
        assert importlib.util.find_spec(modulo), (
            f"«{modulo}» no existe: su proveedor no se va a detectar nunca")


def test_no_se_siguen_los_imports_hasta_el_infinito():
    """Con dos saltos todo depende de todo (cualquier módulo llega a
    `core.postgres`) y la correlación empieza a inventar causas."""
    assert dep.PROFUNDIDAD == 1
    assert dep.proveedores_de("engines.curvas") == frozenset(), (
        "un motor de cálculo no depende de ningún proveedor externo")


def test_una_pieza_DESCONOCIDA_no_inventa_dependencias():
    """Sin candidato NO se adivina: mejor no correlacionar que atribuirle la
    caída al job equivocado."""
    assert dep.de_quien_depende("job:no_existe_este") == frozenset()
    assert dep.de_quien_depende("") == frozenset()


# ── EL LABEL DEL CRON, que era el eslabón roto (2026-08-20) ──────────────────

def test_el_hallazgo_de_SALUD_llega_con_el_LABEL_y_igual_se_resuelve():
    """**El caso que motivó toda la correlación, y que no funcionaba.**

    SALUD nombra sus chequeos `job:<label del crontab>`, y ese label es libre:
    `portafolio_diario` corre `jobs.portafolio_backfill`. Como el grafo se arma
    con nombres de MÓDULO, `de_quien_depende("job:portafolio_diario")` daba vacío
    y la pantalla seguía mostrando las dos cosas sueltas:

        proveedor_caido   Aunesa no responde
        salud_job         portafolio_diario: la última corrida falló

    El 2026-08-20 el AuM no se escribió por un 500 de Aunesa y el aviso no lo
    decía. La traducción label → módulos ya existía en `jobs_catalogo`.
    """
    from core.dependencias import de_quien_depende
    assert "aunesa" in de_quien_depende("job:portafolio_diario")
    assert "aunesa" in de_quien_depende("portafolio_diario")


def test_una_CADENA_hereda_la_dependencia_de_cualquiera_de_sus_modulos():
    """`negocio_chain` es UNA línea de cron con varios `-m jobs.x`. Si cualquiera
    le pega a un proveedor, la corrida entera depende de ese proveedor."""
    from core.dependencias import de_quien_depende
    assert "aunesa" in de_quien_depende("job:negocio_chain")


def test_un_label_INVENTADO_no_devuelve_nada():
    """Sin candidato NO se inventa: atribuir la caída al job equivocado es peor
    que no correlacionar."""
    from core.dependencias import de_quien_depende
    assert de_quien_depende("job:esto_no_existe_en_ningun_lado") == frozenset()
