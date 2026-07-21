"""asistente_tools — tools curadas READ-ONLY del asistente de negocio (QuantAI P7).

Regla de oro (token-in / token-out):
- Las tools reciben FICHAS (CLIENTE_7, CTA_2) y las resuelven a ids reales
  SOLO acá adentro (perímetro), vía core.pii_gateway.id_cuenta_de_ficha.
- Lo que devuelven al LLM ya está limpio: los resultados se refieren a la
  cuenta por su FICHA (jamás nombre/id real) y, cinturón y tirantes, TODO
  resultado pasa por pii_gateway.tokenize antes de volver al loop.
- El LLM nunca escribe SQL: elige tool + parámetros; el SQL vive acá, fijo.
- READ-ONLY absoluto: ninguna tool muta nada.

Las cifras (AuM, P&L) salen pseudonimizadas — atadas a la ficha, imposibles
de vincular a una persona desde afuera (decisión user 2026-07-21, QUANTAI P7).

Set inicial CHICO (se amplía con uso real, no por las dudas):
  resumen_mesa()            — agregados del día: AuM total, cuentas, top segmentos.
  rendimiento_cuenta(ficha) — AuM + PnL de UNA cuenta, referida por su ficha.
"""
from __future__ import annotations

import logging

from core import pii_gateway
from core.postgres import get_pool

logger = logging.getLogger(__name__)


# ── Schemas para el function-calling (formato OpenAI) ────────────────────────

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "resumen_mesa",
            "description": (
                "Agregados del día de la mesa: AuM total administrado, cantidad de "
                "cuentas con tenencia y los segmentos más grandes por AuM. Usala para "
                "preguntas generales del negocio ('¿cuál es el AuM total?', '¿cómo se "
                "reparte por segmento?'). No recibe parámetros y no trae clientes."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rendimiento_cuenta",
            "description": (
                "AuM y P&L (realizado, no realizado, pasivo) de UNA cuenta puntual. "
                "El parámetro es la FICHA con la que la cuenta aparece en la "
                "conversación (CLIENTE_1, CTA_2...). Usala cuando pregunten por un "
                "cliente o cuenta específica."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ficha_cuenta": {
                        "type": "string",
                        "description": "La ficha tal cual aparece (ej. CLIENTE_1 o CTA_1).",
                    },
                },
                "required": ["ficha_cuenta"],
            },
        },
    },
]


# ── Queries (perímetro, read-only) ───────────────────────────────────────────

def _fecha_snapshot() -> str | None:
    """Último snapshot de tenencias (portafolio.tenencia, writer diario 11 UTC)."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia WHERE aum = 'si'")
        f = cur.fetchone()[0]
    return str(f) if f else None


def _aum_totales(fecha: str) -> tuple[float, int]:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(sum(valuacion), 0), count(DISTINCT id_cuenta) "
            "FROM portafolio.tenencia WHERE fecha = %s AND aum = 'si'", (fecha,))
        total, n = cur.fetchone()
    return float(total or 0), int(n or 0)


def _aum_por_segmento(fecha: str, top: int = 5) -> list[tuple[str, float]]:
    """AuM agrupado por segmento nivel_1 (clientes.comitentes) — agregado puro,
    sin nombres. Cuentas sin segmentación → 'SIN SEGMENTO'."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(nullif(trim(m.nivel_1), ''), 'SIN SEGMENTO') AS seg, "
            "       sum(t.valuacion) AS aum "
            "FROM portafolio.tenencia t "
            "LEFT JOIN clientes.comitentes m USING (id_cuenta) "
            "WHERE t.fecha = %s AND t.aum = 'si' "
            "GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT %s", (fecha, top))
        return [(r[0], float(r[1] or 0)) for r in cur.fetchall()]


def _aum_cuenta(fecha: str, id_cuenta: str) -> float | None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT sum(valuacion) FROM portafolio.tenencia "
            "WHERE fecha = %s AND aum = 'si' AND id_cuenta = %s", (fecha, id_cuenta))
        v = cur.fetchone()[0]
    return float(v) if v is not None else None


def _pnl_cuenta(id_cuenta: str) -> dict | None:
    """Totales del motor de PnL (valuaciones.pnl_totales_cache, precalculado
    por jobs.pnl_totales_precompute). None = la cuenta no está en el cache."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT totales, computed_at FROM valuaciones.pnl_totales_cache "
            "WHERE id_cuenta = %s", (id_cuenta,))
        fila = cur.fetchone()
    if not fila or not fila[0]:
        return None
    return {"totales": fila[0], "computed_at": str(fila[1] or "")}


# ── Formato (números legibles, sin identidades) ──────────────────────────────

def _monto(v: float | None) -> str:
    if v is None:
        return "sin dato"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f} mil millones ARS"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.1f} millones ARS"
    return f"{v:.0f} ARS"


# ── Las tools ────────────────────────────────────────────────────────────────

def resumen_mesa() -> str:
    fecha = _fecha_snapshot()
    if not fecha:
        return "sin datos de tenencia disponibles (el snapshot diario no corrió todavía)"
    total, n_cuentas = _aum_totales(fecha)
    lineas = [
        f"[resumen de la mesa — snapshot {fecha}]",
        f"AuM total administrado: {_monto(total)}",
        f"cuentas con tenencia: {n_cuentas}",
        "AuM por segmento (top):",
    ]
    for seg, aum in _aum_por_segmento(fecha):
        pct = 100 * aum / total if total else 0
        lineas.append(f"  - {seg}: {_monto(aum)} ({pct:.1f}%)")
    return "\n".join(lineas)


def rendimiento_cuenta(ficha_cuenta: str, *, mapping: dict) -> str:
    """La ficha se resuelve al id real SOLO acá adentro; la respuesta vuelve
    a referirse a la cuenta por su ficha — el id jamás viaja al LLM."""
    ficha = (ficha_cuenta or "").strip()
    id_cuenta = pii_gateway.id_cuenta_de_ficha(ficha, mapping)
    if not id_cuenta:
        return (f"no pude identificar la cuenta {ficha or '(vacía)'} — pedile al usuario "
                "que aclare el nombre completo o el número de cuenta")
    fecha = _fecha_snapshot()
    lineas = [f"[rendimiento de {ficha}]"]
    if fecha:
        aum = _aum_cuenta(fecha, id_cuenta)
        lineas.append(f"AuM ({fecha}): {_monto(aum)}"
                      if aum is not None else f"sin tenencia con AuM al {fecha}")
    pnl = _pnl_cuenta(id_cuenta)
    if pnl:
        t = pnl["totales"]
        lineas += [
            f"PnL títulos (motor de valuaciones, al {pnl['computed_at'][:16]}):",
            f"  - no realizado: {_monto(t.get('pnl_no_realizado'))}",
            f"  - pasivo (cupones/divs/amorts): {_monto(t.get('pnl_pasivo'))}",
            f"  - realizado del día: {_monto(t.get('pnl_realizado_dia'))}",
            f"  - total: {_monto(t.get('pnl_total'))}",
        ]
    else:
        lineas.append("PnL: la cuenta no está en el cache del motor (sin posiciones con "
                      "cost-basis o el precompute no corrió)")
    return "\n".join(lineas)


# ── Dispatcher (lo invoca el loop de tools del gateway) ──────────────────────

def ejecutar(nombre: str, args: dict, *, mapping: dict) -> str:
    """Ejecuta la tool pedida y devuelve el resultado YA pasado por la aduana
    (token-out): si algún dato colara una identidad, se tacha con las MISMAS
    fichas del chat antes de volver al LLM. Errores → mensaje corto, jamás
    excepción con datos adentro."""
    try:
        if nombre == "resumen_mesa":
            crudo = resumen_mesa()
        elif nombre == "rendimiento_cuenta":
            crudo = rendimiento_cuenta(str(args.get("ficha_cuenta", "")), mapping=mapping)
        else:
            return f"herramienta desconocida: {nombre}"
    except Exception as e:
        logger.warning("asistente_tools.%s falló: %s: %s", nombre, type(e).__name__, e)
        return "la herramienta falló — respondé con lo que tengas y avisá que faltó ese dato"
    # texto_generado: los números del resultado son agregados producidos por
    # el código (AuM, conteos, %) — se respetan; los NOMBRES se tachan igual
    limpio, _ = pii_gateway.tokenize(crudo, mapping, texto_generado=True)
    return limpio
