"""api/ext/lectura.py — LA ÚNICA puerta por la que la API externa lee datos.

Servicio puro (sin FastAPI). Lee `operaciones.operaciones` —la MISMA tabla que
dibuja la vista OPERACIONES de la mesa— aplicando el MISMO predicado
(`operaciones_sql._ops_where`), no una copia.

⚠️ **Por qué se reusa y no se copia.** Las reglas que definen "una operación" no
son inferibles: el `COALESCE(es_cierre,false)` que hace que el FCI bilateral
entre, la regla de `etapa` que evita contarlo dos veces, el filtro de anulados.
Si se duplicaran, el día que cambie una el accionista y la mesa dirían números
distintos, cada mitad coherente consigo misma, y **nada fallaría** — es
exactamente el modo de falla de la REGLA #9.

## Qué entrega

Los boletos vigentes de las cuentas del cliente, por rango de fecha de
concertación. **Sin paginación**: se pide un rango y se devuelve entero.

Los **anulados no viajan**: los filtra `_ops_where` con `anulado_en IS NULL`, el
mismo predicado que usa la vista de la mesa. Un boleto que se anula deja de
aparecer, así que volver a consultar un período ya entregado reconcilia solo.

El único límite es `EXT_MAX_FILAS`, y no es una página: es la red para que una
consulta desmedida no arme un JSON gigante en memoria y se lleve puesta la API
que comparte toda la mesa. Quien lo alcanza recibe un mensaje que le dice que
consulte por períodos más cortos.
"""
from __future__ import annotations

from datetime import UTC, datetime

from api.services._sql import _q
from api.services.operaciones_sql import _ops_where
from config import EXT_MAX_FILAS

# Columnas que viajan SIEMPRE. Deliberadamente NO se exponen `segmento`,
# `nivel_3`, `es_cierre` ni `etapa`. Los dos primeros son nuestra clasificación
# comercial interna (cómo segmentamos al cliente). Tampoco viaja el string
# interno de la unidad de Aunesa: el título se identifica por TICKER y nada más
# (decisión del user 2026-08-27 — un `ticker` vacío se acepta como tal).
# `es_cierre` es una marca
# nuestra —`"CIERRE" in tipo_operacion`, ver operaciones_informes.py:313—, así
# que el dato ya viaja en `tipo_operacion` con el nombre real de la operación.
# Y `etapa` (solicitud/liquidacion del FCI bilateral) es la mecánica con la que
# NOSOTROS evitamos contar dos veces esos boletos: eso ya lo resuelve el
# predicado antes de la respuesta, así que del otro lado sería jerga sin uso.
# Default-deny: agregar se puede; lo que se entregó una vez, no se saca.
_COLS = [
    "boleto",
    "to_char(concertacion, 'YYYY-MM-DD') AS fecha",
    "id_cuenta",
    "denominacion AS cuenta_nombre",
    # TICKER: es lo que se relaciona fácil del otro lado (contra su propio
    # catálogo, contra un proveedor de precios, contra una planilla). Sale del
    # catálogo `portafolio.assets`, cuya PK es `unidad` y matchea contra
    # `operaciones.instrumento`.
    #
    # Subconsulta escalar y NO un JOIN, por dos razones: (1) `assets` también
    # tiene una columna `instrumento` (el símbolo de Primary) y un JOIN dejaría
    # ese nombre ambiguo en el resto del SELECT y en `_ops_where`; (2) el join
    # no puede alterar el conjunto de filas ni aunque cambie el catálogo. Es el
    # mismo patrón que ya usa la vista de la mesa (operaciones_sql.py:626).
    "(SELECT a.ticker FROM portafolio.assets a "
    " WHERE a.unidad = operaciones.instrumento) AS ticker",
    "tipo_operacion",
    "operacion",
    "cantidad",
    "bruto",
    "moneda",
    "mercado",
    "tasa",
    "mep",
]
_COLS_ARANCEL = ["arancel"]


class RangoDemasiadoGrande(Exception):
    """El rango pedido devuelve más de `EXT_MAX_FILAS`. El router la traduce a 400."""



def _iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(v)


def _num(v) -> float | None:
    """Montos y cantidades como NÚMERO JSON (decisión del user, 2026-08-26).

    Un número es un número: el consumidor lo suma sin parsear y es lo que hacen
    las APIs del rubro (1816 entre ellas). El riesgo real de los `float` es la
    deriva al sumar MUCHOS decimales en binario (0.1+0.2 = 0.30000000000000004),
    y a nuestras magnitudes es de centavos sobre millones: un entero es exacto
    hasta 2^53 ≈ 9.007e15, muy por encima de cualquier boleto.

    Está documentado para el consumidor en `docs/API_EXTERNA.md`: si concilia
    sumando decenas de miles de filas, que sume en centavos (enteros).
    """
    return None if v is None else float(v)


# ── lectura ──────────────────────────────────────────────────────────────────
def operaciones(
    *,
    cuentas: tuple[str, ...],
    desde: str | None = None,
    hasta: str | None = None,
    incluir_aranceles: bool = False,
) -> dict:
    """Boletos del scope en el rango pedido. `cuentas` YA viene verificado.

    Levanta `RangoDemasiadoGrande` si el resultado supera `EXT_MAX_FILAS`. Se pide
    UNA fila de más que el tope para poder distinguir "justo el tope" de "se pasó"
    sin tener que contar el universo entero con un COUNT aparte.
    """
    where, p = _ops_where(
        scope=cuentas,
        arancel=True,             # sin filtro de moneda + cierres con arancel (caución)
    )
    if desde:
        where += " AND concertacion >= %(desde)s"
        p["desde"] = desde
    if hasta:
        where += " AND concertacion <= %(hasta)s"
        p["hasta"] = hasta

    cols = list(_COLS) + (_COLS_ARANCEL if incluir_aranceles else [])
    p["_lim"] = EXT_MAX_FILAS + 1
    rows = _q(
        f"SELECT {', '.join(cols)} FROM operaciones "
        f"WHERE {where} ORDER BY concertacion, boleto LIMIT %(_lim)s",
        p,
    )
    if len(rows) > EXT_MAX_FILAS:
        raise RangoDemasiadoGrande(
            f"el rango pedido supera las {EXT_MAX_FILAS:,} operaciones; "
            "consultá por períodos más cortos (por ejemplo, mes a mes)"
            .replace(",", ".")
        )

    filas = [_fila(r, incluir_aranceles) for r in rows]
    return {"operaciones": filas, "total": len(filas)}


def _fila(r: dict, con_arancel: bool) -> dict:
    """Una fila cruda → el JSON del contrato. Un solo lugar arma la respuesta."""
    out = {
        "boleto": r["boleto"],
        "fecha": r["fecha"],
        "cuenta": r["id_cuenta"],
        "cuenta_nombre": r["cuenta_nombre"],
        "ticker": r["ticker"],
        "tipo_operacion": r["tipo_operacion"],
        "operacion": r["operacion"],
        "cantidad": _num(r["cantidad"]),
        "bruto": _num(r["bruto"]),
        "moneda": r["moneda"],
        "mercado": r["mercado"],
        # `tasa` es el "precio" de los boletos MAV (pagarés/cheques), en PORCENTAJE
        # (6 = 6%). null ≠ 0: 0 es una tasa real.
        "tasa": _num(r["tasa"]),
        # TC del día del boleto, para que el consumidor pueda dolarizar con el
        # MISMO número que usamos nosotros y no con uno propio.
        "mep": _num(r["mep"]),
    }
    if con_arancel:
        out["arancel"] = _num(r["arancel"])
        out["arancel_moneda"] = "ARS"   # SIEMPRE ARS (sql/schema.sql:270). Viaja
                                        # explícito aunque sea fijo: el contrato
                                        # se explica solo y no hay que suponer.
    return out


def meta(*, cuentas: tuple[str, ...]) -> dict:
    """Cobertura de datos del scope: hasta cuándo hay, y cuándo se ingestó por última vez.

    ⚠️ Existe para que "no operó ese día" y "todavía no ingesté ese día" no se vean
    iguales del otro lado. Es el invariante #1 del AV AGENT —una corrida que no
    pudo mirar no cierra nada— aplicado a un tercero: sin este endpoint, un día
    vacío es indistinguible de un día que todavía no llegó.
    """
    where, p = _ops_where(scope=cuentas, arancel=True)
    rows = _q(
        "SELECT to_char(MIN(concertacion), 'YYYY-MM-DD') AS primera, "
        "       to_char(MAX(concertacion), 'YYYY-MM-DD') AS ultima, "
        "       MAX(ingestado_en) AS ultima_ingesta, "
        "       COUNT(*) AS total "
        f"FROM operaciones WHERE {where}",
        p,
    )
    r = rows[0] if rows else {}
    return {
        "primera_operacion": r.get("primera"),
        "ultima_operacion": r.get("ultima"),
        "ultima_ingesta": _iso(r.get("ultima_ingesta")),
        "total_operaciones": int(r.get("total") or 0),
    }
