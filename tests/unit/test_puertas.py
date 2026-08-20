"""CADA HALLAZGO DICE QUÉ SE PUEDE HACER CON ÉL — y si no, POR QUÉ.

Lo dictó el caso BOPREAL: un hallazgo sin arreglo posible no es un aviso, es una
**pared**, y una pared que aparece todas las ruedas enseña a ignorar la lista
entera. `pata_equivocada` fue la más cara y tardamos semanas en saberlo porque la
única forma de enterarse era que alguien se hartara de verla.

`ACCION_POR_TIPO` decía `None` para doce tipos, mezclando cuatro cosas que no se
parecen: se acciona por otra vía · es una buena noticia · se decidió no
automatizar · **falta construirlo**. Solo la última es deuda, y era imposible
contarla.
"""
from __future__ import annotations

import pytest

from api.services import av_agent as a

# ── la declaración ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "tipo", [t for t, v in a.ACCION_POR_TIPO.items() if v is None])
def test_todo_tipo_SIN_ACCION_declara_por_que(tipo):
    """**La ley, igual que `DE_QUIEN` y `DOMINIO_EVAL`.** Un tipo nuevo sin
    declarar se vuelve otra fila muerta en la pantalla, en silencio — que es
    exactamente el modo de falla que este módulo persigue."""
    assert tipo in a.SIN_PUERTA, (
        f"«{tipo}» no tiene acción y no dice por qué: en la pantalla queda una "
        f"fila sin botón y sin explicación, que se lee como que el agente no "
        f"sabe qué hacer con lo que él mismo encontró")
    clase, porque = a.SIN_PUERTA[tipo]
    assert clase in a.CLASE_ES_DEUDA, f"«{tipo}»: clase desconocida «{clase}»"
    assert len(porque) > 15, f"«{tipo}»: el porqué tiene que explicar algo"


def test_no_se_declara_de_MAS():
    """Si un tipo consigue su puerta y queda en `SIN_PUERTA`, el catálogo empieza
    a mentir sobre por qué no se puede hacer algo que sí se puede."""
    con_accion = {t for t, v in a.ACCION_POR_TIPO.items() if v}
    assert not (set(a.SIN_PUERTA) & con_accion), (
        "estos tienen acción Y están declarados como sin puerta: "
        f"{sorted(set(a.SIN_PUERTA) & con_accion)}")


def test_un_tipo_DESCONOCIDO_cae_del_lado_de_la_DEUDA():
    """Ante la duda, deuda. Al revés —asumir «no se puede»— escondería el hueco
    justo cuando nadie lo declaró, que es cuando más falta hace verlo."""
    p = a.puerta("tipo_que_no_existe")
    assert p["hay"] is False and p["es_deuda"] is True


# ── la medición ──────────────────────────────────────────────────────────────

def _h(tipo, regla, ticker):
    return {"tipo": tipo, "regla": regla, "ticker": ticker}


def test_la_cobertura_separa_DEUDA_de_lo_que_no_lo_es():
    """**Es la mitad del valor.** Contar juntas las buenas noticias y las paredes
    daría una cobertura falsamente mala, y entonces nadie sabría cuál de los dos
    números mirar."""
    c = a.cobertura([
        _h("precio_moneda", "pata_equivocada", "AL30"),      # con puerta
        _h("recuperado", "volvio", "motor_x"),               # no es deuda
        _h("dato_partido", "difieren", "simbolo"),           # no es deuda
        _h("motor_caido", "sin_producir", "motor_rofex"),    # DEUDA
    ])
    assert c["total"] == 4 and c["con_puerta"] == 1
    assert c["sin_puerta"] == 3, "las tres no tienen botón…"
    assert c["deuda"] == 1, "…pero solo UNA se podría cerrar"
    assert c["pct"] == 25


def test_la_fila_se_ordena_por_CUANTO_RUIDO_HACE():
    """El ranking es el punto: arreglar la que sale 48 veces vale más que la que
    sale una. Sin esto, la prioridad la fijaba el hartazgo."""
    c = a.cobertura(
        [_h("motor_caido", "sin_producir", f"m{i}") for i in range(3)]
        + [_h("tabla_quieta", "sin_escribir", "t1")])
    assert [p["regla"] for p in c["paredes"]] == ["sin_producir", "sin_escribir"]
    assert c["paredes"][0]["veces"] == 3
    # Con ejemplos, para poder ir a mirar uno sin buscarlo a mano.
    assert c["paredes"][0]["ejemplos"] == ["m0", "m1", "m2"]


def test_agrupa_por_REGLA_y_no_por_TIPO():
    """La regla es la unidad que después se convierte en acción: fue
    `pata_equivocada`, no `precio_moneda`. Agrupar por tipo escondería que dentro
    de uno solo hay dos problemas con arreglos distintos."""
    c = a.cobertura([_h("motor_caido", "sin_producir", "a"),
                     _h("motor_caido", "fallo", "b")])
    assert {p["regla"] for p in c["paredes"]} == {"sin_producir", "fallo"}


def test_sin_hallazgos_no_inventa_un_porcentaje():
    """0 de 0 no es 0% de cobertura: es que no hay nada que medir. Un `0%` ahí
    diría que el agente no resuelve nada, que es falso."""
    c = a.cobertura([])
    assert c["total"] == 0 and c["pct"] is None


def test_las_CUATRO_paredes_reales_estan_declaradas_como_deuda():
    """Los que hoy tienen un arreglo concreto que el agente no ejecuta. Nombrarlo
    es lo que los pone en la fila para construirse, en vez de quedar como «cosas
    que el agente no arregla»."""
    for tipo in ("motor_caido", "motor_ruidoso", "tabla_quieta",
                 "cron_desalineado"):
        p = a.puerta(tipo)
        assert p["es_deuda"] is True, tipo
        assert p["clase"] == a.AFUERA
