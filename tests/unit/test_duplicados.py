"""Los datos que viven en dos lugares, y el detector de la CLASE de bug.

> *Dos representaciones del mismo dato sin un árbitro declarado no conviven: se
> separan. Y cuando se separan **no falla nada** — cada mitad sigue siendo
> internamente coherente y el sistema miente en silencio.*

Se pagó tres veces en cuatro días (el símbolo columna-vs-blob, el ticker corto,
`preferencia` escrita tres veces) y las tres se descubrió tarde y de casualidad,
mirando una pantalla.
"""
from __future__ import annotations

from agente.detectores import datos as det
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


# ── el detector ─────────────────────────────────────────────────────────────
#
# ⚠️ `dato_partido(u)` NO lee `u`: va a la base él mismo vía
# `duplicados.divergencias()`. Los tres tests de abajo le pasaban un payload
# fabricado —contrato del agente VIEJO— y desde AGENT 2.0 lo ignoraba: corrían
# contra Postgres, fallaban con `no_pude_chequear` para los 5 duplicados reales,
# y afirmaban sobre `h["tipo"]`/`h["motivo"]`, campos que `Hallazgo` no tiene.
# Se pincha la FUENTE, que es la única forma de que el test hable del detector.

def _con(monkeypatch, res: dict):
    from core import duplicados as D_
    monkeypatch.setattr(D_, "divergencias", lambda: res)


def test_el_hallazgo_es_ALTA_y_lleva_los_DOS_valores(monkeypatch):
    """`alta` sin dudar: si dos copias difieren, algo está leyendo el valor
    incorrecto AHORA — lo único que no sabemos es quién. Y tiene que mostrar los
    dos valores, porque decidir cuál está bien es de una persona."""
    _con(monkeypatch, {"partidos": [{
        "id": "x", "que": "el símbolo", "a": "columna", "b": "blob",
        "arbitro": "la columna", "rompe": "la fila sale vacía teniendo precio",
        "tiene_sql": True,
        "n": 2, "ejemplos": [{"sujeto": "AO29", "valor_a": "AO29D",
                              "valor_b": "AO29"}]}], "sin_mirar": []})
    hs = det.dato_partido({})
    assert len(hs) == 1
    h = hs[0]
    assert h.regla == "copias_que_no_coinciden" and h.severidad == "alta"
    assert "AO29D" in h.problema and "«AO29»" in h.problema
    assert h.evidencia["arbitro"] == "la columna"
    # Y el árbitro viaja en el `que_hacer`: decidir cuál gana es de una persona.
    assert "la columna" in h.que_hacer


def test_lo_que_NO_SE_PUDO_MIRAR_se_canta(monkeypatch):
    """**El silencio no es un verde.** Un duplicado sin chequear se leería igual
    que uno sano, que es exactamente la forma de mentir que este módulo
    persigue."""
    _con(monkeypatch, {"partidos": [], "sin_mirar": [
        {"id": "y", "que": "el emisor", "error": "UndefinedTable: no existe"}]})
    hs = det.dato_partido({})
    assert len(hs) == 1
    assert hs[0].regla == "no_pude_chequear"
    assert "no se miró" in hs[0].problema


def test_sin_divergencias_no_hay_hallazgos(monkeypatch):
    _con(monkeypatch, {"partidos": [], "sin_mirar": []})
    assert det.dato_partido({}) == []


def test_si_no_puede_LEER_levanta_SIN_DATOS(monkeypatch):
    """La otra mitad del invariante #1: una corrida que no pudo mirar no puede
    devolver `[]`, porque con eso el motor cerraría por ausencia todo lo abierto.
    """
    import pytest

    from agente.tipos import SinDatos
    from core import duplicados as D_

    def _revienta():
        raise RuntimeError("el pool no responde")

    monkeypatch.setattr(D_, "divergencias", _revienta)
    with pytest.raises(SinDatos):
        det.dato_partido({})


def test_se_arbitra_solo_lo_que_declara_su_SQL_y_lo_aprieta_una_persona():
    """Hasta el 2026-09-02 esto congelaba «no se automatiza». Lo que cambió
    (§0.dc, decisión del user): el duplicado que declara `arreglo_sql` tiene
    botón —lo aprieta una persona, con el preview fila por fila y el libro
    anotando antes → después, así la evidencia de la divergencia no se
    pierde—; el que declara `arreglo_manual` sigue sin botón, como aviso."""
    from agente import catalogo
    h = catalogo.HABILIDADES["dato_partido"]
    assert h.arreglos == {"copias_que_no_coinciden": "arbitrar_copia"}
    assert h.arreglo_de("copias_a_mano") == "" and h.arreglo_de("no_pude_chequear") == ""


def test_el_caso_AO29_esta_cubierto():
    """El bug que originó todo: el motor escribía leyendo el BLOB y la vista
    buscaba por la COLUMNA. Si mañana vuelven a separarse, se ve el mismo día."""
    ids = {d.id for d in D.DUPLICADOS}
    assert "simbolo_columna_vs_blob" in ids
    d = next(d for d in D.DUPLICADOS if d.id == "simbolo_columna_vs_blob")
    assert "columna" in d.arbitro.lower()


# ── (2026-08-19) NO TODOS SE ARREGLAN IGUAL ─────────────────────────────────

def test_el_que_no_tiene_arreglo_MECANICO_explica_por_que():
    """Sincronizar dos copias parece siempre lo mismo y no lo es. Sin este texto,
    el próximo escribe el UPDATE «obvio» y deja las copias coincidiendo en un
    valor que ninguna fuente respalda — peor que la divergencia, porque además
    la esconde."""
    for d in D.DUPLICADOS:
        if not d.arreglo_sql:
            assert len(d.arreglo_manual.strip()) > 40, (
                f"{d.id} no tiene arreglo mecánico y no dice qué hacer")


def test_el_arreglo_usa_el_MISMO_WHERE_que_la_deteccion():
    """Es lo que lo hace idempotente y scopeado (REGLA #4): corre solo sobre las
    filas que difieren, y la segunda corrida no toca nada."""
    for d in D.DUPLICADOS:
        if d.arreglo_sql:
            assert "WHERE" in d.arreglo_sql and "<>" in d.arreglo_sql, (
                f"{d.id}: el arreglo no está scopeado a lo que difiere")


def test_el_arreglo_le_escribe_a_la_copia_B_y_no_al_ARBITRO():
    """El árbitro es la fuente de verdad: si el arreglo lo pisara, estaríamos
    sincronizando hacia el lado equivocado."""
    for d in D.DUPLICADOS:
        if d.arreglo_sql:
            # Los dos mecánicos de hoy escriben el blob desde la columna.
            assert "jsonb_set" in d.arreglo_sql, (
                f"{d.id}: revisá que el arreglo escriba la copia B, no el árbitro")


def test_ninguno_arregla_lo_que_necesita_REINICIAR_UN_MOTOR():
    """Una acción que se aplica, se verifica en verde y no cambia nada en la
    pantalla destruye la confianza en todas las demás (§0.v)."""
    d = next(x for x in D.DUPLICADOS if x.id == "simbolo_master_vs_especies")
    assert not d.arreglo_sql
    assert "reiniciar" in d.arreglo_manual.lower()
