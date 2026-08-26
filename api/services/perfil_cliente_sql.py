"""api/services/perfil_cliente_sql.py — la ficha operativa de UN cliente.

Contesta tres preguntas sobre una cuenta, en la ventana de los últimos N meses:

    1. ¿Cuál fue su ÚLTIMA operación?  (una línea, con el boleto que la fija)
    2. ¿Cuánto arancel dejó MES A MES? (serie mm-aa para el gráfico de barras)
    3. ¿En QUÉ opera?                  (share del volumen por tipo de operación)

**Módulo aparte y genérico a propósito.** Nace para el detalle de SE ESTÁN
APAGANDO, pero el share por tipo de operación se va a reusar en otras pantallas:
por eso no recibe nada de esa vista (ni cortes, ni contexto), solo `id_cuenta`.

⚠️ **VOLUMEN y ARANCEL no se filtran igual, y confundirlos es plata mal contada:**

* **Volumen** (`bruto`) **EXCLUYE los cierres** (`es_cierre = true`). La apertura
  de la caución ya contó el volumen; sumar el cierre lo cuenta dos veces.
* **Arancel** los **INCLUYE**: el arancel de una caución vive SOLO en el cierre.
  Filtrarlos haría desaparecer el arancel de toda la caución sin que nada avise.
* En los dos, `etapa <> 'solicitud'` (la liquidación ya cuenta) y `anulado_en IS
  NULL`.

⚠️ **El `bruto` viene en la moneda del boleto.** Sumar pesos con dólares da un
número que parece plata y no lo es. Se pesifica con `comercial_sql._pesif`, que
usa el `mep` DEL BOLETO — así el share de un mes viejo no se mueve con el dólar
de hoy.
"""
from __future__ import annotations

from datetime import date

from api.services._sql import _q
from api.services.comercial import _cv, _factor_usd, _hoy_art
from api.services.comercial_sql import (
    _act_where,
    _arancel_where,
    _f,
    _fin_de_mes,
    _iso,
    _pesif,
)
from api.services.profundidad_sql import _label, _op_label

# Ventana por defecto y su tope. 12 meses es lo que hace falta para ver un patrón
# anual (y es la misma ventana con la que ANÁLISIS CUANTITATIVO mide el ritmo).
MESES_DEF = 12
MESES_MAX = 60

# Qué boleto suma VOLUMEN: los cierres afuera (ver el docstring del módulo).
_VOL_WHERE = ("COALESCE(o.es_cierre, false) = false "
              "AND o.etapa IS DISTINCT FROM 'solicitud'")

# Columnas de la última operación: crudas, sin derivar nada.
_COLS_ULT = ("o.boleto, o.concertacion, o.operacion, o.tipo_operacion, o.instrumento, "
             "o.mercado, o.moneda, o.bruto, o.arancel, o.cantidad, o.etapa, o.es_cierre")


def _ventana(meses: int, hasta: date) -> tuple[date, list[tuple[str, str, date]]]:
    """(primer día de la ventana, [(mes, label, fin_de_mes)]) — `meses` meses hacia
    atrás contando el de `hasta`. La lista se arma acá y NO en la query: así la
    serie tiene TODOS los meses, incluidos los que el cliente no operó. Un mes sin
    barra y un mes ausente no se ven igual en un gráfico."""
    n = max(1, min(int(meses or MESES_DEF), MESES_MAX))
    total = hasta.year * 12 + hasta.month - 1
    out = []
    for k in range(n - 1, -1, -1):
        t = total - k
        a, m = t // 12, t % 12 + 1
        out.append((f"{a:04d}-{m:02d}", _label(a, m), _fin_de_mes(a, m)))
    a0, m0 = (total - n + 1) // 12, (total - n + 1) % 12 + 1
    return date(a0, m0, 1), out


def perfil_cliente(*, id_cuenta: str, meses: int = MESES_DEF,
                   moneda: str = "ARS", hasta: str | None = None) -> dict:
    """Ficha operativa de una cuenta. `hasta` (ISO) = último día a considerar
    (default: hoy) — para que el detalle cuadre con la foto que se está mirando."""
    idc = str(id_cuenta)
    fin = date.fromisoformat(hasta) if hasta else _hoy_art()
    ini, meses_lista = _ventana(meses, fin)
    factor = _factor_usd(moneda)
    p = {"idc": idc, "ini": ini, "fin": fin}

    cab = _q("SELECT u.denominacion, c.nivel_1, c.nivel_3, c.operador_email, "
             "       o.nombre AS operador_nombre, c.fecha_alta_legajo, c.estado "
             "FROM cuentas u LEFT JOIN comitentes c ON c.id_cuenta = u.id_cuenta "
             "LEFT JOIN operadores o ON o.email = c.operador_email "
             "WHERE u.id_cuenta = %(idc)s", {"idc": idc})
    cab = cab[0] if cab else {}

    # ── 1) La ÚLTIMA operación ────────────────────────────────────────────────
    # Sin tope de ventana: la última es la última, aunque sea de hace dos años. Si
    # se acotara a la ventana, un cliente dormido mostraría "—" y parecería que
    # nunca operó.
    ult = _q(f"SELECT {_COLS_ULT} FROM operaciones o "
             f"WHERE o.id_cuenta = %(idc)s AND {_act_where('o')} "
             f"  AND o.concertacion <= %(fin)s "
             f"ORDER BY o.concertacion DESC NULLS LAST, o.boleto DESC LIMIT 1", p)
    u = ult[0] if ult else None
    ultima = None if not u else {
        "boleto": u["boleto"], "fecha": _iso(u["concertacion"]),
        "dias": (fin - u["concertacion"]).days if u["concertacion"] else None,
        "operacion": u["operacion"], "operacion_label": _op_label(u["operacion"] or ""),
        "tipo_operacion": u["tipo_operacion"], "instrumento": u["instrumento"],
        "mercado": u["mercado"], "moneda": u["moneda"],
        "bruto": _f(u["bruto"]), "arancel": _f(u["arancel"]),
        "cantidad": _f(u["cantidad"]), "etapa": u["etapa"],
        "es_cierre": bool(u["es_cierre"]),
    }

    # ── 2) ARANCEL MES A MES (el gráfico de barras) ──────────────────────────
    por_mes = {r["mes"]: r for r in _q(
        f"SELECT to_char(date_trunc('month', o.concertacion), 'YYYY-MM') AS mes, "
        f"  COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS arancel, "
        f"  count(*) FILTER (WHERE {_VOL_WHERE}) AS n_boletos, "
        f"  COALESCE(SUM(CASE WHEN {_VOL_WHERE} THEN {_pesif('o', 'bruto')} END), 0) AS volumen "
        f"FROM operaciones o "
        f"WHERE o.id_cuenta = %(idc)s AND {_act_where('o')} "
        f"  AND o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s "
        f"GROUP BY 1", p)}
    serie = [{
        "mes": mes, "label": label, "fin": _iso(f),
        "arancel": _cv(_f((por_mes.get(mes) or {}).get("arancel")), factor),
        "volumen": _cv(_f((por_mes.get(mes) or {}).get("volumen")), factor),
        "n_boletos": int((por_mes.get(mes) or {}).get("n_boletos") or 0),
    } for mes, label, f in meses_lista]

    # ── 3) SHARE DEL VOLUMEN POR TIPO DE OPERACIÓN ───────────────────────────
    # El `%` se calcula ACÁ y no en el navegador: es el número que se va a reusar
    # en otras pantallas, y dos lugares que lo derivan terminan mostrando dos
    # porcentajes distintos del mismo cliente.
    # Cada tipo trae SUS DOS números con SU propio filtro. Si el arancel se contara
    # con el predicado del volumen, la caución mostraría arancel CERO —su arancel
    # vive solo en el cierre, que el volumen excluye— y el total de la tabla no
    # cerraría contra el del gráfico.
    filas = _q(
        f"SELECT o.operacion AS v, "
        f"  COALESCE(SUM(CASE WHEN {_VOL_WHERE} THEN {_pesif('o', 'bruto')} END), 0) AS volumen, "
        f"  count(*) FILTER (WHERE {_VOL_WHERE}) AS n_boletos, "
        f"  COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS arancel "
        f"FROM operaciones o "
        f"WHERE o.id_cuenta = %(idc)s AND {_act_where('o')} "
        f"  AND o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s "
        f"GROUP BY o.operacion ORDER BY 2 DESC", p)
    total_vol = sum(_f(r["volumen"]) for r in filas)
    share = [{
        "operacion": r["v"], "label": _op_label(r["v"] or "(sin tipo)"),
        "volumen": _cv(_f(r["volumen"]), factor),
        "pct": round(100 * _f(r["volumen"]) / total_vol, 2) if total_vol else 0.0,
        "n_boletos": int(r["n_boletos"]),
        "arancel": _cv(_f(r["arancel"]), factor),
        # Un tipo puede dejar arancel SIN volumen propio (todo su arancel en el
        # cierre). Se marca en vez de esconderlo: un 0% con plata al lado es un
        # dato, no un error.
        "solo_arancel": _f(r["volumen"]) == 0 and _f(r["arancel"]) > 0,
    } for r in filas
        # Un tipo que no dejó ni volumen ni arancel (p. ej. solo solicitudes) no
        # aporta nada a la lectura.
        if _f(r["volumen"]) > 0 or _f(r["arancel"]) > 0]

    total_ar = sum(s_["arancel"] for s_ in serie)
    n_meses = len(meses_lista)
    return {
        "id_cuenta": idc,
        "denominacion": cab.get("denominacion") or "—",
        "operador_nombre": cab.get("operador_nombre"),
        "nivel_1": cab.get("nivel_1"), "nivel_3": cab.get("nivel_3"),
        "fecha_alta_legajo": _iso(cab.get("fecha_alta_legajo")),
        "estado": cab.get("estado"),
        "moneda": "USD" if factor else "ARS",
        "desde": _iso(ini), "hasta": _iso(fin), "meses": n_meses,
        "ultima_op": ultima,
        "serie_aranceles": serie,
        "share_operacion": share,
        "totales": {
            "arancel": round(total_ar, 2),
            "arancel_por_mes": round(total_ar / n_meses, 2) if n_meses else 0.0,
            "volumen": _cv(total_vol, factor),
            "n_boletos": sum(s_["n_boletos"] for s_ in serie),
            "meses_operados": sum(1 for s_ in serie if s_["n_boletos"] > 0),
        },
        "fuentes": {
            "volumen": ("operaciones.operaciones — bruto pesificado al mep del boleto, "
                        "EXCLUYE los cierres (la apertura de la caución ya contó el volumen)"),
            "arancel": ("operaciones.operaciones — arancel > 0, etapa <> 'solicitud', "
                        "INCLUYE los cierres (el arancel de caución vive solo ahí)"),
        },
    }
