"""copiloto/series.py — SERIE HISTÓRICA GENÉRICA: "¿contra qué?".

Sale del hallazgo #1 de la auditoría (`docs/TOOLS_IA.md`): 17 de 55 propuestas
eran **la misma pregunta con distinta ropa** — *¿esto está alto o bajo contra
su propia historia o contra el período anterior?*.

La causa raíz no era falta de datos: la historia está guardada y con cron vivo.
Era que (a) los services están escritos para pintar una vista y devuelven la
foto de hoy, y (b) al modelo le está PROHIBIDO restar (`base.py`: "prohibida la
aritmética propia"). O sea: **si el código no calcula la comparación, la
comparación no existe** — y nadie era dueño de ese cálculo.

## Por qué UNA tool y no ocho

Ocho tools con ocho descriptions es ocho veces la chance de que el modelo elija
mal, y ocho lugares donde resolver lo mismo: el capeo de tokens, el etiquetado
de la ventana, el "no hay histórico", el submuestreo. Acá se resuelve una vez.

## El registro (mismo patrón que copiloto/registro.py)

Agregar una serie es una fila de `_SERIES`, no una tool nueva:
    "nombre": {etiqueta, unidad, escala, reader, ayuda}
`reader(clave, desde, hasta) -> [(fecha_iso, valor)]` — lo único específico.

## El contrato de salida (igual para todas)

SIEMPRE stats + una muestra ralificada; **jamás los puntos crudos** (es la
razón principal por la que conviene la tool única: el costo en tokens se
controla en un solo lugar). Los stats los calcula `quant.stats` cuando puede.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

logger = logging.getLogger(__name__)

_MAX_MUESTRA = 24       # puntos que se le muestran al modelo
_VENTANA_DEFAULT = 180  # días si no piden rango


def _hoy() -> date:
    return (datetime.now(UTC) - timedelta(hours=3)).date()


def _rango(desde: str | None, hasta: str | None) -> tuple[str, str]:
    h = (hasta or "").strip() or _hoy().isoformat()
    d = (desde or "").strip() or (
        date.fromisoformat(h) - timedelta(days=_VENTANA_DEFAULT)).isoformat()
    return d, h


# ── Readers: lo ÚNICO específico de cada serie ───────────────────────────────

def _reader_macro(clave: str, desde: str, hasta: str) -> list[tuple[str, float]]:
    """Series macro (CER, inflación, riesgo país, badlar, MEP/CCL, caución…).
    `obtener_serie_macro` toma una VENTANA en días, no un rango → se pide la
    ventana que cubre `desde` y se recorta acá."""
    from api.services import macro_sql

    dias = max(1, (date.fromisoformat(hasta) - date.fromisoformat(desde)).days + 30)
    r = macro_sql.obtener_serie_macro(variable=clave, ventana_dias=min(dias, 3650)) or {}
    return [(str(p.get("fecha"))[:10], float(p["valor"]))
            for p in (r.get("serie") or [])
            if p.get("valor") is not None
            and desde <= str(p.get("fecha"))[:10] <= hasta]


def _reader_1816(clave: str, desde: str, hasta: str) -> list[tuple[str, float]]:
    """Bonos del watch 1816: `clave` = "TICKER:campo" (ej. "TX26:tea")."""
    from api.services import research_1816_sql

    ticker, _, campo = clave.partition(":")
    campo = campo or "tea"
    r = research_1816_sql.series([ticker.strip().upper()], campo, desde, hasta) or {}
    series = r.get("series") or []
    if not series:
        return []
    escala = 100.0 if campo in ("tea", "paridad") else 1.0
    return [(str(f), float(v) * escala)
            for f, v in (series[0].get("puntos") or []) if v is not None]


def _reader_aum(clave: str, desde: str, hasta: str) -> list[tuple[str, float]]:
    """AuM valorizado por día. `clave` = cartera (HD/DL/ARS/FCI) o "" = total."""
    from api.services.asistente_tools import _aum_serie_diaria

    return _aum_serie_diaria(desde, hasta, (clave or "").strip() or None, None)


def _reader_bcra(clave: str, desde: str, hasta: str) -> list[tuple[str, float]]:
    """Variables monetarias del BCRA (tab Research → BCRA). Los ids son
    ENTEROS (research_bcra_sql.series(ids: list[int], …))."""
    from api.services import research_bcra_sql

    try:
        idv = int(str(clave).strip())
    except (TypeError, ValueError):
        return []
    r = research_bcra_sql.series([idv], desde, hasta) or {}
    series = r.get("series") or []
    if not series:
        return []
    return [(str(f)[:10], float(v)) for f, v in (series[0].get("puntos") or [])
            if v is not None]


def _reader_fred(clave: str, desde: str, hasta: str) -> list[tuple[str, float]]:
    """Series internacionales (FRED): tasas US, commodities, liquidez."""
    from api.services import research_fred_sql

    r = research_fred_sql.series([str(clave).strip()], desde, hasta) or {}
    series = r.get("series") or []
    if not series:
        return []
    return [(str(f)[:10], float(v)) for f, v in (series[0].get("puntos") or [])
            if v is not None]


# ── El registro ──────────────────────────────────────────────────────────────

_SERIES: dict[str, dict] = {
    "macro": {
        "etiqueta": "serie macro argentina",
        "unidad": "",
        "reader": _reader_macro,
        "ayuda": "clave = variable macro (ej. cer, inflacion, riesgo_pais, "
                 "badlar, tamar, dolar_oficial, mep, ccl)",
    },
    "bono_1816": {
        "etiqueta": "bono del watch 1816",
        "unidad": "%",
        "reader": _reader_1816,
        "ayuda": "clave = 'TICKER:campo' con campo en tea|paridad|precioClean|"
                 "duration (ej. 'TX26:tea')",
    },
    "aum": {
        "etiqueta": "AuM valorizado",
        "unidad": "ARS",
        "reader": _reader_aum,
        "ayuda": "clave = cartera (HD, DL, ARS, FCI…) o vacío para el total de "
                 "la mesa",
    },
    "bcra": {
        "etiqueta": "variable monetaria del BCRA",
        "unidad": "",
        "reader": _reader_bcra,
        "ayuda": "clave = id de la variable BCRA del watch de Research",
    },
    "internacional": {
        "etiqueta": "serie internacional (FRED)",
        "unidad": "",
        "reader": _reader_fred,
        "ayuda": "clave = id de la serie FRED del watch (ej. DGS10)",
    },
}


def series_disponibles() -> list[str]:
    return sorted(_SERIES)


# ── Stats + formato (el contrato único de salida) ───────────────────────────

def _stats(valores: list[float]) -> dict:
    """Percentil y z del ÚLTIMO valor contra su propia historia — que es la
    pregunta real ('¿está alto o bajo?'), no la serie."""
    import statistics

    n = len(valores)
    ult = valores[-1]
    menores = sum(1 for v in valores if v <= ult)
    out = {
        "n": n, "primero": valores[0], "ultimo": ult,
        "min": min(valores), "max": max(valores),
        "media": statistics.fmean(valores),
        "mediana": statistics.median(valores),
        "percentil": 100.0 * menores / n if n else None,
        "z": None,
    }
    if n >= 3:
        sd = statistics.pstdev(valores)
        out["z"] = (ult - out["media"]) / sd if sd else 0.0
    return out


def _fmt(v: float, unidad: str) -> str:
    if unidad == "ARS":
        if abs(v) >= 1e9:
            return f"{v / 1e9:.2f} mil M"
        if abs(v) >= 1e6:
            return f"{v / 1e6:.1f} M"
        return f"{v:,.0f}".replace(",", ".")
    return f"{v:,.2f}{unidad}".replace(",", "")


def serie_historica(que: str, clave: str = "", desde: str | None = None,
                    hasta: str | None = None) -> str:
    """La tool. Devuelve SIEMPRE el mismo bloque: qué es, la ventana REAL
    (no la pedida — nadie más va a decir "el último año" cuando son 6 meses),
    stats de valor relativo y una muestra ralificada."""
    cfg = _SERIES.get((que or "").strip())
    if cfg is None:
        return (f"no conozco la serie {que!r}. Disponibles: "
                + " · ".join(f"{k} ({v['ayuda']})" for k, v in _SERIES.items()))
    d, h = _rango(desde, hasta)
    try:
        puntos = cfg["reader"]((clave or "").strip(), d, h) or []
    except Exception as e:
        logger.warning("series: reader de %s falló (%s)", que, e)
        return f"no pude leer esa serie ({type(e).__name__})"
    puntos = [(f, v) for f, v in puntos if v is not None]
    if not puntos:
        return (f"sin datos de {cfg['etiqueta']} {clave or ''} entre {d} y {h}. "
                "Puede que el período sea anterior al primer registro guardado.")
    puntos.sort(key=lambda p: p[0])
    vals = [v for _f, v in puntos]
    st = _stats(vals)
    u = cfg["unidad"]

    # submuestreo: el modelo NUNCA recibe los puntos crudos (control de tokens
    # en UN solo lugar — la razón principal de que esta tool sea única)
    paso = max(1, len(puntos) // _MAX_MUESTRA)
    muestra = puntos[::paso][-_MAX_MUESTRA:]
    if muestra[-1][0] != puntos[-1][0]:
        muestra.append(puntos[-1])

    var = ""
    if st["primero"]:
        var = f" ({100 * (st['ultimo'] / st['primero'] - 1):+.1f}% en el período)"
    lineas = [
        f"[{cfg['etiqueta']} {clave} — {puntos[0][0]} a {puntos[-1][0]}, "
        f"{st['n']} registros]",
        f"  último {_fmt(st['ultimo'], u)}{var} · primero {_fmt(st['primero'], u)}",
        f"  mín {_fmt(st['min'], u)} · máx {_fmt(st['max'], u)} · "
        f"media {_fmt(st['media'], u)} · mediana {_fmt(st['mediana'], u)}",
    ]
    if st["percentil"] is not None:
        lectura = ("alto contra su historia" if st["percentil"] >= 80 else
                   "bajo contra su historia" if st["percentil"] <= 20 else
                   "en zona media")
        z = f" · z {st['z']:+.2f}" if st["z"] is not None else ""
        lineas.append(f"  VALOR RELATIVO: percentil {st['percentil']:.0f}{z} → {lectura}")
    lineas.append("  muestra: " + ", ".join(f"{f} {_fmt(v, u)}" for f, v in muestra))
    return "\n".join(lineas)


TOOL_SERIE = {
    "type": "function",
    "function": {
        "name": "serie_historica",
        "description": (
            "La HISTORIA de cualquier serie del sistema y —lo importante— si el "
            "valor de hoy está ALTO o BAJO contra esa historia (percentil y "
            "desvíos). Usala SIEMPRE que la pregunta compare con el pasado: "
            "'¿cómo venía?', '¿está caro?', '¿subió contra el mes pasado?', "
            "'la evolución de…'. El contexto que tenés es la foto de HOY: sin "
            "esta herramienta no podés responder nada de eso, y tenés PROHIBIDO "
            "calcular la comparación vos."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "que": {
                    "type": "string",
                    "enum": sorted(_SERIES),
                    "description": "Qué familia de serie: macro (CER, inflación, "
                                   "riesgo país, dólar), bono_1816 (TEA/paridad de "
                                   "un bono), aum (AuM de la mesa por cartera), "
                                   "bcra (variables monetarias), internacional "
                                   "(FRED: tasas US, commodities).",
                },
                "clave": {
                    "type": "string",
                    "description": "Cuál dentro de esa familia. macro: cer, "
                                   "inflacion, riesgo_pais, badlar… · bono_1816: "
                                   "'TICKER:campo' (ej. 'TX26:tea') · aum: la "
                                   "cartera (HD, FCI…) o vacío para el total · "
                                   "bcra/internacional: el id de la serie.",
                },
                "desde": {"type": "string", "description": "YYYY-MM-DD (opcional)."},
                "hasta": {"type": "string", "description": "YYYY-MM-DD (opcional)."},
            },
            "required": ["que"],
        },
    },
}


def ejecutar_serie(nombre: str, args: dict) -> str:
    if nombre != "serie_historica":
        return f"herramienta desconocida: {nombre}"
    return serie_historica(str(args.get("que") or ""), str(args.get("clave") or ""),
                           args.get("desde"), args.get("hasta"))
