"""Los datos que viven en dos lugares, y el detector de la CLASE de bug.

> *Dos representaciones del mismo dato sin un árbitro declarado no conviven: se
> separan. Y cuando se separan **no falla nada** — cada mitad sigue siendo
> internamente coherente y el sistema miente en silencio.*

Se pagó tres veces en cuatro días (el símbolo columna-vs-blob, el ticker corto,
`preferencia` escrita tres veces) y las tres se descubrió tarde y de casualidad,
mirando una pantalla.
"""
from __future__ import annotations

from api.services import av_agent
from core import duplicados as D


def test_todo_duplicado_declara_QUIEN_MANDA():
    """Un duplicado sin árbitro es el bug, no el registro del bug: si no está
    escrito cuál gana, el día que difieran se decide a ojo o no se decide."""
    for d in D.DUPLICADOS:
        assert d.arbitro.strip(), f"{d.id} no declara árbitro"
        assert d.a.strip() and d.b.strip(), f"{d.id} no dice dónde vive cada copia"


def test_todo_duplicado_declara_QUE_SE_ROMPE():
    """Sin esto la lista es un inventario y no una lista de trabajo: nadie sabe
    si atenderlo hoy o el mes que viene."""
    for d in D.DUPLICADOS:
        assert len(d.rompe.strip()) > 30, f"{d.id} no explica qué se rompe"


def test_los_ids_son_unicos():
    ids = [d.id for d in D.DUPLICADOS]
    assert len(ids) == len(set(ids))


def test_la_query_devuelve_SOLO_lo_que_difiere():
    """La lista es de PROBLEMAS, no un inventario. Una query sin el `<>` traería
    los 229 bonos sanos y la señal se perdería adentro."""
    for d in D.DUPLICADOS:
        assert "<>" in d.sql, f"{d.id}: la query no filtra por diferencia"


def test_el_hallazgo_es_ALTA_y_lleva_los_DOS_valores():
    """`alta` sin dudar: si dos copias difieren, algo está leyendo el valor
    incorrecto AHORA — lo único que no sabemos es quién. Y tiene que mostrar los
    dos valores, porque decidir cuál está bien es de una persona."""
    hs = av_agent.detectar_dato_partido({"partidos": [{
        "id": "x", "que": "el símbolo", "a": "columna", "b": "blob",
        "arbitro": "la columna", "rompe": "la fila sale vacía teniendo precio",
        "n": 2, "ejemplos": [{"sujeto": "AO29", "valor_a": "AO29D",
                              "valor_b": "AO29"}]}], "sin_mirar": []})
    assert len(hs) == 1
    h = hs[0]
    assert h["tipo"] == "dato_partido" and h["severidad"] == "alta"
    assert "AO29D" in h["motivo"] and "«AO29»" in h["motivo"]
    assert h["evidencia"]["arbitro"] == "la columna"


def test_lo_que_NO_SE_PUDO_MIRAR_se_canta():
    """**El silencio no es un verde.** Un duplicado sin chequear se leería igual
    que uno sano, que es exactamente la forma de mentir que este módulo
    persigue."""
    hs = av_agent.detectar_dato_partido({"partidos": [], "sin_mirar": [
        {"id": "y", "que": "el emisor", "error": "UndefinedTable: no existe"}]})
    assert len(hs) == 1
    assert hs[0]["regla"] == "no_pude_chequear"
    assert "no se miró" in hs[0]["motivo"]


def test_sin_divergencias_no_hay_hallazgos():
    assert av_agent.detectar_dato_partido({"partidos": [], "sin_mirar": []}) == []


def test_NO_se_automatiza_y_es_una_decision():
    """Elegir la del árbitro y pisar la otra parece obvio y no lo es: puede que
    la equivocada sea la del árbitro, y pisar borra la evidencia de que hubo una
    divergencia."""
    assert av_agent.ACCION_POR_TIPO["dato_partido"] is None


def test_el_caso_AO29_esta_cubierto():
    """El bug que originó todo: el motor escribía leyendo el BLOB y la vista
    buscaba por la COLUMNA. Si mañana vuelven a separarse, se ve el mismo día."""
    ids = {d.id for d in D.DUPLICADOS}
    assert "simbolo_columna_vs_blob" in ids
    d = next(d for d in D.DUPLICADOS if d.id == "simbolo_columna_vs_blob")
    assert "columna" in d.arbitro.lower()
