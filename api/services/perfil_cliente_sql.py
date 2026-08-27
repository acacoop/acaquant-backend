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

import re
import unicodedata
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
from api.services.comercial_sql import portafolio_cliente as _portafolio_cliente
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


def _clave(v: str) -> str:
    """Normaliza un nombre de operación para poder emparejar el CIERRE con su
    APERTURA: sin acentos, sin mayúsculas, sin signos y sin la palabra «cierre».
    `"Caución tomadora cierre"` → `"caucion tomadora"`."""
    txt = unicodedata.normalize("NFD", str(v or ""))
    txt = "".join(c for c in txt if unicodedata.category(c) != "Mn").lower()
    txt = re.sub(r"[^a-z0-9]+", " ", txt).strip()
    return re.sub(r"\s*\bcierres?\b\s*", " ", txt).strip()


def _fusionar_cierres(filas: list[dict]) -> tuple[list[dict], list[dict]]:
    """Junta la fila del CIERRE con la de su APERTURA.

    El cierre de una caución **nunca tiene volumen** (la apertura ya lo contó) pero
    **se lleva TODO el arancel**. Dejarlos separados muestra una fila con 0% de
    volumen y toda la plata, y otra con todo el volumen y arancel cero: las dos
    mienten sobre el mismo negocio.

    Emparejar por el NOMBRE es lo que la REGLA #9 desaconseja, así que va con las
    mismas guardas que `core/pareo`:

      1. Solo se fusiona una fila cuyos boletos sean **TODOS de cierre** (si tiene
         volumen propio, es un negocio aparte y no se toca).
      2. La apertura tiene que **EXISTIR** en el mismo resultado. Si no está, la
         fila queda sola — «no pude emparejar» ≠ «lo invento».
      3. El destino no puede ser a su vez una fila de cierre.

    Devuelve (filas, fusiones) — las fusiones se reportan para poder auditarlas.
    """
    base = {}
    for f in filas:
        if not f["_solo_cierre"]:
            base.setdefault(_clave(f["v"]), f)
    out, fusiones = [], []
    for f in filas:
        destino = base.get(_clave(f["v"])) if f["_solo_cierre"] else None
        if destino is not None and destino is not f:
            destino["arancel"] += f["arancel"]
            destino["n_boletos_cierre"] = destino.get("n_boletos_cierre", 0) + f["n_todos"]
            fusiones.append({"de": f["v"], "a": destino["v"], "arancel": round(f["arancel"], 2)})
        else:
            out.append(f)
    return out, fusiones


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
    hoy = _hoy_art()
    fin = date.fromisoformat(hasta) if hasta else hoy
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
    # NI la búsqueda ni los días se acotan al período elegido:
    #   · sin tope hacia atrás, porque la última es la última aunque sea de hace
    #     dos años (acotarla haría que un cliente dormido muestre "—");
    #   · sin tope hacia adelante, y los días **se cuentan contra HOY**. Contra el
    #     fin del mes elegido, un mes en curso da días de MÁS (el 31 todavía no
    #     llegó) y uno viejo da un número que no es "hace cuánto" sino "cuánto
    #     había pasado en ese momento" — que no es lo que nadie lee ahí.
    ult = _q(f"SELECT {_COLS_ULT} FROM operaciones o "
             f"WHERE o.id_cuenta = %(idc)s AND {_act_where('o')} "
             f"ORDER BY o.concertacion DESC NULLS LAST, o.boleto DESC LIMIT 1",
             {"idc": idc})
    u = ult[0] if ult else None
    ultima = None if not u else {
        "boleto": u["boleto"], "fecha": _iso(u["concertacion"]),
        "dias": (hoy - u["concertacion"]).days if u["concertacion"] else None,
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

    # ── 2b) EL AuM DEL CLIENTE, MES A MES ────────────────────────────────────
    # Para cada fin de mes, la foto de tenencia más reciente <= ese día, y el AuM
    # del cliente EN esa foto.
    # ⚠️ **Un mes sin foto vale `null`, no 0.** Que el cliente no tenga filas en una
    # foto que SÍ existe sí es cero (no tenía nada); que no haya foto es "no pude
    # mirar", y dibujarlo como cero sería inventar una caída a cero.
    fines = [f for _, _, f in meses_lista]
    aum_por_fin = {r["fin"]: r for r in _q(
        "WITH meses AS (SELECT * FROM unnest(%(fines)s::date[]) AS t(fin)), "
        "snaps AS (SELECT m.fin, s.snap FROM meses m LEFT JOIN LATERAL ("
        "  SELECT max(t.fecha) AS snap FROM tenencia t "
        "  WHERE t.aum = 'si' AND t.fecha <= m.fin) s ON TRUE) "
        "SELECT s.fin, s.snap, COALESCE(SUM(t.valuacion), 0) AS aum "
        "FROM snaps s LEFT JOIN tenencia t "
        "  ON t.fecha = s.snap AND t.aum = 'si' AND t.id_cuenta = %(idc)s "
        "GROUP BY s.fin, s.snap", {"fines": fines, "idc": idc})}
    serie_aum = [{
        "mes": mes, "label": label,
        "aum": (_cv(_f((aum_por_fin.get(f) or {}).get("aum")), factor)
                if (aum_por_fin.get(f) or {}).get("snap") is not None else None),
        "foto": _iso((aum_por_fin.get(f) or {}).get("snap")),
    } for mes, label, f in meses_lista]

    # ── 2c) LA TENENCIA DE HOY ───────────────────────────────────────────────
    # Se reusa `comercial_sql.portafolio_cliente` en vez de repetir la query: es la
    # MISMA tenencia que muestra Portfolio & Operaciones, así las dos pantallas no
    # pueden mostrar carteras distintas del mismo cliente.
    tenencia = _portafolio_cliente(id_cuenta=idc)

    # ── 3) SHARE DEL VOLUMEN POR TIPO DE OPERACIÓN ───────────────────────────
    # El `%` se calcula ACÁ y no en el navegador: es el número que se va a reusar
    # en otras pantallas, y dos lugares que lo derivan terminan mostrando dos
    # porcentajes distintos del mismo cliente.
    # Cada tipo trae SUS DOS números con SU propio filtro. Si el arancel se contara
    # con el predicado del volumen, la caución mostraría arancel CERO —su arancel
    # vive solo en el cierre, que el volumen excluye— y el total de la tabla no
    # cerraría contra el del gráfico.
    crudas = _q(
        f"SELECT o.operacion AS v, "
        f"  COALESCE(SUM(CASE WHEN {_VOL_WHERE} THEN {_pesif('o', 'bruto')} END), 0) AS volumen, "
        f"  count(*) FILTER (WHERE {_VOL_WHERE}) AS n_boletos, "
        f"  count(*) AS n_todos, "
        f"  count(*) FILTER (WHERE COALESCE(o.es_cierre, false)) AS n_cierre, "
        f"  COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS arancel "
        f"FROM operaciones o "
        f"WHERE o.id_cuenta = %(idc)s AND {_act_where('o')} "
        f"  AND o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s "
        f"GROUP BY o.operacion", p)
    filas = [{
        "v": r["v"], "volumen": _f(r["volumen"]), "arancel": _f(r["arancel"]),
        "n_boletos": int(r["n_boletos"]), "n_todos": int(r["n_todos"]),
        # Una fila es "solo cierre" cuando TODOS sus boletos lo son: ésa es la que
        # se puede fusionar con su apertura sin perder un negocio propio.
        "_solo_cierre": int(r["n_cierre"]) == int(r["n_todos"]) and int(r["n_todos"]) > 0,
    } for r in crudas
        if _f(r["volumen"]) > 0 or _f(r["arancel"]) > 0]
    filas, fusiones = _fusionar_cierres(filas)
    total_vol = sum(f["volumen"] for f in filas)
    filas.sort(key=lambda f: f["volumen"], reverse=True)
    share = [{
        "operacion": f["v"], "label": _op_label(f["v"]) or "(sin tipo)",
        "volumen": _cv(f["volumen"], factor),
        "pct": round(100 * f["volumen"] / total_vol, 2) if total_vol else 0.0,
        "n_boletos": f["n_boletos"],
        "n_boletos_cierre": f.get("n_boletos_cierre", 0),
        "arancel": _cv(f["arancel"], factor),
        # Quedó sin volumen y con plata = no se pudo emparejar con una apertura.
        # Se muestra igual, marcada: un 0% con arancel al lado es un dato.
        "solo_arancel": f["volumen"] == 0 and f["arancel"] > 0,
    } for f in filas]

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
        "hoy": _iso(hoy),
        "ultima_op": ultima,
        "serie_aranceles": serie,
        "serie_aum": serie_aum,
        "tenencia": tenencia,
        "share_operacion": share,
        # Qué cierres se juntaron con qué apertura: la fusión se puede auditar en
        # vez de tener que creerle a la pantalla.
        "fusiones": fusiones,
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
