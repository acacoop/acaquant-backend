"""api/services/comisiones_fci.py — COMISIONES FCI (Back Office).

Qué cobra ACA Valores por la tenencia de fondos, día por día, sin planilla.

EL CÁLCULO, Y POR QUÉ ES ASÍ
============================

    arancel del día = valuación × fee_admin ÷ 2 ÷ 365

- **`fee_admin`** (`portafolio.assets`) es el honorario ANUAL COMPLETO de la
  sociedad gerente — lo que carga la mesa en Manager → TÍTULOS.
- **`÷ 2`** porque de ese honorario **la mitad es del mercado y la mitad nuestra**.
  Verificado contra el export del sistema contable de agosto 2026: en los fondos
  donde los dos lados tienen el dato, la relación es **exactamente 2,0** — no
  aproximadamente. Por eso se guarda el fee ENTERO (que es el que existe en el
  prospecto y el que alguien puede auditar) y la mitad se aplica ACÁ, en el
  cálculo. Guardar el fee ya dividido dejaría en la base un número que no coincide
  con ningún papel.
- **`÷ 365`** porque el honorario es anual y se devenga por día corrido.

TRAMOS: EL FIN DE SEMANA NO ES UN AGUJERO
=========================================

La comisión se devenga TODOS los días corridos, pero `portafolio.tenencia` es una
FOTO y solo hay foto los días hábiles. Si se sumaran nada más los días con foto, un
mes daría ~22 días en vez de 30 y **faltaría un cuarto de la plata**, sin que nada
avise.

Solución: cada foto **cubre** desde su fecha hasta el día anterior a la foto
siguiente. La del viernes cubre viernes, sábado y domingo (3 días). La última foto
antes del día 1 cubre el arranque del mes si ese día 1 cayó en fin de semana.

Se resuelve con una ventana (`LEAD`) sobre las fechas DISTINTAS del período —una
decena de filas— y no generando un calendario por cuenta y por fondo. La tabla se
recorre UNA vez.

LO QUE NO SE PUEDE CALCULAR, SE DECLARA
=======================================

Un fondo sin `fee_admin` cargado **no aporta cero: no se sabe**. Medido el
2026-09-16 contra el export: 28 fondos que el contable factura no tienen fee de
nuestro lado. Por eso cada respuesta trae `sin_fee`, con los fondos y cuánta
valuación quedó sin poder devengar. Mostrarlos en cero diría que no generan.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.cache import cached
from api.services._sql import _q
from core.cartera import FCI

# De cada honorario de gerente, la mitad es del mercado. Constante nombrada y no un
# `/2` suelto: es una regla de NEGOCIO, y el día que cambie tiene que haber UN lugar
# donde cambiarla (y este docstring al lado explicando por qué).
PARTE_ACA = 0.5
DIAS_ANIO = 365

# `cartera` convive con dos grafías ('FCI' y 'CARTERA FCI', ver core/cartera.py) y
# puede venir con espacios o en minúscula según quién cargó el asset.
_W_FCI = "upper(btrim(coalesce(t.cartera, ''))) = ANY(%(fci)s)"
_FCI_PARAMS = [x.upper() for x in FCI]

# Devengamiento diario de UNA fila de tenencia. NULL si el fondo no tiene fee — y
# ese NULL es información: se cuenta aparte en `sin_fee`, no se convierte en 0.
_ARANCEL_DIA = f"(t.valuacion * a.fee_admin * {PARTE_ACA} / {DIAS_ANIO})"


def _mes_rango(mes: str) -> tuple[date, date]:
    """'2026-08' → (2026-08-01, 2026-08-31)."""
    anio, m = (int(x) for x in mes.split("-"))
    ini = date(anio, m, 1)
    fin = date(anio + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return ini, fin


def _corte(ini: date, fin: date) -> date | None:
    """Última fecha CON foto FCI dentro del mes. Es el "hoy" del mes elegido: el
    mes en curso corta en la última foto disponible y un mes cerrado corta en su
    último día hábil. None = no hay foto en ese mes (mes sin datos, no mes en cero).
    """
    r = _q(f"SELECT max(t.fecha) AS f FROM portafolio.tenencia t "
           f"WHERE t.fecha >= %(ini)s AND t.fecha <= %(fin)s AND {_W_FCI}",
           {"ini": ini, "fin": fin, "fci": _FCI_PARAMS})
    return r[0]["f"] if r and r[0]["f"] else None


# ── TRAMOS ───────────────────────────────────────────────────────────────────
# `ancla` = fecha con foto. Cada una cubre [max(ancla, ini) … min(sig-1, fin)].
# Se arranca en la última foto ANTERIOR O IGUAL a `ini` para que los días del
# principio del mes queden cubiertos aunque el 1º haya caído en fin de semana.
_TRAMOS = f"""
WITH ini_ancla AS (
    SELECT coalesce(
        (SELECT max(t.fecha) FROM portafolio.tenencia t
          WHERE t.fecha <= %(ini)s AND {_W_FCI}),
        %(ini)s) AS f
),
anclas AS (
    SELECT fecha, LEAD(fecha) OVER (ORDER BY fecha) AS sig
    FROM (
        SELECT DISTINCT t.fecha FROM portafolio.tenencia t
        WHERE t.fecha >= (SELECT f FROM ini_ancla) AND t.fecha <= %(fin)s
          AND {_W_FCI}
    ) d
),
tramo AS (
    SELECT fecha,
           (LEAST(coalesce(sig - 1, %(fin)s), %(fin)s)
            - GREATEST(fecha, %(ini)s) + 1) AS n_dias
    FROM anclas
)
SELECT fecha, n_dias FROM tramo WHERE n_dias > 0
"""


def _tramos(ini: date, fin: date) -> dict[date, int]:
    """{fecha_de_foto: días corridos que cubre} dentro de [ini, fin]."""
    return {r["fecha"]: int(r["n_dias"]) for r in _q(
        _TRAMOS, {"ini": ini, "fin": fin, "fci": _FCI_PARAMS})}


def _sql_agregado(group_by: str, extra_where: str = "") -> str:
    """Σ arancel del período y del día de corte, agrupado por lo que se pida.

    El JOIN con `tramo` (una decena de filas) multiplica cada foto por los días que
    cubre: así el fin de semana entra sin generar un calendario por cuenta. El
    `LEFT JOIN` a `assets` es a propósito — un fondo sin asset no se pierde, sale
    con el fee en NULL y cae en `sin_fee`."""
    return f"""
    WITH tramo AS ({_TRAMOS})
    SELECT {group_by} AS clave,
           coalesce(max(t.moneda), 'ARS')                         AS moneda,
           SUM({_ARANCEL_DIA} * tr.n_dias)                        AS acum,
           SUM({_ARANCEL_DIA}) FILTER (WHERE t.fecha = %(corte)s)  AS dia,
           SUM(t.valuacion)    FILTER (WHERE t.fecha = %(corte)s)  AS val_corte,
           count(DISTINCT t.id_cuenta) FILTER (WHERE t.fecha = %(corte)s) AS ctas,
           max(a.fee_admin)                                       AS fee,
           max(a.emisor)                                          AS gerente,
           bool_or(a.fee_admin IS NULL)                           AS sin_fee
    FROM portafolio.tenencia t
    JOIN tramo tr ON tr.fecha = t.fecha
    LEFT JOIN portafolio.assets a ON a.unidad = t.unidad
    WHERE t.fecha >= %(ini)s AND t.fecha <= %(fin)s AND {_W_FCI}{extra_where}
    GROUP BY {group_by}
    """


def _f(x) -> float:
    return float(x or 0)


def _p(ini: date, fin: date, corte: date) -> dict:
    return {"ini": ini, "fin": fin, "corte": corte, "fci": _FCI_PARAMS}


def _vacio(mes: str) -> dict:
    return {"mes": mes, "corte": None, "fondos": [], "gerentes": [],
            "totales": {"ARS": {"dia": 0.0, "acum": 0.0},
                        "USD": {"dia": 0.0, "acum": 0.0}},
            "sin_fee": {"n": 0, "valuacion": 0.0, "fondos": []},
            "sin_datos": True}


def resumen_mes(mes: str) -> dict:
    """Vista COMISIONES FCI para un mes: tabla por fondo + por gerente + totales.

    Una sola pasada por `tenencia`: la tabla por fondo trae el gerente y la moneda,
    así el agregado por sociedad gerente y los totales ARS/USD se derivan en memoria
    en vez de repetir la query. Que salgan de las MISMAS filas es lo que garantiza
    que el total y la tabla no se puedan contradecir.
    """
    ini, fin = _mes_rango(mes)
    corte = _corte(ini, fin)
    if corte is None:
        return _vacio(mes)

    rows = _q(_sql_agregado("t.unidad"), _p(ini, fin, corte))
    fondos, gerentes, tot = [], {}, {
        "ARS": {"dia": 0.0, "acum": 0.0}, "USD": {"dia": 0.0, "acum": 0.0}}
    sf_fondos, sf_val = [], 0.0
    for r in rows:
        mon = (r["moneda"] or "ARS").upper()
        mon = mon if mon in tot else "ARS"
        fila = {
            "unidad": r["clave"],
            "gerente": r["gerente"] or "(sin gerente)",
            "moneda": mon,
            "fee_admin": _f(r["fee"]) if r["fee"] is not None else None,
            "arancel_dia": _f(r["dia"]),
            "arancel_acum": _f(r["acum"]),
            "valuacion": _f(r["val_corte"]),
            "cuentas": int(r["ctas"] or 0),
            # El fondo se muestra igual, pero con el fee en NULL y esta marca: es
            # "no se puede calcular", que no es lo mismo que "no generó".
            "sin_fee": bool(r["sin_fee"]),
        }
        fondos.append(fila)
        if fila["sin_fee"]:
            sf_fondos.append(fila["unidad"])
            sf_val += fila["valuacion"]
            continue
        tot[mon]["dia"] += fila["arancel_dia"]
        tot[mon]["acum"] += fila["arancel_acum"]
        g = gerentes.setdefault(
            fila["gerente"], {"gerente": fila["gerente"], "moneda": mon,
                              "arancel_dia": 0.0, "arancel_acum": 0.0, "fondos": 0})
        g["arancel_dia"] += fila["arancel_dia"]
        g["arancel_acum"] += fila["arancel_acum"]
        g["fondos"] += 1

    fondos.sort(key=lambda x: x["arancel_acum"], reverse=True)
    lista_g = sorted(gerentes.values(), key=lambda x: x["arancel_acum"], reverse=True)
    return {
        "mes": mes, "corte": corte.isoformat(),
        "dias_devengados": sum(_tramos(ini, fin).values()),
        "fondos": fondos, "gerentes": lista_g,
        "totales": {m: {k: round(v, 2) for k, v in d.items()} for m, d in tot.items()},
        "sin_fee": {"n": len(sf_fondos), "valuacion": round(sf_val, 2),
                    "fondos": sorted(sf_fondos)},
        "sin_datos": False,
    }


def detalle_fondo(mes: str, unidad: str) -> dict:
    """Las CUENTAS que tuvieron ese fondo en el mes — la trazabilidad de la fila.

    Mismo cálculo y mismos tramos que la tabla de arriba: el detalle no puede
    contradecir al total porque no hay dos fórmulas, hay una."""
    ini, fin = _mes_rango(mes)
    corte = _corte(ini, fin)
    if corte is None:
        return {"mes": mes, "unidad": unidad, "corte": None, "cuentas": [],
                "total_dia": 0.0, "total_acum": 0.0}
    rows = _q(_sql_agregado("t.id_cuenta", " AND t.unidad = %(u)s"),
              {**_p(ini, fin, corte), "u": unidad})
    nombres = {r["id_cuenta"]: r["cuenta"] for r in _q(
        "SELECT DISTINCT id_cuenta, cuenta FROM portafolio.tenencia "
        "WHERE unidad = %(u)s AND fecha >= %(ini)s AND fecha <= %(fin)s",
        {"u": unidad, "ini": ini, "fin": fin})}
    cuentas = [{
        "id_cuenta": r["clave"],
        "cuenta": nombres.get(r["clave"]) or r["clave"],
        "valuacion": _f(r["val_corte"]),
        "arancel_dia": _f(r["dia"]),
        "arancel_acum": _f(r["acum"]),
    } for r in rows]
    cuentas.sort(key=lambda x: x["arancel_acum"], reverse=True)
    return {
        "mes": mes, "unidad": unidad, "corte": corte.isoformat(),
        "moneda": (rows[0]["moneda"] if rows else "ARS"),
        "cuentas": cuentas,
        "total_dia": round(sum(c["arancel_dia"] for c in cuentas), 2),
        "total_acum": round(sum(c["arancel_acum"] for c in cuentas), 2),
    }


# ── SERIE HISTÓRICA MENSUAL ──────────────────────────────────────────────────
# Recorre toda la historia de tenencia FCI, así que va cacheada: cambia una vez por
# día (cuando entra la foto nueva) y la consulta el gráfico en cada apertura.
_SQL_SERIE = f"""
WITH anclas AS (
    SELECT fecha, LEAD(fecha) OVER (ORDER BY fecha) AS sig
    FROM (SELECT DISTINCT t.fecha FROM portafolio.tenencia t WHERE {_W_FCI}) d
),
tramo AS (
    -- El tramo se corta en fin de mes: una foto de un 31 no puede devengar días
    -- del mes siguiente, o el gráfico mensual mezclaría períodos.
    SELECT fecha,
                     (CASE
                            -- El mes en curso debe coincidir con el resumen: corta en la
                            -- última foto disponible y no proyecta días todavía no cerrados.
                                WHEN date_trunc('month', fecha) = date_trunc('month', CURRENT_DATE)
                                    AND sig IS NULL
                                THEN 1
                            ELSE LEAST(coalesce(sig - 1, fecha), (date_trunc('month', fecha)
                                     + interval '1 month - 1 day')::date) - fecha + 1
                        END) AS n_dias
    FROM anclas
)
SELECT to_char(t.fecha, 'YYYY-MM')        AS mes,
       coalesce(t.moneda, 'ARS')          AS moneda,
       SUM({_ARANCEL_DIA} * tr.n_dias)    AS acum
FROM portafolio.tenencia t
JOIN tramo tr ON tr.fecha = t.fecha AND tr.n_dias > 0
JOIN portafolio.assets a ON a.unidad = t.unidad AND a.fee_admin IS NOT NULL
WHERE {_W_FCI}
GROUP BY 1, 2 ORDER BY 1
"""


@cached(ttl=1800)
def serie_mensual() -> dict:
    """Acumulado por MES y por moneda, toda la historia. Para el gráfico de barras."""
    por_mes: dict[str, dict] = {}
    for r in _q(_SQL_SERIE, {"fci": _FCI_PARAMS}):
        mon = (r["moneda"] or "ARS").upper()
        m = por_mes.setdefault(r["mes"], {"mes": r["mes"], "ARS": 0.0, "USD": 0.0})
        m[mon if mon in ("ARS", "USD") else "ARS"] += _f(r["acum"])
    meses = [{"mes": k, "ARS": round(v["ARS"], 2), "USD": round(v["USD"], 2)}
             for k, v in sorted(por_mes.items())]
    return {"meses": meses}


@cached(ttl=1800)
def meses_disponibles() -> dict:
    """Meses con foto FCI — alimenta el selector. Que salga de los datos y no de un
    rango inventado evita ofrecer un mes que va a venir vacío."""
    rows = _q(f"SELECT DISTINCT to_char(t.fecha, 'YYYY-MM') AS mes "
              f"FROM portafolio.tenencia t WHERE {_W_FCI} ORDER BY 1 DESC",
              {"fci": _FCI_PARAMS})
    return {"meses": [r["mes"] for r in rows]}


@cached(ttl=600)
def fees_vigentes() -> dict:
    """Fee por fondo y por gerente — lo que muestra el modal de ayuda (`?`).

    Incluye los que NO tienen fee cargado: el modal que explica el cálculo es
    exactamente el lugar donde hay que ver qué fondos no se pueden calcular."""
    rows = _q(
        "SELECT a.unidad, a.emisor, a.fee_admin "
        "FROM portafolio.assets a "
        "WHERE upper(btrim(coalesce(a.cartera, ''))) = ANY(%(fci)s) "
        "ORDER BY coalesce(a.emisor, ''), a.unidad", {"fci": _FCI_PARAMS})
    fondos = [{
        "unidad": r["unidad"],
        "gerente": r["emisor"] or "(sin gerente)",
        # `fee_admin` es el honorario ENTERO; `fee_aca` es la mitad que cobramos.
        # Viajan los dos para que el modal pueda mostrar la cuenta completa y nadie
        # tenga que confiar en que el ÷2 se hizo.
        "fee_admin": _f(r["fee_admin"]) if r["fee_admin"] is not None else None,
        "fee_aca": (_f(r["fee_admin"]) * PARTE_ACA) if r["fee_admin"] is not None else None,
    } for r in rows]
    return {
        "fondos": fondos,
        "sin_fee": [f["unidad"] for f in fondos if f["fee_admin"] is None],
        "formula": {
            "texto": "arancel del día = valuación × fee_admin ÷ 2 ÷ 365",
            "parte_aca": PARTE_ACA,
            "dias_anio": DIAS_ANIO,
            "por_que_mitad": "El fee_admin es el honorario anual COMPLETO de la "
                             "sociedad gerente. La mitad es del mercado y la mitad "
                             "de ACA Valores.",
            "fines_de_semana": "La comisión se devenga todos los días corridos. "
                               "Como la tenencia es una foto de días hábiles, cada "
                               "foto cubre hasta el día anterior a la siguiente: "
                               "la del viernes devenga viernes, sábado y domingo.",
        },
    }
