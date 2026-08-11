"""api/services/financiamiento.py — vista FINANCIAMIENTO (tab de /operaciones).

QUÉ ES
------
El libro VIVO de instrumentos de financiamiento (pagarés / cheques, mercado MAV):
qué tiene cada cliente, por qué nominal y a qué tasa, de acá en adelante.

Universo = `portafolio.assets` con `cartera = 'FINANCIAMIENTO'` y `vencimiento`
HOY o posterior. Lo vencido no se muestra: ya cobró, no es cartera viva.

POR QUÉ NOMINAL Y NO BRUTO (decisión del user, 2026-08-11)
----------------------------------------------------------
Estos instrumentos se compran CON DESCUENTO: el bruto es lo que se pagó, no lo
que vale la operación. Además la mayoría son dólar-linked / hard dollar pero se
liquidan en pesos, así que el bruto ni siquiera es comparable entre filas. Lo
relevante es la CANTIDAD (el nominal, que es la operación real) y la TASA. Por
eso `valuacion` no se lee acá — a propósito.

DE DÓNDE SALE CADA COSA (y por qué son dos fuentes)
---------------------------------------------------
- CANTIDAD por cliente → `portafolio.tenencia` del último snapshot. Es la
  POSICIÓN: lo que el cliente todavía tiene. Sumar boletos daría el flujo
  (compras + ventas) y contaría dos veces lo que entró y salió.
- TASA → `operaciones.operaciones.tasa` (la rellena `jobs/ops_tasa_mav.py`
  parseando '...100.000,00@6%...' de `negocio_movimientos.informacion`). No hay
  tasa en la tenencia: un pagaré no tiene precio unitario, se negocia A TASA.

El puente entre las dos es el CÓDIGO del instrumento: en assets es el `ticker`
(el contenido del corchete de la unidad, '*ACI250300289') y en el movimiento es
el mismo corchete dentro de `informacion`. Se matchea por (id_cuenta, código).

Una fila SIN tasa es normal y NO es un error: el cliente puede haber recibido el
instrumento por una vía que no dejó boleto MAV con tasa parseada. Se devuelve
`tasa = None` y la vista muestra '—'. NUNCA se inventa un número: la tasa es el
dato por el que existe esta pantalla.

VERIFICAR EN PROD: `python -m scripts.diag_financiamiento` mide cobertura de la
tasa, cuántos assets quedan vigentes y el tamaño real del payload.
"""
from __future__ import annotations

from datetime import date

from api.cache import cached
from core.postgres import get_pool

# Mercados cuyos boletos cotizan A TASA. Mismo criterio (y mismo motivo) que
# `jobs/ops_tasa_mav._MERCADOS`: si mañana se suma otro, va en los dos lados.
_MERCADOS_TASA = ("MAV",)

# Valor de `portafolio.assets.cartera` que define el universo de la vista.
CARTERA = "FINANCIAMIENTO"

# Techo del payload. La vista manda TODO el grano (cuenta × instrumento) al front
# para que el cruce interactivo no pague un refetch por click. Si algún día el
# libro crece más que esto, la respuesta lo dice en `truncado` en vez de mentir
# con una tabla incompleta.
_MAX_FILAS = 20_000

# `vencimiento` es TEXT en assets (lo carga a mano el panel Manager → Assets, y
# lo autocompleta `jobs/assets_autofill` en ISO). Un valor con otro formato NO se
# puede comparar como fecha: se descarta con el regex en vez de reventar la query
# entera con un cast inválido.
_RE_ISO = r"^\d{4}-\d{2}-\d{2}$"


def _fecha_snapshot(conn) -> str | None:
    """Última fecha con tenencia cargada (writer diario `jobs/portafolio_backfill`)."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.tenencia")
        f = cur.fetchone()[0]
    return str(f) if f else None


def _tasas_por_cuenta_codigo(conn) -> dict[tuple[str, str], dict]:
    """{(id_cuenta, código de instrumento): {tasa, n_boletos, tasa_min, tasa_max}}.

    La tasa del par es el PROMEDIO PONDERADO POR NOMINAL de sus boletos: si el
    cliente compró el mismo papel en dos tandas, la tasa que lo representa es la
    de la plata, no la media simple. Cuando los boletos no traen `cantidad` no
    hay con qué ponderar y cae a `avg` — mejor una media simple que un NULL.

    `tasa_min`/`tasa_max` viajan para que la vista pueda avisar cuándo el
    promedio esconde dispersión (comprar a 6% y a 39% promedia en algo que no
    ocurrió nunca).

    Anulados afuera: un boleto anulado no fijó ninguna tasa.
    """
    sql = """
        SELECT n.id_cuenta,
               substring(n.informacion from '\\[([^\\]]+)\\]') AS cod,
               coalesce(
                   sum(o.tasa * abs(o.cantidad))
                       FILTER (WHERE o.cantidad IS NOT NULL AND o.cantidad <> 0)
                   / nullif(sum(abs(o.cantidad))
                       FILTER (WHERE o.cantidad IS NOT NULL AND o.cantidad <> 0), 0),
                   avg(o.tasa)
               ) AS tasa,
               min(o.tasa) AS tasa_min,
               max(o.tasa) AS tasa_max,
               count(*)    AS n_boletos
        FROM operaciones.operaciones o
        JOIN operaciones.negocio_movimientos n ON n.comprobante = o.boleto
        WHERE o.mercado = ANY(%(mercados)s)
          AND o.tasa IS NOT NULL
          AND o.anulado_en IS NULL
          AND n.id_cuenta IS NOT NULL
          AND n.informacion ~ '\\[[^\\]]+\\]'
        GROUP BY 1, 2
    """
    out: dict[tuple[str, str], dict] = {}
    with conn.cursor() as cur:
        cur.execute(sql, {"mercados": list(_MERCADOS_TASA)})
        for id_cuenta, cod, tasa, tmin, tmax, n in cur.fetchall():
            if not cod or tasa is None:
                continue
            out[(str(id_cuenta), str(cod))] = {
                "tasa": float(tasa),
                "tasa_min": float(tmin) if tmin is not None else None,
                "tasa_max": float(tmax) if tmax is not None else None,
                "n_boletos": int(n or 0),
            }
    return out


def _posiciones(conn, fecha: str, hoy: str, scope: tuple[str, ...] | None) -> list[tuple]:
    """Tenencia del snapshot cruzada con los assets de FINANCIAMIENTO vigentes.

    El cruce va por `unidad` (la PK de assets y la clave de tenencia). El filtro
    de vigencia se aplica en el CTE, así el JOIN ya arranca chico.
    """
    cond = ["t.fecha = %(fecha)s", "t.cantidad IS NOT NULL", "t.cantidad <> 0"]
    p: dict = {"fecha": fecha, "hoy": hoy, "cartera": CARTERA, "iso": _RE_ISO}
    if scope is not None:
        cond.append("t.id_cuenta = ANY(%(scope)s)")
        p["scope"] = list(scope)
    sql = f"""
        WITH vigentes AS (
            SELECT unidad, ticker, emisor, vencimiento::date AS vencimiento
            FROM portafolio.assets
            WHERE upper(btrim(cartera)) = %(cartera)s
              AND vencimiento ~ %(iso)s
              AND vencimiento::date >= %(hoy)s
        )
        SELECT t.id_cuenta, t.cuenta, v.unidad, v.ticker, v.emisor,
               v.vencimiento, t.cantidad, t.moneda, t.aum
        FROM portafolio.tenencia t
        JOIN vigentes v ON v.unidad = t.unidad
        WHERE {' AND '.join(cond)}
        ORDER BY v.vencimiento, t.id_cuenta
        LIMIT {_MAX_FILAS + 1}
    """
    with conn.cursor() as cur:
        cur.execute(sql, p)
        return cur.fetchall()


def armar_filas(rows, tasas: dict[tuple[str, str], dict], hoy: str) -> tuple[list[dict], int]:
    """(filas de tenencia, tasas por par) → filas del payload + cuántas resolvieron tasa.

    PURA a propósito: es acá donde se decide qué tasa le toca a cada posición, y
    esa decisión se testea sin base (`tests/unit/test_financiamiento.py`).
    """
    hoy_d = date.fromisoformat(hoy)
    filas: list[dict] = []
    con_tasa = 0
    for id_cuenta, cuenta, unidad, ticker, emisor, vto, cantidad, moneda, aum in rows:
        cod = (ticker or "").strip()
        t = tasas.get((str(id_cuenta), cod)) if cod else None
        if t:
            con_tasa += 1
        filas.append({
            "id_cuenta": str(id_cuenta),
            # El nombre lindo vive en `cuenta` ('[534] NOMBRE'); si falta, el id
            # solo es preferible a una celda vacía.
            "cuenta": (cuenta or "").strip() or str(id_cuenta),
            "unidad": unidad,
            "ticker": cod or unidad,
            "emisor": (emisor or "").strip(),
            "vencimiento": vto.isoformat(),
            "dias": (vto - hoy_d).days,
            "cantidad": float(cantidad),
            "moneda": (moneda or "").strip().upper(),
            "aum": (aum or "").strip().lower() == "si",
            "tasa": t["tasa"] if t else None,
            "tasa_min": t["tasa_min"] if t else None,
            "tasa_max": t["tasa_max"] if t else None,
            "n_boletos": t["n_boletos"] if t else 0,
        })
    return filas, con_tasa


@cached(ttl=300)
def libro(scope: tuple[str, ...] | None = None, hoy: str | None = None) -> dict:
    """Payload completo de la vista FINANCIAMIENTO.

    Devuelve el GRANO (una fila por cuenta × instrumento) y no los agregados: las
    cuatro tablas de la pantalla se cruzan entre sí (elegir un cliente refiltra
    instrumentos y gráfico), y resolver eso con agregados server-side obligaría a
    un round-trip por click. El grano está acotado por `_MAX_FILAS`.

    `scope` = tuple de id_cuenta visibles del usuario (grupos), o None = todas.
    """
    hoy = hoy or date.today().isoformat()
    with get_pool().connection() as conn:
        fecha = _fecha_snapshot(conn)
        if not fecha:
            return {"fecha": None, "hoy": hoy, "filas": [], "n": 0,
                    "truncado": False, "con_tasa": 0}
        rows = _posiciones(conn, fecha, hoy, scope)
        truncado = len(rows) > _MAX_FILAS
        rows = rows[:_MAX_FILAS]
        tasas = _tasas_por_cuenta_codigo(conn) if rows else {}

    filas, con_tasa = armar_filas(rows, tasas, hoy)
    return {
        "fecha": fecha,          # snapshot de tenencia usado (no es "hoy" si el job no corrió)
        "hoy": hoy,
        "filas": filas,
        "n": len(filas),
        "con_tasa": con_tasa,    # cuántas filas resolvieron tasa (el resto muestra '—')
        "truncado": truncado,
    }
