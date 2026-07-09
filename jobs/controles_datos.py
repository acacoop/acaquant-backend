"""controles_datos.py — Auto-control de CALIDAD DE DATOS de ACAQuant.

Complementa a jobs/informe_salud (frescura de motores/jobs) con controles de
CONTENIDO: datos que existen pero están mal/incompletos. Cada control devuelve
una lista de anomalías con una clave estable; el runner las diffea contra el
estado persistido en SQL `manager.controles_datos` y alerta por Telegram
destacando lo NUEVO de hoy y lo RESUELTO (no repite lo ya conocido como si
fuera novedad). Con ANTHROPIC_API_KEY seteada, core/ai_resumen agrega una
lectura ejecutiva en criollo al final del mensaje.

Controles v1:
  * forwards_faltantes    — bonos del master (mercado.curvas) que NO están en la
                            matriz de forwards publicada de su curva, con causa
                            probable (ej. TZXM9 sin TEA en market_snapshot), +
                            matrices que dejaron de publicarse.
  * rf_sin_tasa           — bonos cotizando (last_price>0) sin TEA en la rueda.
  * assets_sin_cartera    — portafolio.assets con cartera vacía/'NO APLICA'
                            (rompen el divisor de valuación: quedan SIN CLASIFICAR).
  * comitentes_sin_nivel1 — comitentes Activas sin nivel_1 (quedan fuera de la
                            segmentación / filtros madre). Privado: solo conteo
                            en Telegram, detalle vía GET /api/manager/controles.
  * contrapartes_pendientes — corre el conciliador de contrapartes (Aunesa live,
                            el botón "Solicitar cuentas" de Manager) y alerta si
                            hay cuentas candidatas sin dar de alta. Privado.

Regla del canal Telegram (core/notify.py): NUNCA datos de clientes → los
controles con publico=False mandan solo conteos; el detalle queda en la tabla
y en el endpoint de Manager.

Cron: L-V 16:30 UTC (media rueda: motores vivos → forwards/tasas medibles).
Uso: python -m jobs.controles_datos [--dry] [--no-telegram]
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from psycopg.rows import dict_row

from core.postgres import get_pool

logger = logging.getLogger("controles_datos")

_DDL = """
CREATE SCHEMA IF NOT EXISTS manager;
CREATE TABLE IF NOT EXISTS manager.controles_datos (
    control_id  text NOT NULL,
    item_key    text NOT NULL,
    detalle     text,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    last_seen   timestamptz NOT NULL DEFAULT now(),
    resuelto_at timestamptz,
    PRIMARY KEY (control_id, item_key)
);
CREATE INDEX IF NOT EXISTS ix_controles_datos_activos
    ON manager.controles_datos(control_id) WHERE resuelto_at IS NULL;
"""


def _q(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _ensure() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(_DDL)
        conn.commit()


# ── Controles (cada uno devuelve [{key, detalle}]) ───────────────────────────

# La matriz de forwards que importa a la mesa es SOLO la de la tabla de renta
# fija: TASA FIJA y CER. Las curvas de ONs (on_*) tienen forwards publicados
# pero sus faltantes no son accionables (bonos ilíquidos) → ruido.
CURVAS_FORWARDS = ("tasa_fija", "cer")


def _chk_forwards_faltantes() -> list[dict]:
    """Bonos del master ausentes de la matriz de forwards de su curva + matrices
    stale. Solo evalúa CURVAS_FORWARDS (tasa_fija/cer) que el motor efectivamente
    publica (hay doc en mercado_hist)."""
    from core.curvas_sql import agrupado_por_curva
    from core.market_snapshot import cols_map
    grupos = agrupado_por_curva()
    docs = {r["k"]: r for r in _q(
        "SELECT DISTINCT ON (k) k, fecha, data FROM mercado.mercado_hist "
        "WHERE coleccion = 'ForwardsHistorico' AND k = ANY(%s) ORDER BY k, fecha DESC",
        (list(CURVAS_FORWARDS),))}
    items: list[dict] = []
    for curva, doc in docs.items():
        insts = grupos.get(curva) or []
        if not insts:
            continue
        # Matriz que dejó de publicarse (>4 días cubre finde largo).
        if doc["fecha"] < date.today() - timedelta(days=4):
            items.append({"key": f"{curva}::__stale__",
                          "detalle": f"[{curva}] matriz sin publicar desde {doc['fecha']}"})
        publicados = set((doc.get("data") or {}).get("tickers") or [])
        master = {(i.get("ticker_corto") or "").strip(): i["ticker"]
                  for i in insts if (i.get("ticker_corto") or "").strip() and i.get("ticker")}
        faltantes = sorted(set(master) - publicados)
        if not faltantes:
            continue
        snap = cols_map([master[tc] for tc in faltantes], ["tea", "duration", "last_price"])
        for tc in faltantes:
            m = snap.get(master[tc]) or {}
            # last_price None O 0 = no operó (el dry-run 2026-07-09 mostró filas
            # con precio 0: "cotiza sin TEA" era engañoso — no había trade).
            if not m.get("last_price"):
                causa = "no operó (sin precio vivo en market_snapshot)"
            elif m.get("tea") is None:
                causa = "cotiza pero SIN TEA (motor_curvas no le calculó tasa — revisar flujos/CER del bono)"
            elif m.get("duration") is None:
                causa = "cotiza con TEA pero sin duration"
            else:
                causa = "tiene TEA+duration y aún no entró a la matriz (revisar motor_forwards)"
            items.append({"key": f"{curva}::{tc}", "detalle": f"[{curva}] {tc}: {causa}"})
    return items


def _chk_rf_sin_tasa() -> list[dict]:
    """Bonos del master cotizando (precio vivo) pero sin TEA/TNA — figuran en la
    tabla de renta fija sin tasa. Mismo criterio que el informe de salud."""
    rows = _q(
        "SELECT c.ticker_corto FROM mercado.curvas c "
        "JOIN mercado.market_snapshot ms ON ms.ticker = c.ticker "
        "WHERE COALESCE(ms.last_price, 0) > 0 AND COALESCE(ms.tea, 0) = 0 "
        "ORDER BY c.ticker_corto")
    return [{"key": r["ticker_corto"], "detalle": f"{r['ticker_corto']}: cotiza sin TEA/TNA"}
            for r in rows if r.get("ticker_corto")]


def _chk_assets_sin_cartera() -> list[dict]:
    """Assets sin CARTERA: su valuación queda SIN CLASIFICAR (el divisor del AuM
    lo decide la cartera) — Manager → Títulos → Assets."""
    rows = _q(
        "SELECT unidad FROM portafolio.assets "
        "WHERE cartera IS NULL OR cartera = '' OR cartera = 'NO APLICA' "
        "ORDER BY unidad")
    return [{"key": r["unidad"], "detalle": f"{r['unidad']}: sin cartera"}
            for r in rows if r.get("unidad")]


def _chk_comitentes_sin_nivel1() -> list[dict]:
    """Comitentes Activas sin nivel_1 → fuera de la segmentación (Clientes →
    Segmentación) y de los filtros madre. PRIVADO (keys = id_cuenta)."""
    rows = _q(
        "SELECT id_cuenta FROM clientes.comitentes "
        "WHERE estado = 'Activa' AND (nivel_1 IS NULL OR nivel_1 = '') "
        "ORDER BY id_cuenta")
    return [{"key": str(r["id_cuenta"]), "detalle": f"cuenta {r['id_cuenta']}: sin nivel_1"}
            for r in rows if r.get("id_cuenta")]


def _chk_simbolos_cuarentena() -> list[dict]:
    """Símbolos en cuarentena: ROFEX los rechazó ("Product don't exist") y el WS
    los excluye de las suscripciones (core/simbolos_cuarentena). La causa de
    fondo suele ser un ticker mal cargado o un bono vencido en el master —
    corregirlo ahí es el fix definitivo. Se auto-resuelven si ROFEX los vuelve
    a aceptar en la ventana de reintento (7d)."""
    rows = _q(
        "SELECT ticker, motivo, rechazos, last_seen::date AS ultimo "
        "FROM mercado.simbolos_cuarentena "
        "WHERE last_seen >= now() - interval '7 days' ORDER BY ticker")
    return [{
        "key": r["ticker"],
        "detalle": (f"{r['ticker']}: {r['motivo']} — {r['rechazos']} rechazo(s), "
                    f"último {r['ultimo']}"),
    } for r in rows if r.get("ticker")]


def _chk_contrapartes_pendientes() -> list[dict]:
    """Conciliador de contrapartes (= botón "Solicitar cuentas" de Manager):
    cuentas de Aunesa que matchean una contraparte conocida y NO están dadas de
    alta en clientes.contrapartes. PRIVADO (keys = cuenta; detalle queda en DB).
    Import de api/services permitido: misma excepción documentada que los jobs
    de precompute (jobs/CLAUDE.md)."""
    from api.services.contrapartes_seg import reconciliar
    res = reconciliar(limit=500)
    return [{
        "key": str(c.get("cuenta")),
        "detalle": (f"cuenta {c.get('cuenta')} '{c.get('denominacion', '')}' → "
                    f"sugerida: {c.get('contraparte_sugerida')} ({c.get('segmento_sugerido')})"),
    } for c in res.get("candidatos", []) if c.get("cuenta")]


@dataclass(frozen=True)
class Control:
    id: str
    titulo: str
    publico: bool          # True → los keys pueden ir a Telegram (tickers/unidades)
    fn: Callable[[], list[dict]]


CONTROLES: list[Control] = [
    Control("forwards_faltantes", "Bonos ausentes de forwards", True, _chk_forwards_faltantes),
    Control("rf_sin_tasa", "Renta fija cotizando sin TEA/TNA", True, _chk_rf_sin_tasa),
    Control("assets_sin_cartera", "Assets sin cartera", True, _chk_assets_sin_cartera),
    Control("simbolos_cuarentena", "Símbolos rechazados por ROFEX (cuarentena)", True,
            _chk_simbolos_cuarentena),
    Control("comitentes_sin_nivel1", "Comitentes activos sin nivel 1", False,
            _chk_comitentes_sin_nivel1),
    Control("contrapartes_pendientes", "Cuentas de contrapartes sin dar de alta", False,
            _chk_contrapartes_pendientes),
]


# ── Diff + persistencia ──────────────────────────────────────────────────────

def _diff_y_persistir(control_id: str, items: list[dict]) -> dict:
    """Compara las anomalías actuales contra el estado en manager.controles_datos.
    Nuevos → insert/reactivación (first_seen=now); vigentes → last_seen=now;
    ausentes → resuelto_at=now. Devuelve el resumen del control."""
    now = datetime.now(UTC)
    activos_previos = {r["item_key"] for r in _q(
        "SELECT item_key FROM manager.controles_datos "
        "WHERE control_id = %s AND resuelto_at IS NULL", (control_id,))}
    por_key = {it["key"]: it["detalle"] for it in items}
    nuevos = sorted(set(por_key) - activos_previos)
    resueltos = sorted(activos_previos - set(por_key))

    with get_pool().connection() as conn, conn.cursor() as cur:
        if por_key:
            cur.executemany(
                "INSERT INTO manager.controles_datos "
                "(control_id, item_key, detalle, first_seen, last_seen) "
                "VALUES (%(cid)s, %(k)s, %(d)s, %(now)s, %(now)s) "
                "ON CONFLICT (control_id, item_key) DO UPDATE SET "
                "  detalle = EXCLUDED.detalle, last_seen = EXCLUDED.last_seen, "
                # Reaparición de un resuelto = anomalía NUEVA → first_seen se resetea.
                "  first_seen = CASE WHEN manager.controles_datos.resuelto_at IS NOT NULL "
                "               THEN EXCLUDED.first_seen ELSE manager.controles_datos.first_seen END, "
                "  resuelto_at = NULL",
                [{"cid": control_id, "k": k, "d": d, "now": now} for k, d in por_key.items()])
        if resueltos:
            cur.execute(
                "UPDATE manager.controles_datos SET resuelto_at = %s "
                "WHERE control_id = %s AND item_key = ANY(%s) AND resuelto_at IS NULL",
                (now, control_id, resueltos))
        conn.commit()

    # Antigüedad del activo más viejo (para que el mensaje diga "roto hace N días").
    viejo = _q("SELECT min(first_seen) AS f FROM manager.controles_datos "
               "WHERE control_id = %s AND resuelto_at IS NULL", (control_id,))
    mas_viejo = viejo[0]["f"] if viejo and viejo[0]["f"] else None
    return {
        "activos": len(por_key), "nuevos": nuevos, "resueltos": resueltos,
        "detalles_nuevos": [por_key[k] for k in nuevos[:20]],
        "dias_mas_viejo": (now - mas_viejo).days if mas_viejo else 0,
    }


# ── Render Telegram ──────────────────────────────────────────────────────────

def _md(s) -> str:
    import re
    return re.sub(r"([_*\[`])", r"\\\1", str(s))


def render_telegram(resultados: dict[str, dict], errores: dict[str, str],
                    lectura_ai: str | None) -> str:
    total_nuevos = sum(len(r["nuevos"]) for r in resultados.values())
    icono = "🟢" if total_nuevos == 0 and not errores else ("🔴" if errores else "🟠")
    lines = [f"{icono} *Controles de datos ACAQuant* — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC"]
    for c in CONTROLES:
        r = resultados.get(c.id)
        if r is None:
            lines.append(f"  ⚪ {_md(c.titulo)}: sin datos ({_md(errores.get(c.id, '?'))})")
            continue
        marca = "✅" if r["activos"] == 0 else "⚠️"
        extra = []
        if r["nuevos"]:
            extra.append(f"▲{len(r['nuevos'])} nuevos")
        if r["resueltos"]:
            extra.append(f"▼{len(r['resueltos'])} resueltos")
        if r["activos"] and r["dias_mas_viejo"] >= 2:
            extra.append(f"el más viejo hace {r['dias_mas_viejo']}d")
        sufijo = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"  {marca} {_md(c.titulo)}: {r['activos']}{sufijo}")
        # Detalle SOLO de lo nuevo, y SOLO si el control es público (regla del canal).
        if c.publico and r["nuevos"]:
            for d in r["detalles_nuevos"][:8]:
                lines.append(f"      • {_md(d)}")
            if len(r["nuevos"]) > 8:
                lines.append(f"      … +{len(r['nuevos']) - 8} más")
        elif not c.publico and r["nuevos"]:
            lines.append("      (detalle en Manager → controles, no va por Telegram)")
    if lectura_ai:
        lines.append("")
        lines.append(f"🧠 *Lectura:* {_md(lectura_ai)}")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    dry = "--dry" in sys.argv
    no_tg = "--no-telegram" in sys.argv

    from core.job_runs import JobRunLogger
    with JobRunLogger("controles_datos") as jr:
        _ensure()
        resultados: dict[str, dict] = {}
        errores: dict[str, str] = {}
        for c in CONTROLES:
            try:
                items = c.fn()
                if dry:
                    resultados[c.id] = {"activos": len(items),
                                        "nuevos": [i["key"] for i in items],
                                        "detalles_nuevos": [i["detalle"] for i in items[:20]],
                                        "resueltos": [], "dias_mas_viejo": 0}
                else:
                    resultados[c.id] = _diff_y_persistir(c.id, items)
                jr.set_stat(c.id, resultados[c.id]["activos"])
            except Exception as e:
                errores[c.id] = f"{type(e).__name__}: {e}"
                jr.error(f"{c.id}: {errores[c.id]}")

        # Contexto extra para la lectura AI: fallas de jobs de las últimas 24h.
        try:
            fallas_jobs = _q(
                "SELECT tipo, status, started_at::text AS started_at "
                "FROM manager.job_runs WHERE started_at >= now() - interval '24 hours' "
                "AND status <> 'ok' ORDER BY started_at DESC LIMIT 20")
        except Exception:
            fallas_jobs = []

        from core.ai_resumen import resumen_ejecutivo
        lectura = resumen_ejecutivo({
            "controles": {cid: {**r, "titulo": next(c.titulo for c in CONTROLES if c.id == cid)}
                          for cid, r in resultados.items()},
            "controles_con_error": errores,
            "jobs_fallados_24h": fallas_jobs,
        })
        jr.set_stat("lectura_ai", bool(lectura))

        msg = render_telegram(resultados, errores, lectura)
        if dry:
            print(msg)
            print("\n[--dry: no se persiste ni se manda]")
            return 0
        if not no_tg:
            from core.notify import send_telegram
            send_telegram(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
