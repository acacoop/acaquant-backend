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

## Los dos modos de lectura, y por qué hacen falta los dos

**FOTO** (`desde`/`hasta`) — "dame agosto". Ordena por `id` y pagina por `id`.
Es para la carga inicial y para reportes.

**INCREMENTAL** (`actualizado_desde`) — "dame todo lo que cambió desde mi última
corrida". Es el modo que sostiene una integración viva, y existe porque **un
boleto de un día viejo cambia después**: backfills, la tasa que rellena
`jobs/ops_tasa_mav`, la etapa que escribe `jobs/fci_bilateral`, y sobre todo las
ANULACIONES. Un consumidor que sólo pidiera por fecha de concertación se
quedaría con datos viejos y no se enteraría nunca.

⚠️ **La anulación NO toca `ingestado_en`** (`operaciones_informes.py:511` y
`anulados.py:82` hacen `SET anulado_en = now()` y nada más). Por eso el reloj de
esta API es `GREATEST(ingestado_en, anulado_en)` y no `ingestado_en` a secas: si
mirara sólo la ingesta, el aviso de que un boleto se anuló **nunca llegaría**, que
es justo el evento que más importa entregar.
"""
from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime

from api.services._sql import _q
from api.services.operaciones_sql import _ops_where

# El reloj de la API: cuándo cambió esta fila POR ÚLTIMA VEZ, sea por ingesta o
# por anulación. `to_timestamp(0)` (epoch) para las filas viejas sin ingesta, así
# el orden es total y el cursor no puede saltearse una.
_ACT = ("GREATEST(COALESCE(ingestado_en, to_timestamp(0)), "
        "COALESCE(anulado_en, to_timestamp(0)))")

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
    "(anulado_en IS NOT NULL) AS anulado",
    f"{_ACT} AS actualizado_en",
    "id AS _id",
]
_COLS_ARANCEL = ["arancel"]

LIMIT_DEFAULT = 500


# ── cursor ───────────────────────────────────────────────────────────────────
def _cursor_encode(partes: list[str]) -> str:
    """Cursor opaco. base64 de los valores de orden, no un offset.

    Es opaco para que nadie del otro lado construya uno a mano y quede atado a
    nuestro esquema; NO es una credencial (no otorga acceso — el scope se
    verifica aparte, en cada request).
    """
    return base64.urlsafe_b64encode("|".join(partes).encode()).decode().rstrip("=")


def _cursor_decode(cursor: str) -> list[str]:
    relleno = "=" * (-len(cursor) % 4)
    try:
        return base64.urlsafe_b64decode(cursor + relleno).decode().split("|")
    except (binascii.Error, UnicodeDecodeError, ValueError) as e:
        raise ValueError("cursor inválido") from e


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
    actualizado_desde: str | None = None,
    cursor: str | None = None,
    limit: int = LIMIT_DEFAULT,
    incluir_aranceles: bool = False,
) -> dict:
    """Boletos del scope, paginados por cursor. `cuentas` YA viene verificado.

    En modo INCREMENTAL los anulados VIAJAN (con `anulado: true`): es la única
    forma de que el consumidor borre de su lado un boleto que dejó de valer. En
    modo FOTO no viajan — una foto de agosto es lo que efectivamente se operó.
    """
    incremental = actualizado_desde is not None
    where, p = _ops_where(
        scope=cuentas,
        arancel=True,             # sin filtro de moneda + cierres con arancel (caución)
        incluir_anulados=incremental,
    )

    if desde:
        where += " AND concertacion >= %(desde)s"
        p["desde"] = desde
    if hasta:
        where += " AND concertacion <= %(hasta)s"
        p["hasta"] = hasta

    if incremental:
        # El filtro por columna va aparte del ORDER BY para que el planner pueda
        # usar los índices reales (`ix_ops_ingestado`, `ix_ops_anulado`); la
        # expresión GREATEST no es indexable por sí sola.
        where += (" AND (ingestado_en >= %(act)s OR anulado_en >= %(act)s)"
                  f" AND {_ACT} >= %(act)s")
        p["act"] = actualizado_desde
        orden = f"{_ACT}, id"
        if cursor:
            c_ts, c_id = _partes_cursor(cursor, 2)
            where += f" AND ({_ACT}, id) > (%(c_ts)s::timestamptz, %(c_id)s::bigint)"
            p["c_ts"], p["c_id"] = c_ts, c_id
    else:
        orden = "id"
        if cursor:
            (c_id,) = _partes_cursor(cursor, 1)
            where += " AND id > %(c_id)s::bigint"
            p["c_id"] = c_id

    cols = list(_COLS) + (_COLS_ARANCEL if incluir_aranceles else [])
    p["_lim"] = limit + 1          # +1 para saber si hay más SIN contar el total
    rows = _q(
        f"SELECT {', '.join(cols)} FROM operaciones "
        f"WHERE {where} ORDER BY {orden} LIMIT %(_lim)s",
        p,
    )

    hay_mas = len(rows) > limit
    rows = rows[:limit]
    siguiente = None
    if hay_mas and rows:
        ult = rows[-1]
        siguiente = (_cursor_encode([_iso(ult["actualizado_en"]) or "", str(ult["_id"])])
                     if incremental else _cursor_encode([str(ult["_id"])]))

    return {
        "operaciones": [_fila(r, incluir_aranceles) for r in rows],
        "paginacion": {
            "limit": limit,
            "devueltas": len(rows),
            "hay_mas": hay_mas,
            "siguiente_cursor": siguiente,
        },
    }


def _partes_cursor(cursor: str, n: int) -> list[str]:
    partes = _cursor_decode(cursor)
    if len(partes) != n:
        raise ValueError("cursor inválido")
    return partes


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
        "anulado": bool(r["anulado"]),
        "actualizado_en": _iso(r["actualizado_en"]),
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
