"""api/services/carteras_informe.py — el INFORME de una cuenta, en un request.

Es la vista NEGOCIO → CARTERAS (`/valuaciones`) contada como el informe de ACA:
la misma cuenta que antes se leía en cuatro paneles apretados, ahora en tres
bloques que se leen de arriba a abajo.

    RESUMEN   → cuánto vale, cómo se compone por cartera y contra el mes anterior
    ACTIVOS   → el detalle título por título, agrupado por cartera
    MÉTRICAS  → la misma plata abierta por clase de activo, emisor y calificación

Tres decisiones que explican por qué esto es un service y no cuentas en el front:

1. **UN request.** La vista pegaba a `/serie`, `/mensual`, `/posiciones-actuales`
   y `/variacion` por separado y armaba los totales en el navegador. Con la
   fórmula duplicada del lado del front, la pantalla puede contradecir al informe
   y ninguna de las dos versiones es la verdad — el mismo criterio que rige en
   `api/services/aca.py`. Además el PDF sale de ESTE payload: si el informe se
   calculara en la pantalla, el PDF sería una segunda implementación.

2. **Las carteras se DERIVAN de la posición.** Acá no hay 4 carteras canónicas
   como en ACA: una cuenta tiene las carteras que tiene (HD, RENTA VARIABLE,
   MONEDAS, FCI…). El orden lo pone el monto del mes ACTUAL, y el cuadro
   comparativo usa el MISMO orden aunque una cartera ya no exista — que una
   cartera que el mes pasado valía algo hoy cierre en cero es información; que la
   fila desaparezca no dice nada.

3. **La regla de moneda es la de Manager → ACA** (`aca.moneda_regla`), no una
   propia. Dos tablas de reglas para la misma pregunta ("¿esta cartera es dólar o
   es pesos?") es exactamente el patrón que la REGLA #9 del repo prohíbe: cuando
   se separan no falla nada, cada mitad sigue siendo coherente y las dos
   pantallas contestan distinto con seguridad. Lo que ninguna regla ubica NO se
   reparte a dedo: cae en `sin_clasificar` y la vista lo canta.

   ÚNICA excepción, y no es una segunda regla: el EFECTIVO. Una regla por cartera
   no puede partir `MONEDAS`, que tiene los pesos y los dólares de la cuenta
   adentro; para el cash el instrumento ES la moneda (`USD`/`USDC`/`USDL` → usd,
   `ARS` → ars), así que se lee de ahí. Las constantes son las que ya usa
   `api/services/valuaciones.py` — importadas, no copiadas.

Lectura: módulo `portfolios` + el scope de grupos del usuario, igual que el resto
de `/api/valuaciones` (el gate vive en el router). Doc: `docs/MAPA_APP.md`.
"""
from __future__ import annotations

import logging
from typing import Any

from api.services._sql import _q
from api.services.valuaciones import _CASH_UNIDADES, _MONEDAS_USD_EQUIV

logger = logging.getLogger("api.carteras_informe")

# Etiqueta del cuadro/panel de cada cartera. Las cuatro de ACA con su rótulo de
# planilla (para que un mismo nombre no se lea distinto según la vista); el resto
# se muestra tal cual viene del maestro de títulos.
_LABEL: dict[str, str] = {
    "ARS": "Cartera Pesos",
    "DL": "Cartera DL",
    "HD": "Cartera HD",
    "FCI": "Cartera FCI",
}
# Etiqueta del bloque de los títulos SIN FICHA en Manager → Títulos. No se
# esconden: suman al total igual que cualquier otro, así que necesitan su cuadro.
_SIN_CARTERA = "Sin cartera"


def _cartera(p: dict) -> str:
    """La cartera de una posición, normalizada. '' = el título no tiene ficha."""
    return (p.get("cartera") or "").strip().upper()


def _label(cartera: str) -> str:
    return _LABEL.get(cartera, f"Cartera {cartera}") if cartera else _SIN_CARTERA


def _pond(monto: float, total: float) -> float | None:
    """Fracción (0.42 = 42%). None con total 0 — dividir por cero es inventar."""
    return (monto / total) if total else None


def _usd(monto: float, mep: float | None) -> float | None:
    """Espejo en dólares de un AGREGADO, al MEP del día del snapshot.

    Va resuelto del backend y no en la pantalla por la misma razón que todo lo
    demás: el toggle ARS/USD tiene que cambiar QUÉ CAMPO se muestra, nunca hacer
    una cuenta. None (no 0) si no hay MEP para esa fecha — el front deshabilita
    el toggle en vez de mostrar ceros.

    Ojo: esto es una conversión a UN tipo de cambio, no el USD nativo del motor
    de PnL (que ancla cada compra a su propio MEP). Por eso el costo y el PnL de
    cada título traen su propio `*_usd` y NO se derivan de acá.
    """
    if not mep:
        return None
    return round(monto / mep, 2)


# ─────────────────────────────────────────────────────────────
# Moneda de una posición
# ─────────────────────────────────────────────────────────────

def _moneda_de(p: dict, reglas: dict[str, dict[str, str]]) -> str | None:
    """'usd' | 'ars' | None (sin clasificar).

    Orden: efectivo (el instrumento ES la moneda) → clase → cartera. La clase le
    gana a la cartera por el mismo motivo que en ACA: es lo que permite partir el
    FCI por moneda sin sacarlo de su cartera.
    """
    unidad = (p.get("unidad") or "").strip().upper()
    if unidad in _CASH_UNIDADES:
        return "usd" if unidad in _MONEDAS_USD_EQUIV else "ars"
    from api.services.aca import _moneda_de as _moneda_aca
    return _moneda_aca(p.get("cartera") or "", p.get("clase_activo") or "", reglas)


# ─────────────────────────────────────────────────────────────
# RESUMEN — el cuadro por cartera de un snapshot
# ─────────────────────────────────────────────────────────────

def _bloque(pos: dict, orden: list[str], reglas: dict[str, dict[str, str]],
            a3500: float | None = None) -> dict:
    """Un cuadro del resumen: valuaciones + monto/ponderación por cartera + los
    totales por moneda. `orden` fija las filas para que los dos cuadros del
    comparativo se lean uno al lado del otro."""
    filas = pos.get("posiciones") or []
    total = float(pos.get("total") or 0.0)

    por_cartera = dict.fromkeys(orden, 0.0)
    fuera = 0.0
    por_moneda = {"usd": 0.0, "ars": 0.0, "sin_clasificar": 0.0}
    sin_regla: set[str] = set()
    for p in filas:
        monto = float(p.get("valuacion") or 0.0)
        c = _cartera(p)
        if c in por_cartera:
            por_cartera[c] += monto
        else:
            # Una cartera que aparece en el mes ANTERIOR y ya no existe hoy: no
            # tiene fila propia (el orden lo fija el mes actual) pero la plata no
            # se puede perder, así que se agrupa acá.
            fuera += monto
        moneda = _moneda_de(p, reglas)
        if moneda:
            por_moneda[moneda] += monto
        else:
            por_moneda["sin_clasificar"] += monto
            sin_regla.add(p.get("clase_activo") or f"(cartera {c or '?'})")

    mep = pos.get("mep")
    # La PONDERACIÓN es la misma en las dos monedas (mismo divisor), así que
    # NO se duplica: el toggle ARS/USD cambia el monto y nunca el porcentaje.
    def _fila(monto: float) -> dict:
        return {"monto": round(monto, 2), "monto_usd": _usd(monto, mep),
                "ponderacion": _pond(monto, total)}

    return {
        "fecha": pos.get("fecha"),
        "mep": mep,
        "a3500": a3500,
        "valuacion_ars": round(total, 2),
        "valuacion_usd": pos.get("total_usd"),
        # La misma plata al oficial. None (no 0) si falta el TC: dividir por un
        # dato que no existe es inventar el número más visible del informe.
        "valuacion_a3500": round(total / a3500, 2) if a3500 else None,
        "carteras": [{"cartera": c, "label": _label(c), **_fila(por_cartera[c])}
                     for c in orden],
        "otras_carteras": _fila(fuera) if fuera else None,
        "total_dolarizado": _fila(por_moneda["usd"]),
        "total_pesos": _fila(por_moneda["ars"]),
        "sin_clasificar": {**_fila(por_moneda["sin_clasificar"]),
                           "claves": sorted(sin_regla)},
        "n_activos": len(filas),
    }


def _a3500(fecha: str | None) -> float | None:
    """El A3500 (mayorista BCRA) vigente al día del snapshot, o None.

    Es el OTRO tipo de cambio del informe: el MEP es al que se puede salir hoy y
    el A3500 es el oficial con el que se reporta hacia afuera. La misma plata
    contada a dos cambios, que es lo que ya hace el informe de ACA.

    Sale de `macro.series_macro` clave DOLAR (el fixing diario del BCRA), la
    MISMA fuente que usa el briefing — no una consulta propia que pueda dar otro
    número. `punto_asof` toma el último cierre con fecha <= la pedida: un sábado
    o un feriado no tienen fixing y ahí corresponde el del último hábil, no un
    hueco.
    """
    if not fecha:
        return None
    try:
        from core.series_macro import punto_asof
        p = punto_asof("DOLAR", fecha, positivo=True)
        return float(p["valor"]) if p else None
    except Exception:
        # El tipo de cambio es CONTEXTO del informe, no el informe: si la serie
        # no está, la card queda vacía y el resto sale igual.
        logger.exception("carteras_informe: A3500 falló para %s", fecha)
        return None


def _cierre_mes_anterior(id_cuenta: str, fecha: str) -> str | None:
    """El último snapshot conciliado ANTERIOR al mes de `fecha`.

    Sale de `portafolio.tenencia` (la foto), nunca de `tenencia_live`: el
    comparativo del informe es contra un mes CERRADO. None = la cuenta no tiene
    historia previa, y ahí el cuadro comparativo simplemente no se dibuja.
    """
    r = _q("SELECT max(fecha) AS f FROM portafolio.tenencia "
           "WHERE id_cuenta = %(c)s AND aum = 'si' "
           "AND fecha < date_trunc('month', %(f)s::date)",
           {"c": id_cuenta, "f": fecha})
    return r[0]["f"].isoformat() if r and r[0]["f"] is not None else None


# ─────────────────────────────────────────────────────────────
# ACTIVOS — el detalle, agrupado por cartera
# ─────────────────────────────────────────────────────────────

def _detalle(pos: dict, orden: list[str]) -> dict:
    """Un bloque por cartera con sus títulos ordenados por valuación.

    ⚠️ **`share` se re-expresa como FRACCIÓN.** `posiciones_actuales` lo devuelve
    en PORCENTAJE (0-100) y todo el resto de este payload —ponderaciones, shares
    de métricas— viaja en fracción (0-1). Dos unidades para el mismo concepto
    dentro de la misma respuesta es la clase de detalle que se paga con un
    número cien veces más grande en pantalla, así que acá se unifica.

    Es el peso del título sobre la CUENTA, que es la única comparación que sirve
    en una tabla donde conviven todas las carteras: un 100% dentro de una
    cartera de dos títulos no dice nada al lado de un 30% de otra.
    """
    filas = pos.get("posiciones") or []
    total = float(pos.get("total") or 0.0)
    mep = pos.get("mep")
    por_cartera: dict[str, list[dict]] = {c: [] for c in orden}
    for p in filas:
        por_cartera.setdefault(_cartera(p), []).append(p)

    bloques = []
    for c, propias in por_cartera.items():
        tot = sum(float(p.get("valuacion") or 0.0) for p in propias)
        bloques.append({
            "cartera": c, "label": _label(c),
            "total": round(tot, 2),
            "total_usd": _usd(tot, mep),
            "ponderacion": _pond(tot, total),
            "filas": [
                {**p, "share": _pond(float(p.get("valuacion") or 0.0), total)}
                for p in sorted(propias, key=lambda x: -float(x.get("valuacion") or 0.0))
            ],
        })
    # Las carteras del `orden` primero (monto desc, ya vienen ordenadas); lo que
    # se sumó después va al final, y el bloque sin ficha SIEMPRE último.
    bloques.sort(key=lambda b: (b["cartera"] == "", orden.index(b["cartera"])
                                if b["cartera"] in orden else len(orden)))
    return {
        "bloques": bloques,
        "total_usd": pos.get("total_usd"),
        # Los títulos SIN FICHA se listan aparte para el aviso de la pantalla,
        # pero NO se sacan de su bloque: se ven en el cuadro y se arreglan en
        # Manager → Títulos.
        "huerfanos": [p.get("ticker") or p.get("unidad") for p in filas if not _cartera(p)],
        "total": round(total, 2),
    }


# ─────────────────────────────────────────────────────────────
# MÉTRICAS — la misma plata abierta por otro eje
# ─────────────────────────────────────────────────────────────

def _agrupar(filas: list[dict], campo: str, denominador: float,
             mep: float | None = None) -> list[dict]:
    """Agrupa por `campo` (monto desc). Sin catálogo: a diferencia de ACA, acá no
    hay una lista curada de emisores destacados — la apertura es la que tenga la
    cuenta."""
    acum: dict[str, dict] = {}
    for p in filas:
        clave = (p.get(campo) or "").strip() or "—"
        st = acum.setdefault(clave, {"clave": clave, "monto": 0.0, "n": 0})
        st["monto"] += float(p.get("valuacion") or 0.0)
        st["n"] += 1
    return [
        {**st, "monto": round(st["monto"], 2), "monto_usd": _usd(st["monto"], mep),
         "share": _pond(st["monto"], denominador)}
        for st in sorted(acum.values(), key=lambda x: -x["monto"])
    ]


def _metricas(pos: dict, orden: list[str]) -> dict:
    """Apertura por CLASE DE ACTIVO (dentro de cada cartera) y por EMISOR /
    CALIFICACIÓN (sobre el total de la cuenta).

    Los denominadores NO son el mismo, y es a propósito: la clase de activo
    responde cómo está compuesta ESA cartera, así que va sobre el total de la
    cartera; el emisor y la calificación responden cuánto pesa ese riesgo en toda
    la cuenta, así que van sobre el total.
    """
    filas = pos.get("posiciones") or []
    total = float(pos.get("total") or 0.0)
    mep = pos.get("mep")

    por_clase = []
    for c in orden:
        propias = [p for p in filas if _cartera(p) == c]
        tot = sum(float(p.get("valuacion") or 0.0) for p in propias)
        por_clase.append({"cartera": c, "label": _label(c), "total": round(tot, 2),
                          "total_usd": _usd(tot, mep),
                          # Cuánto pesa la cartera EN LA CUENTA. Va acá y no se
                          # deriva en la pantalla por la misma razón que todo lo
                          # demás: es el mismo número que muestra el cuadro del
                          # resumen, y calculado en dos lugares un día difiere.
                          "ponderacion": _pond(tot, total),
                          "filas": _agrupar(propias, "clase_activo", tot, mep)})
    return {
        "total": round(total, 2),
        "total_usd": _usd(total, mep),
        "por_clase": por_clase,
        "por_emisor": _agrupar(filas, "emisor", total, mep),
        "por_calificacion": _agrupar(filas, "calificacion", total, mep),
    }


# ─────────────────────────────────────────────────────────────
# La vista
# ─────────────────────────────────────────────────────────────

def vista(id_cuenta: str, fecha: str | None = None, horizonte: str = "t1",
          con_pnl: bool = True) -> dict[str, Any]:
    """El informe completo de una cuenta: RESUMEN + ACTIVOS + MÉTRICAS.

    `fecha` None = la posición de HOY (`portafolio.tenencia_live` en el
    `horizonte` pedido, con fallback a la foto); con fecha es una consulta
    histórica sobre la foto conciliada y `horizonte` se ignora — igual que en
    `posiciones_actuales`, que es de donde sale TODA la plata de este informe.

    El comparativo (`resumen.anterior`) es el cierre del mes anterior. Se pide
    SIN `con_pnl`: el cost-basis del motor es siempre a HOY, así que colgarlo de
    una foto vieja daría un GAN% sobre una posición que ya no existe.
    """
    from api.services import valuaciones_sql as svc_sql

    pos = svc_sql.posiciones_actuales(
        id_cuenta=id_cuenta, fecha=fecha, asof=True, con_pnl=con_pnl,
        horizonte=horizonte)

    # Orden de las carteras: monto desc del mes ACTUAL. Es el mismo orden en el
    # cuadro comparativo, en los bloques de ACTIVOS y en los paneles de MÉTRICAS
    # — tres pantallas que hablan de lo mismo tienen que enumerarlo igual.
    acum: dict[str, float] = {}
    for p in pos.get("posiciones") or []:
        c = _cartera(p)
        if c:
            acum[c] = acum.get(c, 0.0) + float(p.get("valuacion") or 0.0)
    orden = [c for c, _ in sorted(acum.items(), key=lambda kv: -kv[1])]

    from api.services.aca import _reglas_moneda
    reglas = _reglas_moneda()
    tc_oficial = _a3500(pos.get("fecha"))

    anterior = None
    f_anterior = _cierre_mes_anterior(id_cuenta, pos["fecha"]) if pos.get("fecha") else None
    if f_anterior:
        try:
            prev = svc_sql.posiciones_actuales(id_cuenta=id_cuenta, fecha=f_anterior,
                                               asof=False, con_pnl=False)
            # El comparativo se cuenta al TC de SU día, no al de hoy: si no, la
            # variación en dólares sería en parte el movimiento del tipo de cambio.
            anterior = _bloque(prev, orden, reglas, _a3500(f_anterior))
        except Exception:
            # El comparativo es contexto, no el informe: si la foto vieja falla,
            # la vista sale igual sin el segundo cuadro.
            logger.exception("carteras_informe: comparativo falló, cuenta=%s fecha=%s",
                             id_cuenta, f_anterior)

    return {
        "id_cuenta": id_cuenta,
        "fecha": pos.get("fecha"),
        "fecha_anterior": f_anterior,
        "mep": pos.get("mep"),
        "a3500": tc_oficial,
        "horizonte": horizonte if not fecha else None,
        "historico": bool(fecha),
        "resumen": {"actual": _bloque(pos, orden, reglas, tc_oficial), "anterior": anterior},
        "detalle": _detalle(pos, orden),
        "metricas": _metricas(pos, orden),
        # Cost-basis y PnL de la cuenta — los mismos números que la tab PNL
        # TÍTULOS, resueltos por el motor en la corrida que ya pagó `con_pnl`.
        "pnl_disponible": pos.get("pnl_disponible", False),
        "costo_total": pos.get("costo_total"),
        "pnl_total": pos.get("pnl_total"),
        "costo_total_usd": pos.get("costo_total_usd"),
        "pnl_total_usd": pos.get("pnl_total_usd"),
        # `pnl_detalle` (los BOLETOS de cada título, indexados por unidad) NO
        # viaja: alimentaba un panel de auditoría que esta vista ya no tiene, y
        # es lo más pesado que devolvía el endpoint. El PnL por título sigue
        # viniendo en cada fila (costo/pnl/gan%), que es lo que se muestra; el
        # detalle boleto por boleto vive en PNL TÍTULOS, con su propio endpoint.
        "n": pos.get("n", 0),
    }
