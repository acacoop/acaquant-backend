"""api/services/conoce_cliente_sql.py — tab CONOCÉ A TU CLIENTE (vista OPERADORES).

Una fila por CLIENTE, dentro de UN segmento. Contesta las tres preguntas que la
tab de aranceles no puede contestar sola:

    ¿quién deja la plata?          → ARANCEL 12M
    ¿quién NO rinde lo que podría? → ROA (arancel ÷ lo que tiene acá)
    ¿a quién le falta traer plata? → SOW (lo que tiene acá ÷ su cupo)

⚠️ **SIN SEGMENTO NO SE DIBUJA NADA, y no es un capricho.** El ROA de un FCI
institucional y el de una persona de retail no son comparables: el institucional
rinde menos bps por estructura, no por estar desaprovechado. Mezclados, la mitad
de abajo de la lista serían todos institucionales y la vista diría "exprimí a los
institucionales", que es la conclusión equivocada. Medido en la base real, las
medianas por segmento van de 22 a 58 bps y **el orden es al revés del intuitivo**:
PJ MEDIANA rinde 58 y PJ GRANDE 28. El tamaño no es la culpa del ROA bajo — por
eso hay que comparar adentro del segmento.

TODA COLUMNA DERIVADA SE PUEDE VERIFICAR CON LAS DOS DE AL LADO
==============================================================
Es la regla que ordena el resto del módulo:

    ROA = ARANCEL 12M ÷ TIENE          (las dos columnas están en pantalla)
    SOW = TIENE ÷ CUPO                 (las dos columnas están en pantalla)

Por eso **TIENE es el AuM PROMEDIO de los 12 meses**, no la foto de hoy. Si
mostrara la foto y dividiera por el promedio, alguien saca la calculadora, no le
da, y deja de creerle a la pantalla — con razón. El valor de hoy viaja igual
(`aum_hoy`, en el tooltip), pero la columna que se muestra es la que cierra la
cuenta.

Y el promedio no es un detalle: con la foto de hoy como divisor, el p90 del ROA
del libro daba **67.816 bps (678%)** — una cuenta que opera y barre la plata el
mismo día tiene un divisor cercano a cero y el cociente explota. No es un cliente
que rinde muchísimo: es una división rota.

EL PISO DEL ROA
===============
Debajo de `piso_aum` el ROA **no se calcula** (`null`, "—"). Un cliente que
opera sin dejar tenencia es un caso real —el operador barre la plata— y para él
"arancel ÷ casi cero" no significa nada. Mostrar 678% al lado de un 31% le pide
al que mira que adivine cuál de los dos es un número y cuál es un artefacto.

LO QUE SE REUSA Y POR QUÉ
=========================
- Universo, filtros y predicados: `comercial_sql` / `profundidad_sql`. Las tres
  tabs cuentan los mismos clientes o no cuenta ninguna.
- Arancel: `_arancel_where` (cierres INCLUIDOS — el arancel de caución vive solo
  en el cierre). Volumen no se usa acá.
- El ritmo (la marca ⚠ de la fila): `cuantitativo_sql.ritmo_por_cuenta`, la MISMA
  consulta que arma SE ESTÁN APAGANDO. Esa es la unificación de las dos tabs: la
  señal deja de ser una lista aparte y pasa a ser una marca en la fila, así
  "grande y apagándose" se ve en un renglón.
"""
from __future__ import annotations

from datetime import date, timedelta

from api.services._sql import _q
from api.services.comercial import _cv, _factor_usd, _hoy_art
from api.services.comercial_sql import _arancel_where, _f, _iso
from api.services.cuantitativo_sql import ritmo_por_cuenta
from api.services.profundidad_sql import _op_label, _scope

# El campo que ES el segmento. `nivel_3` es el patrimonial (derivado del cupo:
# PH RETAIL / PH ALTO PATRIMONIO / PJ GRANDE / …), que es justo el grupo que hace
# comparable al ROA — agrupa por tamaño de plata.
SEGMENTO_CAMPO = "nivel_3"

MESES = 12

# Debajo de este AuM promedio, el ROA no se calcula (ver el docstring).
PISO_AUM_DEF = 1_000_000
PISO_AUM_MAX = 10**12

LIMITE_DEF = 500
LIMITE_MAX = 5000

# Por qué columna se ordena. El default es TIENE: pone arriba de cada segmento a
# las cuentas más grandes, y las que tienen mucha plata con ARANCEL 0 quedan en
# la primera pantalla — medido, 18 cuentas concentran el 96% de la plata quieta
# del libro, y hoy no aparecen en ninguna lista porque justamente no operan.
ORDENES = ("aum", "arancel", "roa", "sow", "cupo", "denominacion")
ORDEN_DEF = "aum"


def _num(v, default: float, minimo: float, maximo: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if not (minimo <= x <= maximo) else x


def _meses_atras(fin: date, n: int) -> date:
    """El primero del mes `n-1` meses atrás (ventana de `n` meses contando el actual)."""
    a, m = fin.year, fin.month - (n - 1)
    while m <= 0:
        a, m = a - 1, m + 12
    return date(a, m, 1)


def _fines_de_mes(ini: date, fin: date) -> list[date]:
    """Los fines de mes de la ventana. Son las fechas de referencia del AuM."""
    out: list[date] = []
    a, m = ini.year, ini.month
    while (a, m) <= (fin.year, fin.month):
        na, nm = (a + 1, 1) if m == 12 else (a, m + 1)
        out.append(min(date(na, nm, 1) - timedelta(days=1), fin))
        a, m = na, nm
    return out


# ── El desplegable de segmentos ──────────────────────────────────────────────
def segmentos(**filtros) -> list[dict]:
    """Los segmentos que EXISTEN en el scope, con cuántas cuentas tiene cada uno.

    No se hardcodean: si mañana la mesa agrega un segmento aparece solo. Las
    cuentas sin segmento viajan como un valor propio y NO se esconden — son
    cuentas que nadie clasificó, y ocultarlas es cómo se pierden.
    """
    p: dict = {}
    w = _scope(p, alias="c", **filtros)
    rows = _q(
        f"SELECT COALESCE(c.{SEGMENTO_CAMPO}, '') AS v, count(*) AS n "
        f"FROM comitentes c WHERE {w} GROUP BY 1 ORDER BY count(*) DESC", p)
    return [{"valor": r["v"], "label": r["v"] or "(sin segmento)", "n": int(r["n"])}
            for r in rows]


def _mediana(xs: list[float]) -> float | None:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


# ── LO PÚBLICO ───────────────────────────────────────────────────────────────
def conoce_cliente(*, segmento: str | None = None, moneda: str = "ARS",
                   meses: int = MESES, piso_aum=None, limite: int = LIMITE_DEF,
                   orden: str = ORDEN_DEF, operador=None, nivel_1=None, nivel_2=None,
                   nivel_4=None, nivel_5=None, referido=None, division=None) -> dict:
    """Una fila por cliente del segmento. Sin `segmento`, devuelve solo la lista de
    segmentos para que la pantalla haga elegir — nunca todos en la misma bolsa."""
    filtros = dict(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2,
                   nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)
    opciones = segmentos(**filtros)
    if segmento is None or segmento == "__todos__":
        return {"segmento": None, "segmentos": opciones, "items": [], "n": 0,
                "aviso": "Elegí un segmento: el ROA solo se puede comparar entre "
                         "clientes parecidos."}

    fin = _hoy_art()
    ini = _meses_atras(fin, max(1, min(int(meses or MESES), 60)))
    piso = _num(piso_aum, PISO_AUM_DEF, 0, PISO_AUM_MAX)
    lim = max(1, min(int(limite or LIMITE_DEF), LIMITE_MAX))
    orden = orden if orden in ORDENES else ORDEN_DEF
    factor = _factor_usd(moneda)
    # El cupo se guarda en ARS y siempre se muestra en la moneda de la barra, igual
    # que el resto: si el cupo fuera en pesos y el AuM en dólares, el SOW sería un
    # número inventado. Como los dos usan el MISMO factor, el cociente no cambia.
    fac = factor

    # ── 1) El universo del segmento, con su ficha y su cupo ──────────────────
    p: dict = {"seg": segmento}
    w = _scope(p, alias="c", **filtros)
    seg_where = f"COALESCE(c.{SEGMENTO_CAMPO}, '') = %(seg)s"
    fichas = {r["id_cuenta"]: r for r in _q(
        f"SELECT c.id_cuenta, u.denominacion, c.nivel_1, c.{SEGMENTO_CAMPO} AS segmento, "
        f"       o.nombre AS operador_nombre, c.fecha_alta_legajo, "
        f"       c.cupo_transaccional_ars AS cupo "
        f"FROM comitentes c "
        f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
        f"LEFT JOIN operadores o ON o.email = c.operador_email "
        f"WHERE {w} AND {seg_where}", p)}
    if not fichas:
        return {"segmento": segmento, "segmentos": opciones, "items": [], "n": 0,
                "aviso": "No hay cuentas en este segmento con los filtros puestos."}

    ids = list(fichas)
    p2: dict = {"ids": ids, "ini": ini, "fin": fin}
    en_scope = "o.id_cuenta = ANY(%(ids)s)"

    # ── 2) Arancel de la ventana, abierto por tipo de operación ──────────────
    # Una sola pasada da las dos cosas: el total y la FAVORITA. La favorita es
    # por ARANCEL, no por volumen: por volumen medio libro diría "caución" —mueve
    # montos enormes y deja poco— y la columna no informaría nada.
    por_tipo: dict[str, dict[str, float]] = {}
    for r in _q(
        f"SELECT o.id_cuenta, o.operacion, "
        f"  COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS ar "
        f"FROM operaciones o "
        f"WHERE {en_scope} AND o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s "
        f"  AND o.anulado_en IS NULL "
        f"GROUP BY o.id_cuenta, o.operacion", p2):
        por_tipo.setdefault(r["id_cuenta"], {})[r["operacion"] or ""] = _f(r["ar"])

    # ── 3) AuM PROMEDIO de la ventana + la foto de hoy ───────────────────────
    # Las fotos se resuelven PRIMERO (una fecha real por cada fin de mes, la más
    # reciente <= ese día) y después se leen. Si dos meses caen en la misma foto
    # —porque no hubo snapshot nuevo— cuenta UNA sola vez: si no, ese mes pesaría
    # doble en el promedio.
    fines = _fines_de_mes(ini, fin)
    fotos = sorted({r["snap"] for r in _q(
        "SELECT DISTINCT s.snap FROM unnest(%(fines)s::date[]) AS r(fin) "
        "LEFT JOIN LATERAL (SELECT max(t.fecha) AS snap FROM tenencia t "
        "                   WHERE t.aum = 'si' AND t.fecha <= r.fin) s ON TRUE "
        "WHERE s.snap IS NOT NULL", {"fines": fines}) if r["snap"]})
    n_fotos = len(fotos)
    ultima_foto = fotos[-1] if fotos else None
    aum: dict[str, dict] = {}
    if fotos:
        aum = {r["id_cuenta"]: r for r in _q(
            "SELECT t.id_cuenta, SUM(t.valuacion) AS suma, "
            "  SUM(t.valuacion) FILTER (WHERE t.fecha = %(ult)s) AS hoy "
            "FROM tenencia t "
            "WHERE t.aum = 'si' AND t.fecha = ANY(%(fotos)s::date[]) "
            "  AND t.id_cuenta = ANY(%(ids)s) "
            "GROUP BY t.id_cuenta",
            {"fotos": fotos, "ids": ids, "ult": ultima_foto})}

    # ── 4) El ritmo — la MISMA consulta que SE ESTÁN APAGANDO ────────────────
    # Se acota por los ids que ya resolvimos, no por un scope rearmado: una
    # segunda copia del predicado del segmento es exactamente de donde salen las
    # dos pantallas que muestran cosas distintas del mismo cliente.
    ritmos = {r["id_cuenta"]: r for r in ritmo_por_cuenta(
        ini, fin, "c.id_cuenta = ANY(%(ids_r)s)", {"ids_r": ids})}

    # ── 5) La fila ───────────────────────────────────────────────────────────
    items = []
    for idc, f in fichas.items():
        tipos = por_tipo.get(idc, {})
        arancel = sum(tipos.values())
        fav = max(tipos.items(), key=lambda kv: kv[1], default=(None, 0.0))
        a = aum.get(idc) or {}
        # Ausente en una foto = CERO (no tenía nada), así que el divisor es la
        # cantidad de fotos de la ventana, no la cantidad de fotos donde aparece.
        prom = (_f(a.get("suma")) / n_fotos) if n_fotos else None
        hoy = _f(a.get("hoy")) if a.get("hoy") is not None else 0.0
        cupo = _f(f["cupo"]) if f["cupo"] is not None else None

        roa = round(10000 * arancel / prom, 1) if (prom and prom >= piso) else None
        sow = round(100 * prom / cupo, 1) if (cupo and cupo > 0 and prom is not None) else None

        r = ritmos.get(idc) or {}
        ritmo_dias = _f(r.get("ritmo")) if r.get("ritmo") is not None else None
        ult = r.get("ult")
        sin_operar = (fin - ult).days if ult else None
        # Misma regla que SE ESTÁN APAGANDO: más de 3× su propio ritmo.
        apagandose = bool(ritmo_dias and sin_operar and sin_operar > 3 * ritmo_dias)

        items.append({
            "id_cuenta": idc,
            "denominacion": f["denominacion"] or "—",
            "operador_nombre": f["operador_nombre"],
            "nivel_1": f["nivel_1"], "segmento": f["segmento"],
            "arancel": _cv(arancel, fac),
            "aum": _cv(prom, fac) if prom is not None else None,
            "aum_hoy": _cv(hoy, fac),
            "roa_bps": roa,
            # Tres motivos distintos, y ninguno es «cero»: no hay fotas para
            # mirar · las hay y el cliente no tenía nada · tenía tan poco que el
            # cociente no significa nada. Un solo texto para los tres haría que
            # el que audita la fila busque el problema donde no está.
            "roa_motivo": None if roa is not None else (
                "no hay ninguna foto de tenencia en la ventana" if prom is None else
                "la cuenta no tiene tenencia en ninguna foto de la ventana" if not prom
                else "AuM promedio por debajo del piso"),
            "cupo": _cv(cupo, fac) if cupo is not None else None,
            "sow_pct": sow,
            "operacion_fav": _op_label(fav[0]) if fav[0] and fav[1] > 0 else None,
            "operacion_fav_arancel": _cv(fav[1], fac) if fav[1] > 0 else None,
            "n_tipos": sum(1 for v in tipos.values() if v > 0),
            "ritmo_dias": round(ritmo_dias, 1) if ritmo_dias else None,
            "dias_sin_operar": sin_operar,
            "apagandose": apagandose,
            "ultima_op": _iso(ult),
        })

    # `None` va SIEMPRE al fondo, se ordene ascendente o descendente: un "no sé"
    # arriba de todo en el orden por ROA sería la primera fila que se mira.
    if orden == "denominacion":
        items.sort(key=lambda x: (x["denominacion"] or "").upper())
    else:
        k = _CLAVE[orden]
        items.sort(key=lambda x: (x[k] is None, -(x[k] or 0)))

    # ── 6) El contexto del segmento: sin esto, un ROA suelto no se puede juzgar
    roas = [i["roa_bps"] for i in items if i["roa_bps"] is not None]
    sows = [i["sow_pct"] for i in items if i["sow_pct"] is not None]
    contexto = {
        "n_clientes": len(items),
        "roa_mediana": _mediana(roas), "n_con_roa": len(roas),
        "sow_mediana": _mediana(sows), "n_con_cupo": len(sows),
        "aum_mediana": _mediana([i["aum"] for i in items if i["aum"] is not None]),
        "arancel_total": round(sum(i["arancel"] for i in items), 2),
        "n_sin_arancel": sum(1 for i in items if i["arancel"] <= 0 and (i["aum"] or 0) > 0),
        "n_apagandose": sum(1 for i in items if i["apagandose"]),
    }
    return {
        "segmento": segmento, "segmentos": opciones,
        "desde": _iso(ini), "hasta": _iso(fin), "meses": len(fines),
        "moneda": moneda, "piso_aum": _cv(piso, fac), "orden": orden,
        "n": len(items), "limite": lim, "items": items[:lim],
        "contexto": contexto,
        "foto_aum": {"ultima": _iso(ultima_foto), "n_fotos": n_fotos},
        "fuentes": {
            "arancel": "operaciones.operaciones — arancel > 0, etapa <> solicitud, "
                       "CIERRES INCLUIDOS (el arancel de caución vive solo en el cierre)",
            "aum": f"portafolio.tenencia (aum='si') — PROMEDIO de {n_fotos} fotos "
                   f"de fin de mes; ausente en una foto cuenta como cero",
            "cupo": "clientes.comitentes.cupo_transaccional_ars — carga manual por "
                    "Excel, sin fecha de carga registrada",
            "roa": "ARANCEL 12M ÷ TIENE, en bps (100 bps = 1%). Las dos columnas "
                   "están en pantalla: el número se puede verificar dividiendo",
            "sow": "TIENE ÷ CUPO. También verificable con las dos columnas de al lado",
        },
    }


_CLAVE = {"aum": "aum", "arancel": "arancel", "roa": "roa_bps", "sow": "sow_pct",
          "cupo": "cupo", "denominacion": "denominacion"}
