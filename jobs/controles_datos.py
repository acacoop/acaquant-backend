"""controles_datos.py — Auto-control de CALIDAD DE DATOS de ACAQuant.

Controles de CONTENIDO: datos que existen pero están mal/incompletos. Cada
control devuelve una lista de anomalías con una clave estable; el runner las
diffea contra el estado persistido en SQL `manager.controles_datos`,
destacando lo NUEVO de hoy y lo RESUELTO (no repite lo ya conocido como si
fuera novedad). El detalle se consume vía GET /api/manager/controles (tab de
Manager). Con DEEPSEEK_API_KEY seteada, core/ai_resumen (vía el gateway
core/ai.py) agrega una lectura ejecutiva en criollo al resumen.

Controles v1:
  * forwards_faltantes    — bonos del master (mercado.curvas) que NO están en la
                            matriz de forwards publicada de su curva, con causa
                            probable (ej. TZXM9 sin TEA en market_snapshot), +
                            matrices que dejaron de publicarse.
  * rf_sin_tasa           — bonos cotizando (last_price>0) sin TEA en la rueda.
  * assets_sin_cartera    — portafolio.assets con cartera vacía/'NO APLICA'
                            (rompen el divisor de valuación: quedan SIN CLASIFICAR).
  * fci_incompletos       — assets FCI sin ticker/emisor (salen SIN NOMBRE y se
                            fusionan en el detalle de /aum → FCI).
  * rf_valuada_x1         — renta fija (HD/DL/ARS) valuada SIN ÷100 en el último
                            snapshot de tenencia: firma de un tipoTitulo nuevo de
                            Aunesa fuera de TIPOS_DIVISOR_100 (caso LEDE 2026-07-24).
  * comitentes_sin_nivel1 — comitentes Activas sin nivel_1 (quedan fuera de la
                            segmentación / filtros madre). Privado: solo conteo
                            en el resumen, detalle vía GET /api/manager/controles.
  * contrapartes_pendientes — corre el conciliador de contrapartes (Aunesa live,
                            el botón "Solicitar cuentas" de Manager) y marca si
                            hay cuentas candidatas sin dar de alta. Privado.
  * ops_sin_tc            — boletos ARS sin `mep` en un día que SÍ tiene cotización:
                            no se pueden dolarizar en la vista OPERACIONES
                            (caso FCI Bilateral 2026-08).

Regla del resumen: NUNCA datos de clientes → los controles con publico=False
muestran solo conteos; el detalle queda en la tabla y en el endpoint de Manager.

Cron: L-V 16:30 UTC (media rueda: motores vivos → forwards/tasas medibles).
Uso: python -m jobs.controles_datos [--dry]
"""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

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

def _chk_assets_sin_cartera() -> list[dict]:
    """Assets sin CARTERA: su valuación queda SIN CLASIFICAR (el divisor del AuM
    lo decide la cartera) — Manager → Títulos → Assets."""
    rows = _q(
        "SELECT unidad FROM portafolio.assets "
        "WHERE cartera IS NULL OR cartera = '' OR cartera = 'NO APLICA' "
        "ORDER BY unidad")
    return [{"key": r["unidad"], "detalle": f"{r['unidad']}: sin cartera"}
            for r in rows if r.get("unidad")]


def _chk_fci_incompletos() -> list[dict]:
    """Assets con cartera FCI sin `ticker` y/o sin `emisor`: salen SIN NOMBRE en el
    detalle de /aum → FCI y, si comparten "vacío", se fusionan en un renglón mudo.
    El `ticker` se autocompleta desde la unidad (writer diario + scripts.
    backfill_fci_ticker); si acá aparece uno con ticker vacío es que la unidad no
    matchea el formato CAFCI. El `emisor` se carga a mano (Manager → Assets)."""
    rows = _q(
        "SELECT unidad, "
        "       (ticker IS NULL OR trim(ticker) = '') AS sin_ticker, "
        "       (emisor IS NULL OR trim(emisor) = '') AS sin_emisor "
        "FROM portafolio.assets "
        "WHERE cartera IN ('FCI', 'CARTERA FCI') "
        "  AND ((ticker IS NULL OR trim(ticker) = '') "
        "       OR (emisor IS NULL OR trim(emisor) = '')) "
        "ORDER BY unidad")
    out: list[dict] = []
    for r in rows:
        faltan = [c for c, v in (("ticker", r["sin_ticker"]), ("emisor", r["sin_emisor"])) if v]
        out.append({"key": r["unidad"], "detalle": f"{r['unidad']}: FCI sin {' y '.join(faltan)}"})
    return out


@dataclass(frozen=True)
class Control:
    id: str
    titulo: str
    publico: bool          # True → los keys pueden ir al resumen (tickers/unidades)
    fn: Callable[[], list[dict]]


CONTROLES: list[Control] = [
    # ⚠️⚠️ **QUEDAN DOS DE DIECISÉIS** (2026-08-27), y los dos son transitorios:
    # se van cuando la habilidad `ficha_incompleta` del agente los reemplace.
    #
    # DE LOS CATORCE QUE SE FUERON, SIETE ERAN LITERALMENTE EL AGENTE. Cuando se
    # rehizo el agente (2026-08-24) se cortó el cable de escritura de estos
    # controles —dejaron de espejar hallazgos— pero se los dejó CORRIENDO con su
    # propio cron, mientras los detectores 2.0 se escribían de cero mirando lo
    # mismo. La migración quedó a la mitad: dos ojos para un problema, y uno de
    # los dos ya sin manos. Y el que sobraba era peor que redundante: llegaba al
    # tablero como un aviso genérico —sujeto = el NOMBRE DEL CONTROL— tapando al
    # que sí traía el botón.
    #
    #   patas_sin_precio      -> bono_sin_precio / no_suscripto  (botón: pedir pata)
    #   patas_equivocadas     -> precio_moneda / pata_equivocada (botón: apuntar pata)
    #   patas_dolar_sin_pedir -> precio_moneda / cotiza_en_pesos (botón: pata dólar)
    #   dia_sin_dato          -> motor_caido / job_sin_dato      (botón: rehacer job)
    #   titulos_sin_flujo     -> bono_sin_flujo / sin_flujo      (botón: alta flujos)
    #   rf_sin_tasa           -> bono_sin_tasa  (+ el círculo de 1816)
    #   assets_ticker_partido -> dato_partido / ticker_curva_vs_assets
    #
    # Los otros SIETE (forwards_faltantes, unidades_gemelas, rf_valuada_x1,
    # simbolos_cuarentena, ops_sin_tc, comitentes_sin_nivel1,
    # contrapartes_pendientes) se dieron de baja por decisión del user
    # (2026-08-27): **nadie los mira más**. Si alguno hace falta de nuevo, vuelve
    # como una fila del catálogo del agente, no como un control aparte.
    Control("assets_sin_cartera", "Assets sin cartera", True, _chk_assets_sin_cartera),
    Control("fci_incompletos", "Assets FCI sin ticker/emisor", True,
            _chk_fci_incompletos),
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

    # ⚠️ **EL CIERRE POR AUSENCIA ACÁ ES EL CASO LIMPIO**, y conviene decir por
    # qué: a esta función **solo se llega si `c.fn()` no levantó** (en `main()`
    # está adentro del `try`, y si el control explota se anota en `errores` y no
    # se persiste nada). O sea que una lista vacía significa de verdad «no hay
    # anomalías», y no «no pude mirar» — que es la distinción que costó dos bugs.
    #
    # Un `origen` por control: cada uno concilia SU universo y no toca el de al
    # lado. Y `_diff_y_persistir` es el único camino de escritura, así que el
    # cron y el botón ↻ CHEQUEAR AHORA dejan exactamente el mismo estado.
    # Antigüedad del activo más viejo (para que el mensaje diga "roto hace N días").
    viejo = _q("SELECT min(first_seen) AS f FROM manager.controles_datos "
               "WHERE control_id = %s AND resuelto_at IS NULL", (control_id,))
    mas_viejo = viejo[0]["f"] if viejo and viejo[0]["f"] else None
    return {
        "activos": len(por_key), "nuevos": nuevos, "resueltos": resueltos,
        "detalles_nuevos": [por_key[k] for k in nuevos[:20]],
        "dias_mas_viejo": (now - mas_viejo).days if mas_viejo else 0,
    }


# ── Render resumen (stdout / log del job) ────────────────────────────────────

def _md(s) -> str:
    import re
    return re.sub(r"([_*\[`])", r"\\\1", str(s))


def render_resumen(resultados: dict[str, dict], errores: dict[str, str],
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
            lines.append("      (detalle en Manager → controles)")
    if lectura_ai:
        lines.append("")
        lines.append(f"🧠 *Lectura:* {_md(lectura_ai)}")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def _purgar_controles_de_baja() -> int:
    """Las anomalías de un control que YA NO EXISTE.

    ⚠️ **Un control dado de baja deja su basura ABIERTA para siempre**, y esta
    es la clase de resto que no se ve desde el código: `_diff_y_persistir`
    resuelve por AUSENCIA, pero solo dentro del `control_id` que está corriendo.
    Si el control se borra, nadie vuelve a mirar sus filas: quedan con
    `resuelto_at IS NULL` eternamente y la tab de Manager las sigue mostrando
    como problemas vigentes de algo que ya nadie mide.

    Pasó al dar de baja catorce controles de golpe (2026-08-27). Se purga en
    cada corrida y no una sola vez: así el que borre el próximo control no tiene
    que acordarse de nada.
    """
    vivos = [c.id for c in CONTROLES]
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM manager.controles_datos "
                    " WHERE control_id <> ALL(%s)", (vivos,))
        return cur.rowcount or 0


def main() -> int:
    dry = "--dry" in sys.argv

    from core.job_runs import JobRunLogger
    with JobRunLogger("controles_datos") as jr:
        _ensure()
        if not dry:
            jr.set_stat("huerfanos_purgados", _purgar_controles_de_baja())
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

        # ⚠️ **LA LECTURA CON IA SE DIO DE BAJA** (2026-08-20). `core/ai_resumen`
        # se borró el 2026-08-19 con el copiloto (§0.k) y esta línea quedó
        # importándolo: el job venía MURIENDO todos los días con
        # `ModuleNotFoundError` DESPUÉS de correr los 20 controles — así que
        # calculaba todo y no persistía ni avisaba nada.
        #
        # No se reemplaza por otra IA: rige la regla de §0.k — *una tarea de IA
        # existe solo si alguien lee su salida*, y este resumen no lo leía nadie.
        # `fallas_jobs` se deja porque va en el texto, que sí se imprime.
        lectura = (f"{len(fallas_jobs)} job(s) fallaron en las últimas 24 h"
                   if fallas_jobs else None)
        jr.set_stat("jobs_fallados_24h", len(fallas_jobs))

        msg = render_resumen(resultados, errores, lectura)
        print(msg)
        if dry:
            print("\n[--dry: no se persiste]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
