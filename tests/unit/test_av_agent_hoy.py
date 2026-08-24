"""AHORA ES EL DÍA DE HOY, NO EL ACUMULADO (§0.bo).

La tab decía **AHORA 1** y abajo mostraba un control abierto hacía 21 horas,
con 131 filas plegadas, 40 resueltas y el seguimiento de arreglos viejos.
El user: *«no entiendo cuál es la diferencia entre AHORA y ENCONTRÓ si está
todo mezclado»*.

No era un bug de render: **las dos tabs eran un backlog acumulado**, con
nombres distintos. Por eso no se podía explicar la diferencia — no la había.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.services import av_agent_centinela as c

from ._fuente import codigo

AHORA = datetime.now(UTC)


def _f(**k) -> dict:
    k.setdefault("abierto_at", AHORA.isoformat())
    k.setdefault("vuelto_at", None)
    k.setdefault("resuelto_at", None)
    return k


def test_las_tres_novedades_del_dia():
    r = c._lo_de_hoy(
        [_f(abierto_at=AHORA.isoformat()),
         _f(abierto_at=(AHORA - timedelta(days=3)).isoformat(),
            vuelto_at=AHORA.isoformat())],
        [_f(resuelto_at=AHORA.isoformat())])
    assert len(r["aparecio"]) == 1
    assert len(r["volvio"]) == 1
    assert len(r["se_arreglo"]) == 1


def test_lo_que_ARRASTRA_no_entra():
    """Es la queja entera: un control abierto AYER no es una novedad del día.
    Es trabajo pendiente y vive en ENCONTRÓ, con sus botones.

    ⚠️ **Decía «hace 21 h» y era un test que fallaba según la hora.** El corte
    es el arranque del día EN ART (`_arranco_el_dia`), así que 21 h atrás cae en
    el día anterior sólo si son menos de las 21:00 — a las 21:08 del 2026-08-21
    el caso «viejo» era del MISMO día y el test se cayó. No fallaba por el
    código: fallaba por el reloj, que es la peor clase de test rojo (aparece
    sin que nadie haya tocado nada y enseña a ignorarlo).

    Ahora se ancla al MISMO corte que usa la función, un segundo antes: eso es
    «ayer» a cualquier hora."""
    viejo = _f(abierto_at=(c._arranco_el_dia() - timedelta(seconds=1)).isoformat())
    r = c._lo_de_hoy([viejo], [])
    assert r["aparecio"] == [] and r["volvio"] == []
    assert r["novedades"] == 0


def test_lo_que_VOLVIO_no_se_cuenta_dos_veces():
    """Volvió hoy pero nació hace tres días: es UNA novedad («volvió»), no dos.
    Contarlo también en «apareció» inflaría el número que decide si la tab
    dice algo."""
    v = _f(abierto_at=(AHORA - timedelta(days=3)).isoformat(),
           vuelto_at=AHORA.isoformat())
    r = c._lo_de_hoy([v], [])
    assert len(r["volvio"]) == 1 and r["aparecio"] == []
    assert r["novedades"] == 1


def test_lo_que_SE_ARREGLO_no_suma_al_contador():
    """Es una buena noticia, no algo que pida atención. Si sumara, el número de
    la tab subiría cuando el sistema MEJORA."""
    r = c._lo_de_hoy([], [_f(resuelto_at=AHORA.isoformat())])
    assert len(r["se_arreglo"]) == 1
    assert r["novedades"] == 0


def test_el_corte_es_en_hora_ARGENTINA_y_no_en_UTC():
    """El día UTC arranca a las 21:00 de acá: con el corte en UTC, algo de las
    21:30 de anoche saldría como «de hoy» junto con lo de esta mañana — dos
    días mezclados bajo el mismo rótulo, justo en la tab que no puede fallar."""
    from zoneinfo import ZoneInfo
    d = c._arranco_el_dia()
    local = d.astimezone(ZoneInfo("America/Argentina/Buenos_Aires"))
    assert (local.hour, local.minute, local.second) == (0, 0, 0)
    assert d <= datetime.now(UTC)


def test_una_fecha_rota_NO_cuenta_como_de_hoy():
    """Ante la duda, afuera: meter basura en la lista del día es peor que no
    mostrarla, porque esta tab se lee como «esto es lo que pasa»."""
    assert c._hoy("no-es-una-fecha", c._arranco_el_dia()) is False
    assert c._hoy(None, c._arranco_el_dia()) is False


def test_VOLVIO_sale_del_OBJETO_y_no_de_reaperturas():
    """`av_agent_centinela` no tenía `vuelto_at`: tenía `reaperturas`, un
    contador SIN fecha que no puede decir si volvió HOY. Se traía con un JOIN
    (Fase 2) y desde la Fase 3 es una columna de la única tabla que se lee.

    Lo que el test congela es que la fecha viaje: sin ella, «volvió hoy» y
    «volvió tres veces alguna vez» se dibujan igual."""
    src = codigo(c.estado)
    assert '"vuelto_at"' in src
    assert "FROM agente.av_agent_items" in src


def test_la_pantalla_recibe_el_corte_hecho():
    """El navegador no puede mirar el reloj mientras dibuja, y el criterio de
    «hoy» tiene que ser uno solo para las dos tabs."""
    assert '"hoy": _lo_de_hoy(' in codigo(c.estado)


# ── hora argentina en los mensajes (§0.br) ───────────────────────────────────

def test_los_mensajes_de_SALUD_no_dicen_UTC():
    """⚠️ Decían «la corrida de 20/08 16:30 UTC falló» sobre un datetime en UTC:
    la mesa leía 16:30 cuando acá eran las 13:30. No es solo la etiqueta —
    pedirle a alguien que reste tres horas para ubicar un hecho garantiza que
    lo ubique mal."""
    import inspect

    from api.services import salud
    src = inspect.getsource(salud._chequeo_job) if hasattr(salud, "_chequeo_job") \
        else inspect.getsource(salud)
    assert "UTC falló" not in src
    assert "UTC y la última fue" not in src


def test_el_formateo_de_hora_vive_UNA_vez():
    """`core.tz` existía desde siempre y esta capa no lo usaba: escribía su
    propio `strftime`. Dos formateadores de la misma fecha es cómo un día dicen
    distinto."""
    import inspect

    from api.services import salud
    from core.tz import hora_ar
    assert "hora_ar" in inspect.getsource(salud)
    from datetime import UTC, datetime
    # Naive → se asume UTC (así los guarda Postgres acá) y se pasa a ART.
    assert hora_ar(datetime(2026, 8, 20, 16, 30)) == "20/08 13:30"
    assert hora_ar(datetime(2026, 8, 20, 16, 30, tzinfo=UTC)) == "20/08 13:30"
    assert hora_ar(None) == "nunca"


def test_la_vista_manda_abierto_at_y_no_lo_tira():
    """Se leía del JOIN, se usaba para `dias_abierto` y se descartaba. Va en ISO
    y no formateado: ya escrito no se podría ordenar sin re-parsear el texto."""
    from api.services.av_agent_vista import _hallazgos_ultima_corrida
    src = codigo(_hallazgos_ultima_corrida)
    assert 'h["abierto_at"]' in src and "isoformat()" in src
    assert 'h.pop("abierto_at", None)' not in src


# ── LA COMPARACIÓN ERA CIRCULAR (§0.bu) ──────────────────────────────────────

_MEP = 1525.0


def _bono_usd(tk: str, simbolo: str) -> dict:
    return {"ticker_corto": tk, "ticker": simbolo, "moneda_eje": "USD",
            "valor_nominal": 100, "curva": "corporativos"}


def test_canta_la_pata_del_EJE_aunque_es_default_sea_la_que_ya_usa():
    """⚠️⚠️ **EL CASO QUE NO DETECTABA, medido en prod: 11 de 11.**

    `especies.es_default` se marca con `p["simbolo"] == curvas.instrumento`, o
    sea que **es una COPIA del master**. El detector la comparaba contra el
    master: la comparación era vacía por construcción y solo se disparaba
    cuando el seeder había quedado viejo.

    Acá VSCYO tiene su pata en dólares existente y validada, `es_default`
    apunta a la de pesos (porque es la que el master usa) y el detector tiene
    que cantar igual.
    """
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim_ars = "MERV - XMEV - VSCYO - 24hs"
    hs = detectar_precio_fuera_de_moneda(
        [_bono_usd("VSCYO", sim_ars)],
        {sim_ars: {"last_price": 152_500.0}},   # pesos: ÷MEP da paridad 100
        _MEP,
        set(),
        {"VSCYO": sim_ars},                     # es_default == lo que ya usa
        None,
        {"VSCYO": [
            {"simbolo": sim_ars, "especie": "pesos", "plazo": "24hs"},
            {"simbolo": "MERV - XMEV - VSCYD - 24hs", "especie": "mep", "plazo": "24hs"},
            {"simbolo": "MERV - XMEV - VSCYD - CI", "especie": "mep", "plazo": "CI"},
        ]})
    assert [h["regla"] for h in hs] == ["pata_equivocada"]
    # Y nombra la pata correcta: 24hs sobre CI, que es donde hay liquidez.
    assert hs[0]["evidencia"]["sugerido"] == "MERV - XMEV - VSCYD - 24hs"


def test_una_ON_sin_pata_en_dolares_NO_se_canta():
    """La guarda que evitó 137 falsos positivos en el seeder: un hard dollar
    corporativo que cotiza en su ÚNICA especie no está cruzado — no hay a dónde
    apuntar. «No existe» no es «está mal»."""
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim = "MERV - XMEV - SOLO1 - 24hs"
    hs = detectar_precio_fuera_de_moneda(
        [_bono_usd("SOLO1", sim)], {sim: {"last_price": 152_500.0}}, _MEP,
        set(), {"SOLO1": sim}, None,
        {"SOLO1": [{"simbolo": sim, "especie": "pesos", "plazo": "24hs"}]})
    assert [h["regla"] for h in hs] == ["cotiza_en_pesos"], (
        "sin pata a la que apuntar, es contexto y no un dato mal cargado")


def test_sin_patas_cargadas_cae_a_es_default_y_no_se_queda_mudo():
    """Peor criterio, pero mejor que no decir nada: si `especies` no tiene las
    patas de ese ticker, el respaldo sigue siendo el default."""
    from api.services.av_agent import detectar_precio_fuera_de_moneda
    sim = "MERV - XMEV - AL30 - 24hs"
    hs = detectar_precio_fuera_de_moneda(
        [_bono_usd("AL30", sim)], {sim: {"last_price": 152_500.0}}, _MEP,
        set(), {"AL30": "MERV - XMEV - AL30D - 24hs"}, None, {})
    assert [h["regla"] for h in hs] == ["pata_equivocada"]


def test_el_criterio_del_eje_vive_UNA_vez():
    """La lógica ya existía en `scripts/sembrar_especies` (su lista `cruzadas`)
    y **solo se imprimía por consola**. Reescribirla en el detector habría sido
    la tercera opinión sobre qué pata corresponde."""
    from api.services import av_agent
    assert "pata_para_el_eje" in codigo(av_agent.detectar_precio_fuera_de_moneda)
