"""api/services/curvas_vista.py — la tab CURVAS del rediseño, en UN request.

Doc madre: `docs/RENTA_FIJA.md` §0 (paso 3a). Service PURO (sin FastAPI).

**Qué reemplaza.** Hoy la tab arma la tabla con 4 fetches y clasifica del lado
del navegador: `renta-fija` (los 398 instrumentos del snapshot), `titulos/flujos`
(240 KB — el cronograma COMPLETO de los 222 bonos, del que usa 5 campos para
armar el mapa ticker→curva) y dos `fair-value`. Con los ejes ya en la base
(paso 2) **el backend sabe qué es cada bono**, así que ese mapa deja de existir:
se sirve todo clasificado en una sola respuesta.

**Lo que NO cambia**: qué bono cae en qué pill. Eso lo decide `core.curvas_ejes`,
el MISMO módulo que usó el test de equivalencia — no hay una segunda copia de la
regla que pueda divergir de la del front.

**Lo que sí cambia** (y es lo que se buscaba): entran las ONs. `emisor_tipo` viaja
en cada bono para que el filtro de EMISOR sea client-side y gratis: cambiar de
emisor no vuelve a pegarle al backend.

**Frescura**: TTL 10s, igual que `get_renta_fija` — la tabla es live y el motor
de curvas refresca cada 5s.
"""
from __future__ import annotations

from api.cache import cached
from api.services._sql import _f, _q
from api.services.renta_fija_sql import _METRIC_COLS
from core import curvas_ejes as ce

# El orden en que se muestran las pills dentro de cada lado.
_ORDEN = {"tasa_fija": 1, "cer": 2, "tamar": 3, "duales": 4,
          "hard_dolar": 1, "dolar_linked": 2}

_EMISOR_LABEL = {"soberano": "SOBERANO", "provincial": "PROVINCIAL",
                 "corporativo": "CORPORATIVO", "bcra": "BCRA"}


def _bonos_crudos() -> list[dict]:
    """`mercado.curvas` con sus ejes + el snapshot live, en UNA query.

    El join va del master (222 filas) al snapshot y no al revés: la vista muestra
    BONOS, no tickers de pantalla. `market_snapshot` tiene 398 filas porque
    incluye especies y plazos que no son instrumentos del master — traerlos para
    descartarlos en el navegador es justamente lo que se está sacando.
    """
    cols = ", ".join(f"s.{c}" for c, _ in _METRIC_COLS)
    return _q(
        f"SELECT c.ticker AS ticker_corto, c.instrumento AS ticker, c.curva, c.tipo, "
        f"c.fecha_vencimiento, c.emisor, c.emisor_tipo, c.moneda_eje, c.ajuste, "
        f"c.ajuste_alt, c.ley, c.flujo_vencimiento, e.industria, {cols} "
        f"FROM mercado.curvas c "
        f"LEFT JOIN mercado.market_snapshot s ON s.ticker = c.instrumento "
        # La INDUSTRIA se resuelve desde el EMISOR en la LECTURA, no se guarda en
        # el bono. Ese es todo el punto: el dato existe UNA vez y no puede
        # contradecirse consigo mismo. El LEFT JOIN devuelve NULL cuando el emisor
        # todavía no tiene industria — y NULL es "sin clasificar", que la vista
        # tiene que MOSTRAR como bucket propio, nunca mezclar con `otros`.
        f"LEFT JOIN mercado.emisores e "
        f"       ON upper(btrim(e.emisor)) = upper(btrim(c.emisor))",
    )


def _fijados_cortos() -> set[str]:
    """Tickers CER con el CER de liquidación ya publicado (se comportan como tasa
    fija). MISMA fuente que la vista de hoy — si esto se calculara distinto, un
    bono cambiaría de pill en silencio."""
    from api.services.renta_fija import _bonos_cer_fijados
    return {t.split(" - ")[2].strip() if " - " in t else t.strip()
            for t in (_bonos_cer_fijados() or [])}


def _armar(rows: list[dict], fijados: set[str]) -> dict:
    """Puro: filas crudas → payload de la vista. Testeable sin base."""
    bonos: list[dict] = []
    sin_clasificar: list[str] = []
    n_pill: dict[str, int] = {}
    n_emisor: dict[str, int] = {}

    for r in rows:
        tc = (r.get("ticker_corto") or "").strip()
        ejes = None
        if r.get("emisor_tipo") and r.get("moneda_eje") and r.get("ajuste"):
            ejes = ce.Ejes(r["emisor_tipo"], r["moneda_eje"], r["ajuste"],
                           r.get("ley"), r.get("ajuste_alt"))
        if ejes is None:
            sin_clasificar.append(tc)
            continue
        fijado = tc in fijados
        del_bono = ce.pills(ejes, fijado)
        if not del_bono:          # badlar/tpm/caución: sin pill acordada todavía
            sin_clasificar.append(tc)
            continue

        metrics = {}
        for col, key in _METRIC_COLS:
            v = _f(r.get(col))
            if v is not None:
                metrics[key] = v

        # UNA FILA POR PILL. Un dual CER+TAMAR sale dos veces, con la misma ficha
        # y distinto `pill`/`lado`, y así aparece en las dos tablas — que es donde
        # el trader lo busca. Se emite repetido en vez de mandar una lista de pills
        # a propósito: el front ya filtra por `b.pill === pill`, así que el
        # contrato NO cambia y no hace falta que los dos deploys sean simultáneos
        # (el front va a Vercel solo; el backend se sube a mano y siempre después).
        for pill in del_bono:
            bonos.append({
                "ticker_corto": tc, "instrumento": r.get("ticker"),
                "pill": pill, "lado": ce.LADO[pill],
                "emisor_tipo": ejes.emisor_tipo, "emisor": r.get("emisor"),
                "moneda": ejes.moneda, "ajuste": ejes.ajuste,
                "ajuste_alt": ejes.ajuste_alt, "ley": ejes.ley,
                # Solo para corporativos: un soberano no tiene industria, y
                # mandarla en null para todos haría que el filtro del gráfico
                # muestre un bucket "sin clasificar" con 60 soberanos adentro.
                "industria": (r.get("industria")
                              if ejes.emisor_tipo == "corporativo" else None),
                "tipo": r.get("tipo"), "vencimiento": r.get("fecha_vencimiento"),
                "cer_fijado": fijado,
                "flujo_vencimiento": _f(r.get("flujo_vencimiento")),
                "metrics": metrics,
            })
            n_pill[pill] = n_pill.get(pill, 0) + 1
        # El emisor cuenta BONOS, no filas: un dual que sale en dos pills sigue
        # siendo un bono. Sin este cuidado el filtro EMISOR diría 222 sobre 221 y
        # nadie lo notaría — el contador simplemente estaría un poco alto.
        n_emisor[ejes.emisor_tipo] = n_emisor.get(ejes.emisor_tipo, 0) + 1

    pills = sorted(
        ({"codigo": p, "display": ce.DISPLAY[p], "lado": ce.LADO[p],
          "orden": _ORDEN.get(p, 99), "n": n_pill.get(p, 0)} for p in ce.PILLS),
        key=lambda x: (x["lado"], x["orden"]),
    )
    emisores = sorted(
        ({"codigo": e, "label": _EMISOR_LABEL.get(e, e.upper()), "n": n}
         for e, n in n_emisor.items()),
        key=lambda x: -x["n"],
    )
    return {
        "pills": pills,
        "emisores": emisores,
        "bonos": bonos,
        # NO se ocultan: un bono sin clasificar tiene que ser visible como
        # pendiente, no desaparecer. Son los que 1816 no tiene + los ajustes sin
        # pill (badlar/tpm/caución).
        "sin_clasificar": sorted(t for t in sin_clasificar if t),
    }


@cached(ttl=10)
def get_curvas_vista() -> dict:
    """La tab CURVAS entera: catálogo de pills, filtro de emisores y los bonos ya
    clasificados con sus métricas live."""
    return _armar(_bonos_crudos(), _fijados_cortos())
