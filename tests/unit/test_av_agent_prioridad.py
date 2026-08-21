"""DE 64 ABIERTOS, ¿CUÁLES PIDEN ALGO HOY? (§0.bm)

El modelo guardaba `veces`, `abierto_at` y `vuelto_at` desde §0.bd… y la
pantalla seguía ordenando por severidad, que es **lo mismo que ordenaba antes
de tener memoria**. Sesenta y cuatro cosas abiertas, todas iguales, para
siempre — literalmente la queja del user: *«las cosas en ENCONTRÓ siguen
figurando»*.

Un tablero que no prioriza no es un tablero, es un depósito.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core import ciclo

from ._fuente import codigo

AHORA = datetime.now(UTC)


def it(**k) -> ciclo.Item:
    k.setdefault("abierto_at", AHORA)
    k.setdefault("ultimo_at", AHORA)
    return ciclo.Item(clave=k.pop("clave", "k"), tipo="hallazgo", **k)


# ── las bandas ───────────────────────────────────────────────────────────────

def test_las_cinco_bandas():
    assert ciclo.banda(it(), AHORA) == "nuevo"
    assert ciclo.banda(it(abierto_at=AHORA - timedelta(days=4)), AHORA) == "arrastra"
    assert ciclo.banda(it(abierto_at=AHORA - timedelta(days=4),
                          visto_at=AHORA), AHORA) == "estancado"
    assert ciclo.banda(it(estado=ciclo.VOLVIO), AHORA) == "volvio"
    assert ciclo.banda(it(estado=ciclo.RESUELTO, resuelto_at=AHORA),
                       AHORA) == "mirando"


def test_VOLVIO_gana_sobre_todo():
    """Un arreglo que falló es lo que más informa: alguien ya lo dio por
    resuelto y volvió igual. Que además sea de severidad baja o de ayer no lo
    baja de la punta."""
    volvio = it(estado=ciclo.VOLVIO, severidad="baja")
    alta = it(clave="otra", severidad="alta",
              abierto_at=AHORA - timedelta(days=30), visto_at=AHORA)
    orden = sorted([alta, volvio], key=lambda x: ciclo.prioridad(x, AHORA))
    assert orden[0] is volvio


def test_vuelto_at_alcanza_aunque_el_estado_ya_haya_avanzado():
    """Volvió y después alguien lo marcó visto: sigue siendo un arreglo que
    falló. Mirar solo el `estado` lo perdería justo cuando alguien lo tocó."""
    assert ciclo.banda(it(estado=ciclo.VISTO, visto_at=AHORA,
                          vuelto_at=AHORA), AHORA) == "volvio"


def test_dentro_de_la_banda_manda_la_severidad_y_despues_la_ANTIGUEDAD():
    """Lo que lleva más tiempo abierto va primero: es lo que más tiempo estuvo
    sin que a nadie le importara."""
    viejo = it(clave="v", abierto_at=AHORA - timedelta(days=10))
    nuevo = it(clave="n", abierto_at=AHORA - timedelta(days=2))
    assert sorted([nuevo, viejo],
                  key=lambda x: ciclo.prioridad(x, AHORA))[0] is viejo


def test_la_prioridad_es_una_TUPLA_y_no_un_puntaje():
    """Un «87 puntos» no se puede discutir ni auditar, y esconde cuál de los
    criterios lo puso ahí. La tupla dice el porqué, en orden."""
    p = ciclo.prioridad(it(), AHORA)
    assert isinstance(p, tuple) and len(p) >= 3


def test_NO_hay_banda_estructural_vs_intermitente():
    """⚠️ La tentación era comparar `veces` contra las corridas transcurridas.
    No se puede: **el centinela corre cada 5 minutos y el control una vez por
    noche**, así que 18 veces significa cosas opuestas según quién lo vio.
    Inventar ese denominador sería un cartel con pinta de medición y sin
    medición atrás (REGLA #2). Si volvió, ya lo dice con certeza."""
    src = codigo(ciclo.banda) + codigo(ciclo.prioridad)
    assert "estructural" not in src and "intermitente" not in src
    assert "corridas" not in src


# ── el conteo que convierte la lista en una decisión ────────────────────────

def test_lo_NUEVO_no_cuenta_como_pide_algo():
    """Apareció hoy y todavía no probó nada. Si entrara, el contador subiría y
    bajaría solo — y un número que se mueve sin que pase nada deja de mirarse."""
    from api.services import av_agent_items
    src = codigo(av_agent_items.que_importa)
    assert '("volvio", "estancado", "arrastra")' in src
    assert "nuevo" not in src.split("piden =")[1].split("\n")[0]


def test_el_orden_NO_se_reimplementa_en_SQL():
    """Un `ORDER BY` sería una segunda copia del criterio, y ya sabemos cómo
    termina eso: dos lectores que ordenan distinto sin fallar nunca."""
    from api.services import av_agent_items
    src = codigo(av_agent_items.que_importa)
    assert "ciclo.prioridad" in src
    assert "ORDER BY" not in src.upper()


# ── lo que nadie volvió a mirar ─────────────────────────────────────────────

def test_sin_mirar_distingue_SIGUE_ROTO_de_NADIE_LO_MIRO():
    """Los dos se ven idénticos —una fila abierta— y no son lo mismo. Si el
    origen volvió a correr y a éste no lo refrescó, el detector pasó y NO lo
    evaluó: es el punto ciego de §0.be, mostrado en vez de simplemente
    no-cerrado."""
    from api.services.av_agent_items import _sin_mirar
    fresco = it(clave="a", origen="live", ultimo_at=AHORA)
    viejo = it(clave="b", origen="live", ultimo_at=AHORA - timedelta(hours=9))
    r = _sin_mirar([fresco, viejo], AHORA)
    assert [x["clave"] for x in r] == ["b"]
    assert r[0]["horas_sin_reevaluar"] >= 8


def test_sin_mirar_NO_confunde_a_dos_de_la_MISMA_corrida():
    """Dos objetos de una misma pasada se escriben con segundos de diferencia
    y eso no es que uno quedó sin evaluar."""
    from api.services.av_agent_items import _sin_mirar
    a = it(clave="a", origen="live", ultimo_at=AHORA)
    b = it(clave="b", origen="live", ultimo_at=AHORA - timedelta(seconds=40))
    assert _sin_mirar([a, b], AHORA) == []


def test_sin_mirar_compara_contra_SU_PROPIO_origen():
    """El control corre de noche y el centinela cada 5 minutos: medir uno con
    la vara del otro marcaría al control como abandonado todas las mañanas."""
    from api.services.av_agent_items import _sin_mirar
    live = it(clave="a", origen="live", ultimo_at=AHORA)
    control = it(clave="b", origen="control", ultimo_at=AHORA - timedelta(hours=9))
    assert _sin_mirar([live, control], AHORA) == []


# ── y que se VEA ─────────────────────────────────────────────────────────────

def test_la_vista_lo_publica():
    """Una medición que no se ve no existe (§0.l) — el escalonado ya se comió
    esa lección una vez, construido entero sin que nadie lo llamara."""
    from api.services.av_agent_vista import vista
    assert '"que_importa"' in codigo(vista)


def test_si_la_memoria_falla_la_pantalla_se_dibuja_igual():
    from api.services.av_agent_vista import _que_importa_corto
    assert "except Exception" in codigo(_que_importa_corto)
