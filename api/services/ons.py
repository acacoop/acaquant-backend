"""api/services/ons.py — gestión de ONs, DIRECTO sobre Trading.Curvas (curva on_<sector>).

UNA sola base: las ONs viven en `Trading.Curvas` como `curva='on_<sector>'` (igual que
el resto de la renta fija — la `curva` decide la vista). BondsMaster fue RETIRADO
(Fase 3, 2026-06-22).

⚠️ **EL PANEL QUE LO USABA YA NO EXISTE.** El tab `/manager → TÍTULOS → BONOS`
(que traía adentro el editor de ONs) se borró: lo que hacía lo hace EL AV AGENT.
Con él se fueron `list_ons`, `ons_values`, `delete_on`, `set_sector`, el parser de
flujos pegados de Excel (`parse_flujos_texto`) y el conciliador `conciliar()` — no
los llamaba nadie más. Lo que queda es LO QUE USA EL AGENTE:
  - `bondmaster_to_curva_doc` + `upsert_on` — la puerta de escritura de las ONs
    (`agente/alta.py`; una ON NO se escribe por `bonos_admin.upsert_bono`).
  - `slug_sector` — decide la curva `on_<sector>`. Una ON que da de alta el agente
    nace en `on_otros` y HOY NO HAY PANTALLA para reclasificarla.
  - `ignorar_concil` — la lista de ONs que la mesa no sigue (`agente/vista.py`).
  - `_f` / `_fecha_iso` / `_fecha_flujo_iso` / `_upsert_curva_doc` — los importa
    `api/services/bonos_admin.py`.

Puro (sin FastAPI). SQL-native (decomiso Mongo): el master vive en `mercado.curvas`
(Postgres) — lectura vía `core.curvas_sql`, escritura vía `core.pg_mirror.write_native`.
La lista de tickers ignorados del conciliador vive en `mercado.ons_ignoradas` (SQL-native,
decomiso 2026-06-28). El sector va codificado en la curva ('on_energia', etc.), DB-driven.
Ver `api/services/renta_fija.py`.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from core import curvas_ejes as ce
from core import curvas_sql
from core.pg_mirror import doc_iso, write_native


def slug_sector(raw) -> str:
    """Normaliza el sector que se carga en BondsMaster.sector a un slug para la
    curva: 'Energía' → 'energia', 'ON Finanzas' → 'finanzas'. Vacío → 'otros'."""
    s = (raw or "otros").strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(a, b)
    s = s.replace("on ", "").replace(" ", "_").strip("_")
    return s or "otros"


def _fecha_iso(raw) -> str | None:
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:10]
    return None


def _f(x, default: float = 0.0) -> float:
    """float() tolerante: None / '' / texto raro → default (no explota el sync)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _fecha_flujo_iso(raw) -> str | None:
    """Fecha de un flujo → ISO 'YYYY-MM-DD'. Acepta datetime o string."""
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    return None


# ── Espejo SQL-native de Trading.Curvas → mercado.curvas ─────────────────────
# Las ONs y bonos viven en mercado.curvas (Postgres). El doc COMPLETO va en la
# columna jsonb `data` (mismo shape que tenía Mongo: fechas como strings ISO);
# las columnas tipadas son SOLO para filtrar barato. Mapeo IDÉNTICO al de
# jobs/sync_postgres.py::sync_curvas (la fuente probada del puente Mongo→SQL).

def _row_date(v) -> date | None:
    """ISO str | datetime | date → date para columna date. None si inválido."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def curva_doc_to_row(doc: dict) -> dict:
    """Doc de Trading.Curvas → fila tipada para `mercado.curvas` (write_native).
    Columnas consultables + `flujos` (jsonb) + doc completo en `data` (datetime→ISO
    vía doc_iso). Campos ausentes → NULL; el reader (curvas_sql) usa `data`.

    OJO — las CLAVES de la izquierda son COLUMNAS y las de la derecha son claves del
    DOC, y desde el renombre de 2026-08-15 ya no coinciden: la columna `ticker` es el
    ticker del bono (lo que el doc llama `ticker_corto`) y la columna `instrumento`
    es el símbolo de mercado (lo que el doc llama `ticker`). El doc/blob NO se tocó
    a propósito: sus claves las leen ~500 lugares y se migran en el paso siguiente."""
    return {
        "ticker": (doc.get("ticker_corto") or "").strip(),
        "instrumento": doc.get("ticker") or None,
        "curva": doc.get("curva") or None,
        "tipo": doc.get("tipo") or None,
        "moneda_flujo": doc.get("moneda_flujo") or None,
        "valor_nominal": doc.get("valor_nominal"),
        "fecha_emision": _row_date(doc.get("fecha_emision")),
        "fecha_vencimiento": _row_date(doc.get("fecha_vencimiento")),
        "cupon_anual": doc.get("cupon_anual"),
        "cer_emision": doc.get("cer_emision"),
        "flujo_vencimiento": doc.get("flujo_vencimiento"),
        "emisor": doc.get("emisor") or None,
        "sector": doc.get("sector") or None,
        "flujos": doc_iso(doc.get("flujos") or []),
        "data": doc_iso(doc),
    }


# Columnas de `mercado.curvas` que YA NO las gobierna el blob `data`: las escribe
# OTRO proceso, directo a la columna, y el blob quedó viejo.
#
#   · `emisor` — lo estandariza `jobs/ficha_1816.py` con un UPDATE a la COLUMNA.
#   · `sector` — la industria del emisor, que deja de cargarse por bono.
#
# Sin esta lista el merge las pisaba, y de dos formas distintas, las dos MUDAS:
#   · en un bono NO-ON el blob no tiene `emisor` → editar cualquier campo desde
#     Manager escribía `emisor = NULL` y borraba lo que 1816 había estandarizado;
#   · en una ON el blob tiene el emisor VIEJO (`BCO.COMAFI`) → editar el sector
#     revertía la estandarización a la grafía vieja.
# En los dos casos la pantalla queda igual de prolija y el dato se perdió.
#
# Solo se tocan si el payload las trae EXPLÍCITAS (el alta de ONs manda `emisor`,
# `set_sector` manda `sector`): ahí la escritura es intencional, no un arrastre.
COLUMNAS_AJENAS_AL_BLOB = ("emisor", "sector")


def fila_a_escribir(existing: dict, doc: dict) -> dict:
    """La fila del upsert, sin pisar las columnas que el blob ya no gobierna. PURA.

    `_mirror` agrupa por el set de claves del dict y escribe `c=EXCLUDED.c` por cada
    columna PRESENTE — así que sacar la clave es exactamente "no tocar esa columna".
    """
    row = curva_doc_to_row({**existing, **doc})
    for col in COLUMNAS_AJENAS_AL_BLOB:
        if col not in doc:
            row.pop(col, None)
    return row


def _upsert_curva_doc(doc: dict, ejes: dict | None = None) -> dict:
    """Upsert $set-PARCIAL de un doc de curva en `mercado.curvas` (paridad con el
    `update_one({'$set': doc}, upsert=True)` de Mongo): mergea sobre el doc existente
    y reescribe la fila completa → no pierde campos que el payload no trae. Devuelve
    el doc guardado (releído de SQL).

    `ejes` (ya validado con `core.curvas_ejes.normalizar_ejes`) va **SOLO A LAS
    COLUMNAS, nunca al blob `data`**. Es deliberado: el blob es la forma vieja, la
    que ~500 lugares leen con las claves invertidas, y meterle campos nuevos sería
    agrandar el problema de las dos verdades justo cuando lo estamos cerrando. Los
    ejes nacen viviendo en UN solo lugar.
    """
    tc = (doc.get("ticker_corto") or "").strip()
    existing = curvas_sql.find_one(tc) or {}  # perf-ok: PERF004 — 1ª lectura = base del merge; la 2ª relee POST-write (contrato: devolver lo guardado). Mutación admin, corre poco.
    row = fila_a_escribir(existing, doc)
    if ejes:
        row.update({k: v for k, v in ejes.items() if k in ce.EJES_EDITABLES})
    write_native("mercado.curvas", ["ticker"], [row])
    curvas_sql.invalidar()   # refrescar el cache del master tras el alta/edición
    return curvas_sql.find_one(tc) or {}


def bondmaster_to_curva_doc(bm: dict) -> dict | None:
    """BondsMaster doc → doc de Trading.Curvas (curva='on_<sector>'). None si no
    se puede (sin asset o sin ticker). Pata canónica por moneda: USD → ticker D,
    ARS → ticker O. Flujos en shape nativo {fecha, amortizacion, interes,
    valor_residual} (el motor usa monto_flujo = amortizacion + interes)."""
    asset = bm.get("asset")
    moneda = (bm.get("moneda_flujo") or "USD").upper()
    tickers = bm.get("tickers") or {}
    ticker_full = tickers.get("USD") if moneda == "USD" else tickers.get("ARS")
    if not ticker_full:
        ticker_full = tickers.get("ARS") or tickers.get("USD")
    if not asset or not ticker_full:
        return None

    flujos = []
    for f in bm.get("flujos") or []:
        fi = _fecha_flujo_iso(f.get("fecha"))
        if not fi:
            continue
        flujos.append({
            "fecha": fi,
            "amortizacion": _f(f.get("amortizacion"), 0.0),
            "interes": _f(f.get("interes"), 0.0),
            "valor_residual": _f(f.get("valor_residual"), 100.0) or 100.0,
        })

    return {
        "ticker": ticker_full,
        "ticker_corto": asset,
        "curva": f"on_{slug_sector(bm.get('sector'))}",
        "moneda_flujo": moneda,
        "emisor": bm.get("emisor"),
        "sector": bm.get("sector") or "otros",
        "tasa_cupon": bm.get("tasa_cupon"),
        "valor_nominal": 100,
        "fecha_vencimiento": _fecha_iso(bm.get("vencimiento")),
        "tickers": {"ARS": tickers.get("ARS"), "USD": tickers.get("USD")},  # ambas patas
        "flujos": flujos,
    }


# ─────────────────────────────────────────────
# La PUERTA DE ESCRITURA de las ONs — DIRECTO sobre Trading.Curvas (on_*).
# UNA sola base: las ONs viven en Curvas como curva on_<sector>. BondsMaster RETIRADO
# (Fase 3, 2026-06-22). `bondmaster_to_curva_doc` transforma el payload → doc de Curvas.
# Hoy el único que entra por acá es `agente/alta.py` (arreglo `alta_on`).
# ─────────────────────────────────────────────

def _curva_to_on(d: dict) -> dict:
    """Doc de Curvas on_* → shape ON que consume el panel (asset, tickers, vencimiento…)."""
    return {
        "asset": d.get("ticker_corto"),
        "emisor": d.get("emisor"),
        "sector": d.get("sector") or (str(d.get("curva") or "").replace("on_", "") or "otros"),
        "moneda_flujo": d.get("moneda_flujo"),
        "tasa_cupon": d.get("tasa_cupon"),
        "vencimiento": d.get("fecha_vencimiento"),
        "tickers": d.get("tickers") or {},
        "flujos": d.get("flujos") or [],
        "actualizado_por": d.get("actualizado_por"),
        "actualizado_at": d.get("actualizado_at"),
    }


def upsert_on(payload: dict, actor: str = "") -> dict:
    """Crea/edita una ON DIRECTO en Trading.Curvas (curva on_<sector>), upsert por
    ticker_corto (=`asset`). `payload` trae asset + campos editables (incl. flujos)."""
    asset = (payload.get("asset") or "").strip()
    if not asset:
        raise ValueError("falta 'asset'")
    doc = bondmaster_to_curva_doc({**payload, "asset": asset})
    if not doc:
        raise ValueError("falta el ticker (pata ARS o USD) — no se puede armar el doc de Curvas")
    doc["actualizado_por"] = actor
    doc["actualizado_at"] = datetime.now(UTC)
    saved = _upsert_curva_doc(doc)
    return {"on": _curva_to_on(saved)}


# ─────────────────────────────────────────────
# ONs que la mesa NO sigue (`mercado.ons_ignoradas`)
# ─────────────────────────────────────────────

def ignorar_concil(ticker: str, actor: str = "") -> dict:
    """Marca un ticker como 'no es ON' → no vuelve a aparecer en el gap.
    Upsert SQL-native por ticker (mercado.ons_ignoradas).

    ⚠️ **El único que escribe acá es EL AGENTE**, desde «no me interesan»
    (`agente/vista.no_interesan_ons`); `agente/fuentes.descartadas()` la lee.
    El endpoint `POST /api/manager/ons/ignorar` que también la escribía se fue
    con el tab BONOS, igual que `quitar_ignorar`/`listar_ignoradas`: hoy NO hay
    pantalla para restaurar una descartada."""
    ticker = (ticker or "").strip()
    if not ticker:
        raise ValueError("falta 'ticker'")
    write_native("mercado.ons_ignoradas", ["ticker"], [
        {"ticker": ticker, "ignorado_por": actor, "at": datetime.now(UTC)}])
    return {"ignorada": ticker}
