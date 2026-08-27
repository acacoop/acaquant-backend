"""`OTROS` no entra en NINGÚN número de POSICIONES Y DIFERENCIAS.

⚠️ **Por qué este test existe y no alcanza con "acordarse".** La exclusión vivía
en UN solo lugar —`rankings()`, en Python— y las otras seis queries sumaban
`OTROS` sin enterarse. Eso NO falla: cada mitad de la pantalla es coherente
consigo misma, los totales cierran, y el ranking y el consolidado simplemente
hablan de universos distintos. Se descubre mirando una pantalla, tarde.

Así que en vez de confiar en el criterio, este test RECORRE EL CÓDIGO: toda
query sobre `ap5.portfolio` tiene que llevar el filtro, o estar acá abajo con su
motivo escrito. Una función nueva que lo olvide no compila en verde.
"""
import inspect
import re

from api.services import ap5_posiciones as ap5

# Las ÚNICAS queries que pueden mirar `ap5.portfolio` sin filtrar, con el motivo.
# Agregar una fila acá es una decisión consciente, no un olvido.
SIN_FILTRO_A_PROPOSITO = {
    # `actualizado()` contesta CUÁNDO corrió el job, no cuánta plata hay. Si se
    # filtrara, una corrida que sólo tocó cuentas `OTROS` se vería como «el job
    # no corrió» — que es justo lo que este sello existe para distinguir.
    "actualizado",
}


def _fuentes() -> dict[str, str]:
    return {n: inspect.getsource(f)
            for n, f in vars(ap5).items() if inspect.isfunction(f)}


def test_toda_query_sobre_portfolio_excluye_otros():
    for nombre, src in _fuentes().items():
        if nombre in SIN_FILTRO_A_PROPOSITO or "ap5.portfolio" not in src:
            continue
        # Una query por cada `FROM ap5.portfolio`: cada una necesita su filtro.
        apariciones = len(re.findall(r"FROM ap5\.portfolio", src))
        filtros = src.count("sin_otros(")
        assert filtros >= apariciones, (
            f"{nombre}(): {apariciones} query(s) sobre ap5.portfolio y sólo "
            f"{filtros} sin_otros(). Toda query que sume posición o plata tiene "
            "que excluir los grupos de GRUPOS_FUERA_DEL_REPORTE — si esta es la "
            "excepción, agregala a SIN_FILTRO_A_PROPOSITO con el motivo."
        )


def test_el_filtro_de_python_y_el_de_sql_deciden_igual():
    """El predicado SQL normaliza con `upper(btrim())` y Python además saca los
    acentos. Son lo mismo MIENTRAS los grupos excluidos sean ASCII — el día que
    alguien agregue uno con acento, las dos mitades empezarían a decidir
    distinto sin que falle nada (REGLA #9)."""
    for g in ap5.GRUPOS_FUERA_DEL_REPORTE:
        assert g == g.upper().strip(), f"{g!r} tiene que venir ya normalizado"
        assert ap5.normalizar_grupo(g) == g, (
            f"{g!r} cambia al normalizar en Python, así que el SQL "
            "(upper+btrim, sin tocar acentos) decidiría distinto"
        )
        assert not ap5.entra_al_reporte(g), f"{g!r} tiene que quedar FUERA"


def test_sin_grupo_no_es_otros():
    """Una cuenta sin clasificar SÍ entra: que no se vea sería esconder que
    falta clasificarla. `OTROS` es una decisión tomada; vacío es un pendiente."""
    for g in ("", "   ", ap5.SIN_GRUPO):
        assert ap5.entra_al_reporte(g), f"{g!r} no debería quedar excluido"


def test_el_predicado_usa_el_alias_que_le_pasan():
    assert "cx.account = p.account" in ap5.sin_otros("p")
    assert "cx.account = q.account" in ap5.sin_otros("q")
