"""core/mercado_1816.py — cliente de la API de Mercado de 1816 (vista RESEARCH).

Doc madre: docs/VISTA_RESEARCH.md. Cambiar de proveedor / URL = tocar solo acá.

Lo que resuelve (verificado el 2026-07-18):
- **Auth**: POST /v1/auth/token (apiKey + module=mercado) → JWT 24h. Cacheado en
  memoria del proceso; re-auth automático al vencer o ante 401.
- **RATE LIMIT DURO (429)**: la API tira "Demasiadas solicitudes" al encadenar
  llamadas. Por eso hay THROTTLE (intervalo mínimo entre requests) + BACKOFF
  exponencial en 429. NADA de hammering — este cliente es para jobs de fondo, la
  latencia no importa.
- **Créditos**: se controlan con balance() (100k/día, 3.1M/mes); el consumo por
  endpoint está en docs/VISTA_RESEARCH.md §4.2.

Env vars (.env del Droplet):
  MERCADO_1816_API_KEY   — sin ella el cliente está apagado (disponible() = False).
  MERCADO_1816_BASE_URL  — override, default https://api.1816.com.ar (verificado).
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import threading
import time

from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

logger = logging.getLogger(__name__)

_BASE = os.getenv("MERCADO_1816_BASE_URL", "https://api.1816.com.ar").rstrip("/")
_MIN_INTERVALO_S = 2.5   # throttle mínimo entre llamadas (contra el 429)
_TIMEOUT = 45
_MAX_REINTENTOS = 5

_lock = threading.Lock()
_estado: dict = {"token": None, "exp": 0.0, "ultima": 0.0}


class Error1816(RuntimeError):
    """Fallo de la API de 1816 (auth, rate limit agotado, HTTP no-200)."""


def _api_key() -> str | None:
    return os.getenv("MERCADO_1816_API_KEY")


def disponible() -> bool:
    """True si hay API key configurada (sin ella el cliente no opera)."""
    return bool(_api_key())


_RE_ESPECIE = re.compile(r"^([A-Z]+\d+)[DC]$")   # AL30D/GD30C → AL30/GD30


def normalizar_ticker(t: str | None) -> str:
    """Nuestro ticker → la forma de 1816: mayúsculas y sin la ESPECIE (D/C) final.

    Vive acá porque es una convención DEL PROVEEDOR: nuestros `ticker_corto`
    traen la especie (AL30D/GD30C) y 1816 publica el base (AL30/GD30). Estaba
    duplicada en el job de discovery y en los diags; si las dos copias divergen,
    el cruce de cada una da un universo distinto y nadie se entera.
    Exige letras + dígitos antes de la especie para no mutilar un ticker que
    termina en C/D sin serlo (p.ej. una ON como AER9O queda intacta).
    """
    t = (t or "").strip().upper()
    m = _RE_ESPECIE.match(t)
    return m.group(1) if m else t


def _auth() -> str:
    key = _api_key()
    if not key:
        raise Error1816("falta MERCADO_1816_API_KEY")
    import requests

    r = requests.post(f"{_BASE}/v1/auth/token",
                      json={"apiKey": key, "module": "mercado"}, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise Error1816(f"auth HTTP {r.status_code}: {r.text[:200]}")
    d = r.json()
    tok = d.get("token")
    if not tok:
        raise Error1816("auth sin token en la respuesta")
    _estado["token"] = tok
    _estado["exp"] = time.time() + int(d.get("expiresIn", 86400)) - 300  # 5 min margen
    logger.info("mercado_1816: token renovado (expira en %ss)", d.get("expiresIn"))
    return tok


def _token() -> str:
    with _lock:
        if not _estado["token"] or time.time() >= _estado["exp"]:
            _auth()
        return _estado["token"]


def _throttle() -> None:
    """Garantiza _MIN_INTERVALO_S entre llamadas (rate limit). Serializa con el lock
    para que dos hilos no disparen juntos."""
    with _lock:
        espera = _MIN_INTERVALO_S - (time.monotonic() - _estado["ultima"])
        if espera > 0:
            time.sleep(espera)
        _estado["ultima"] = time.monotonic()


def _get(path: str, params: dict | None = None) -> dict:
    """GET autenticado con throttle + backoff en 429 + re-auth en 401. Levanta
    Error1816 si se agotan los reintentos o ante un 4xx no recuperable."""
    import requests

    ultimo = ""
    for intento in range(_MAX_REINTENTOS):
        _throttle()
        try:
            r = requests.get(f"{_BASE}{path}",
                             headers={"Authorization": f"Bearer {_token()}"},
                             params=params, timeout=_TIMEOUT)
        except requests.RequestException as e:
            ultimo = f"{type(e).__name__}: {e}"
            time.sleep(3 * (intento + 1))
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:                      # rate limit → backoff
            espera = min(5 * 2 ** intento, 60)
            logger.warning("mercado_1816 429 en %s — backoff %ss (intento %s)",
                           path, espera, intento + 1)
            time.sleep(espera)
            continue
        if r.status_code == 401:                      # token vencido → re-auth
            with _lock:
                _estado["token"] = None
            ultimo = "401 (re-auth)"
            continue
        if r.status_code >= 500:                      # server → retry
            ultimo = f"HTTP {r.status_code}"
            time.sleep(3 * (intento + 1))
            continue
        raise Error1816(f"{path} HTTP {r.status_code}: {r.text[:200]}")  # 4xx no-recup.
    raise Error1816(f"{path}: agotados {_MAX_REINTENTOS} reintentos ({ultimo})")


# ── Endpoints ────────────────────────────────────────────────────────────────


def balance() -> dict:
    """{daily:{used,limit}, monthly:{used,limit}} — control de créditos."""
    return _get("/v1/creditos/balance")


def curvas(texto: str | None = None) -> list[dict]:
    return _get("/v1/mercado/curvas", {"texto": texto} if texto else None)


def instrumentos(texto: str | None = None, curva_id: int | None = None,
                 solo_performing: bool | None = None) -> list[dict]:
    """Catálogo de instrumentos. `solo_performing=False` incluye los VENCIDOS
    (el default de la API es true = solo vigentes). Costo: 1 crédito."""
    p: dict = {}
    if solo_performing is not None:
        p["soloPerforming"] = "true" if solo_performing else "false"
    if texto:
        p["texto"] = texto
    if curva_id:
        p["curvaId"] = curva_id
    return _get("/v1/mercado/instrumentos", p)


def indicadores(tickers: list[str], campos: list[str], fuente: str = "byma",
                plazo: int = 1, moneda: str = "ars",
                fecha_operacion: str | None = None) -> dict:
    """Indicadores a precio de mercado (≤50 tickers por llamada). Devuelve
    {fechaOperacion, fuente, plazo, moneda, instrumentos: {ticker: {campo: v}}}.
    Costo: tickers × campos. OJO: el default de la API es fechaOperacion=HOY —
    en fin de semana/feriado devuelve vacío (aprendido 2026-07-18, corrida de
    sábado con 0 datos): pasar `fecha_operacion` del último día hábil."""
    p: dict = {"tickers": list(tickers)[:50], "campos": list(campos),
               "fuente": fuente, "plazo": plazo, "moneda": moneda}
    if fecha_operacion:
        p["fechaOperacion"] = fecha_operacion
    return _get("/v1/mercado/indicadores", p)


# Cuántas ruedas retroceder cuando la pedida vuelve vacía. Cubre un fin de semana
# largo; más que eso ya no es "todavía no hubo rueda", es que el ticker no tiene
# datos y hay que decirlo en vez de seguir buscando.
MAX_RETROCESO = 4


def _habil_anterior(d: _dt.date) -> _dt.date:
    """Día hábil anterior (solo fines de semana). Los feriados los resuelve el
    retroceso por respuesta vacía — para eso no hace falta un calendario."""
    d -= _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


# ⚠️ **Campos que 1816 devuelve SIEMPRE**, haya operado el papel o no: son la
# ficha del pedido, no datos de mercado. Distinguirlos NO es cosmético — es lo
# que hace que «¿esta rueda trajo datos?» signifique algo.
#
# **El bug que esto arregla (2026-08-17, TMG27).** El predicado de "trajo datos"
# era `any(campo is not None)`. Mientras se pidieron 4 campos —todos de valor—
# funcionó. Al ampliar la lista a 13 para enriquecer el diagnóstico entraron
# `fuente`, `convencionTna` y `fechaLiquidacion`, que vienen llenos aunque el
# precio sea `null`: el predicado daba **True en la primera vuelta** y el
# retroceso **nunca corría**. Un domingo, o con un papel que no operó, el
# resultado era «no publicó precio» en vez de buscar la rueda anterior.
CAMPOS_METADATA = frozenset({
    "convencionTna", "denominacion", "fechaLiquidacion", "fechaOperacion",
    "fuente", "moneda", "plazo", "ticker",
})


def indicadores_vigentes(tickers: list[str], campos: list[str], *,
                         fecha: str | None = None, max_retroceso: int = MAX_RETROCESO,
                         al_retroceder=None, campos_dato: list[str] | None = None,
                         **kw) -> dict:
    """`indicadores` de la última rueda CON DATOS. Devuelve la respuesta cruda
    más `fechaOperacion` resuelta, o `{}` si ninguna rueda trajo nada.

    **Existe porque esta trampa se pagó dos veces.** Sin `fechaOperacion` la API
    usa HOY, y un domingo devuelve todos los campos en `null`: el 2026-08-16 eso
    hizo parecer que el campo `spread` no existía. Y antes de las 11 ART tampoco
    hay rueda de hoy — en los dos casos el número bueno es el del último día con
    datos, no un vacío.

    Estaba resuelto dentro de `jobs/tamar_1816` y solo ahí. Dos criterios para la
    misma pregunta terminan siempre igual: uno de los dos se queda viejo. Acá vive
    una vez y lo hereda el que llame.

    `al_retroceder(fecha)` es un callback opcional para logear el intento.

    `campos_dato` acota QUÉ campos deciden que la rueda sirve, sin acotar lo que
    se pide. Existe porque «tiene datos» depende del que pregunta: a
    `jobs/tamar_1816` le alcanza con la tasa, pero el simulador del AV AGENT
    necesita el **precio** —lo demás no le sirve para correr el motor— y una
    rueda con TEA modelada y sin operaciones lo dejaba plantado. Por default son
    todos los campos de valor pedidos.
    """
    # Solo los campos de VALOR deciden si la rueda trajo datos — la metadata viene
    # llena siempre y diría que sí aunque no haya un solo precio (ver CAMPOS_METADATA).
    # El `or list(campos)` cubre al que pide únicamente metadata: ahí no hay nada
    # mejor que el criterio viejo, y quedarse con una lista vacía haría que el
    # predicado fuera False SIEMPRE y agotara el retroceso contra la API.
    datos = list(campos_dato or [c for c in campos if c not in CAMPOS_METADATA]
                 or campos)
    d = _dt.date.fromisoformat(fecha) if fecha else _dt.date.today()
    if d.weekday() >= 5:
        d = _habil_anterior(d)
    for _ in range(max_retroceso + 1):
        resp = indicadores(list(tickers), list(campos),
                           fecha_operacion=d.isoformat(), **kw)
        inst = resp.get("instrumentos") or {}
        # "Trajo datos" = algún campo de VALOR no nulo en algún instrumento. No se
        # ata a un campo puntual: el que pide `precioClean` y el que pide `tea`
        # tienen la misma pregunta, y hardcodear uno rompería al otro en silencio.
        if any(v.get(c) is not None for v in inst.values() if v for c in datos):
            return {**resp, "fechaOperacion": resp.get("fechaOperacion") or d.isoformat()}
        if al_retroceder:
            al_retroceder(d)
        d = _habil_anterior(d)
    return {}


# ── Contrato REAL de la API (OpenAPI 1.1.0, `/v1/doc/openapi.json`) ─────────
#
# Relevado el 2026-08-17 contra el spec + `scripts/diag_1816_indicadores`. Antes
# se adivinaban los valores y la API rechaza la llamada ENTERA cuando uno no
# existe, así que un valor inventado no falla en su campo: hace fallar todo.

# ⚠️ **NO existe `usd`.** Y la diferencia entre estas tres NO es cosmética: el
# spec dice *«para instrumentos pagaderos en moneda distinta a ARS, para calcular
# indicadores las cotizaciones se dividen por CCL»* con el default `ars`.
# **NUESTRO motor divide por MEP** (`engines/curvas.py::precio_soberano_a_usd`),
# así que pedir el default y comparar tasas es comparar dos tipos de cambio
# distintos — eso fueron los 202 bps de GD46, no la fórmula.
MONEDAS = ("ars", "ccl", "mep")

# Los campos que `/indicadores` acepta, TEXTUAL del enum del spec.
CAMPOS_INDICADORES = (
    "convencionTna", "currentYield", "denominacion", "duration", "durationMod",
    "fechaLiquidacion", "fechaOperacion", "fuente", "moneda", "paridad", "plazo",
    "precioClean", "precioDirty", "spread", "tea", "tem", "ticker", "tna",
    "ultimaOperacion", "volumenMontoDiario", "volumenNominalDiario",
)

# Las referencias de cálculo que acepta el endpoint de INPUT MANUAL. **Exactamente
# UNA** por llamada (lo dice el spec y lo valida la API).
REFERENCIAS_MANUALES = ("precioClean", "precioDirty", "tna", "tea", "tem",
                        "spread", "paridad")


def indicadores_de(ticker: str, campos: list[str], *, moneda: str = "ars",
                   plazo: int = 1, fecha_operacion: str | None = None,
                   convencion_tna: str | None = None, **referencia) -> dict:
    """`/indicadores/{ticker}` — **input MANUAL**: le das UN precio y te devuelve
    los indicadores calculados a ESE precio.

    **Es el control cruzado perfecto y no lo estábamos usando.** Comparar nuestra
    TEA contra la de ellos tiene un problema: cada uno la calcula sobre SU precio,
    así que una diferencia puede ser la fórmula o puede ser el insumo, y no hay
    forma de saber cuál. Con esto se le pasa NUESTRO precio y lo que vuelve es su
    tasa sobre el MISMO número: cualquier diferencia que quede es pura convención
    o cronograma. Se elimina la variable.

    Costo: **campos** (no tickers × campos) — una comparación sale ~4 créditos.

    `referencia` tiene que traer EXACTAMENTE UNA de `REFERENCIAS_MANUALES`.
    """
    dadas = {k: v for k, v in referencia.items()
             if k in REFERENCIAS_MANUALES and v is not None}
    if len(dadas) != 1:
        raise Error1816(
            f"indicadores_de necesita EXACTAMENTE una referencia de cálculo "
            f"({', '.join(REFERENCIAS_MANUALES)}); llegaron {list(dadas) or 'ninguna'}")
    tk = normalizar_ticker(ticker)
    p: dict = {"campos": list(campos), "moneda": moneda, "plazo": plazo, **dadas}
    if fecha_operacion:
        p["fechaOperacion"] = fecha_operacion
    if convencion_tna:
        p["convencionTna"] = convencion_tna
    return _get(f"/v1/mercado/indicadores/{tk}", p)


def series(tickers: list[str], campos: list[str], desde: str, hasta: str,
           fuente: str = "byma", plazo: int = 1, moneda: str = "ars",
           convencion: str | None = None) -> dict:
    """Series históricas (≤10 tickers, ≤1 año). Devuelve el JSON crudo de la API;
    usar parse_series() para aplanarlo. Costo: tickers × campos × días."""
    p: dict = {"tickers": list(tickers), "campos": list(campos),
               "fechaInicial": desde, "fechaFinal": hasta,
               "fuente": fuente, "plazo": plazo, "moneda": moneda}
    if convencion:
        p["convencionTna"] = convencion
    return _get("/v1/mercado/series", p)


CAMPOS_CASHFLOW = ("fechaPagoEfectiva", "fechaPagoTeorica", "flujoAmortizacion",
                   "flujoInteres", "flujoTotal")


def cashflow(ticker: str, campos: list[str] | None = None) -> dict:
    """Cupones (cashflow) de UN instrumento — `GET /v1/mercado/cashflow/{ticker}`.

    Devuelve {ticker, fechaOperacion, plazo, cashflow: [{campo: valor}, …]} tal
    cual lo manda la API (nada se recalcula acá — decisión 5 de
    docs/VISTA_RESEARCH.md: 1816 es la fuente de la verdad de los números).
    Costo: **1 crédito por cupón** devuelto → un bono con 20 cupones sale 20.
    `campos` es obligatorio en la API; el default de acá pide los cinco.
    """
    tk = (ticker or "").strip().upper()
    if len(tk) < 3:
        raise Error1816(f"cashflow: ticker inválido {ticker!r} (mínimo 3 caracteres)")
    return _get(f"/v1/mercado/cashflow/{tk}",
                {"campos": list(campos or CAMPOS_CASHFLOW)})


def censar(vencidos: bool = False) -> dict:
    """Universo COMPLETO de 1816: recorre `/curvas` y pide `/instrumentos` por cada
    una. → `{curvas: [{id, nombre, vigentes, total, error}], instrumentos: {ticker: inst}}`,
    donde cada `inst` viene enriquecido con `_curva_id` y `_curva`.

    **Costo: 1 crédito por llamada** → ~29 el censo (1 + 28 curvas), ~57 con
    `vencidos=True`. Es la operación más barata del proveedor y la base del
    AV Agent (docs/AV_AGENT.md E1).

    Vive acá y no en un `scripts/diag_*` porque **es el censo del PROVEEDOR** y lo
    usan tres consumidores (el diag de cashflow, el de mapeo y el job del
    AV Agent). Duplicado, dos cruces podían dar universos distintos sin que nadie
    se entere — la misma razón por la que `normalizar_ticker` vive acá.

    Una curva que falla NO aborta el censo: queda con su `error` en la fila y el
    resto sigue. Medido 2026-08-15: 887 tickers únicos vigentes en 28 curvas, y la
    suma por curva da exactamente 887 (ningún ticker se publica en dos curvas)."""
    curvas_cat = curvas() or []
    logger.info("mercado_1816.censar: %s curvas en el catálogo", len(curvas_cat))

    universo: dict[str, dict] = {}
    filas: list[dict] = []
    for c in curvas_cat:
        cid = c.get("id") or c.get("curvaId")
        nombre = c.get("name") or c.get("nombre") or c.get("descripcion") or f"curva {cid}"
        if cid is None:
            continue
        fila = {"id": cid, "nombre": nombre, "vigentes": 0, "total": 0, "error": ""}
        try:
            vig = instrumentos(curva_id=int(cid)) or []
        except Exception as e:
            fila["error"] = str(e)[:80]
            filas.append(fila)
            continue
        fila["vigentes"] = len(vig)
        for inst in vig:
            tk = (inst.get("ticker") or "").strip().upper()
            if tk:
                inst["_curva_id"], inst["_curva"] = cid, nombre
                universo.setdefault(tk, inst)
        if vencidos:
            try:
                fila["total"] = len(instrumentos(curva_id=int(cid),
                                                 solo_performing=False) or [])
            except Exception as e:
                fila["error"] = str(e)[:80]
        filas.append(fila)

    return {"curvas": filas, "instrumentos": universo}


def parse_series(data: dict) -> list[dict]:
    """{instrumentos:{ticker:{campo:[[fecha,valor],…]}}} → filas tidy
    [{ticker, fecha, campo, valor, fuente, moneda, plazo, convencion_tna}]. Pura
    (testeable sin red). Saltea puntos con valor null o mal formados."""
    meta = {
        "fuente": data.get("fuente"), "moneda": data.get("moneda"),
        "plazo": data.get("plazo"), "convencion_tna": data.get("convencionTna"),
    }
    out: list[dict] = []
    for ticker, campos in (data.get("instrumentos") or {}).items():
        for campo, serie in (campos or {}).items():
            for punto in serie or []:
                if not isinstance(punto, (list, tuple)) or len(punto) < 2:
                    continue
                fecha, valor = punto[0], punto[1]
                if fecha is None or valor is None:
                    continue
                out.append({"ticker": ticker, "fecha": fecha, "campo": campo,
                            "valor": valor, **meta})
    return out
