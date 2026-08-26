"""api/services/cuantitativo_sql.py — tab ANÁLISIS CUANTITATIVO (vista OPERADORES).

Tres listas de llamadas, no un tablero. Cada fila es **un nombre, un motivo en
castellano y cuánta plata hay en juego**, y las tres van ordenadas por plata —
no por gravedad de la señal.

    QUIÉNES IMPORTAN   los que hacen el 80% del arancel
    SE ESTÁN APAGANDO  rompieron su propio ritmo
    PERDIERON AuM      hoy tienen mucho menos que hace N meses

Reglas que valen para las tres:

1. **Todos los cortes son parámetros, ninguno viene fijo.** El usuario los mueve
   en la pantalla y los contadores se mueven con él. Los defaults de `CORTES` son
   un punto de partida, no una verdad — que el número se pueda discutir es lo que
   hace que la lista se siga abriendo.
2. **Nada de puntajes.** El motivo viaja como dato crudo ("suele operar cada 4
   días, lleva 19") y no como un 0-100: un score esconde la razón, y la razón es
   lo que hace levantar el teléfono.
3. **El mismo cliente puede estar en las tres listas** y eso NO es repetición: es
   la misma historia desde tres ángulos. Por eso los contadores no se suman.
4. **El contexto viaja siempre** (`contexto`): "SE ESTÁN APAGANDO 5" no significa
   nada si no sabés que 17 clientes hacen el 80% de la facturación. Si tres de
   esos cinco son del núcleo es una urgencia; si son de la cola es ruido.
5. Universo y scope son **los mismos que PROFUNDIDAD** (`comitentes` activas del
   scope con `fecha_alta_legajo <= fin de mes`) y la actividad usa el **mismo
   predicado** (`comercial_sql._act_where`) — las dos tabs no pueden contradecirse.
"""
from __future__ import annotations

from datetime import date

from api.services import cashflow_sql as _cf_sql
from api.services._sql import _q
from api.services.comercial import _cv, _factor_usd
from api.services.comercial_sql import (
    _act_where,
    _arancel_where,
    _comitentes_where,
    _f,
    _fin_de_mes,
    _hoy_art,
    _iso,
)
from api.services.profundidad_sql import _label, _parse_mes

# Los cortes que el usuario mueve en la pantalla. Cada uno con su rango válido:
# el backend NO confía en lo que le mandan (un piso negativo o un múltiplo 0
# dejarían la lista sin sentido y no fallaría nada).
CORTES: dict[str, dict] = {
    # QUIÉNES IMPORTAN
    "pct_arancel":  {"def": 80,  "min": 1,  "max": 100, "que": "«deja mucho» = entra en este % del arancel del mes"},
    "meses_seguido": {"def": 8,  "min": 1,  "max": 12,  "que": "«viene seguido» = operó en al menos estos meses de los últimos 12"},
    # SE ESTÁN APAGANDO
    "multiplo":     {"def": 3,   "min": 2,  "max": 20,  "que": "avisar cuando lleva más de N veces su propio ritmo sin operar"},
    "min_dias_op":  {"def": 6,   "min": 3,  "max": 60,  "que": "días operados en 12 meses que hacen falta para tener ritmo medible"},
    # PERDIERON AuM
    "caida_pct":    {"def": 75,  "min": 10, "max": 99,  "que": "cayó más de este % contra la foto de referencia"},
    "meses_atras":  {"def": 3,   "min": 1,  "max": 24,  "que": "contra hace cuántos meses se compara"},
    "piso_aum":     {"def": 10_000_000, "min": 0, "max": 10**15,
                     "que": "solo cuentas que tenían más de este AuM (sin piso la lista se llena de ruido)"},
}

# Tope de filas por lista. Los CONTADORES se calculan sobre todas y recién después
# se capea: el tope no puede hacer que la solapa diga un número y la lista otro.
LIMITE_DEF = 300
LIMITE_MAX = 5000


def _corte(nombre: str, valor) -> float:
    """Un corte validado contra su rango. Fuera de rango vuelve al default en vez
    de generar una lista que no significa nada."""
    c = CORTES[nombre]
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return float(c["def"])
    return float(c["def"]) if not (c["min"] <= v <= c["max"]) else v


def _scope(p: dict, alias: str = "", **filtros) -> str:
    return _comitentes_where(
        filtros.get("operador"), p,
        nivel_1=filtros.get("nivel_1"), nivel_3=filtros.get("nivel_3"),
        referido=filtros.get("referido"), alias=alias,
        nivel_4=filtros.get("nivel_4"), nivel_5=filtros.get("nivel_5"),
        nivel_2=filtros.get("nivel_2"), division=filtros.get("division"))


def _menos_meses(d: date, n: int) -> date:
    """Fin de mes de `n` meses antes que el mes de `d`."""
    total = (d.year * 12 + d.month - 1) - n
    return _fin_de_mes(total // 12, total % 12 + 1)


def _snapshot(hasta: date) -> date | None:
    """Fecha de la foto de tenencia más reciente <= `hasta`. `None` = no hay foto,
    y entonces NO se puede opinar sobre el AuM de ese día (no vale 0)."""
    r = _q("SELECT max(fecha) AS f FROM tenencia WHERE aum = 'si' AND fecha <= %(h)s",
           {"h": hasta})
    return r[0]["f"] if r else None


def _aum_en(snap: date | None, ids_where: str, p: dict) -> dict[str, float]:
    """{id_cuenta: AuM} en la foto `snap`, scopeado. Vacío si no hay foto."""
    if snap is None:
        return {}
    pp = dict(p, snap_f=snap)
    return {r["id_cuenta"]: _f(r["aum"]) for r in _q(
        f"SELECT t.id_cuenta, SUM(t.valuacion) AS aum FROM tenencia t "
        f"JOIN comitentes c ON c.id_cuenta = t.id_cuenta AND {ids_where} "
        f"WHERE t.fecha = %(snap_f)s AND t.aum = 'si' GROUP BY t.id_cuenta", pp)}


def _ficha(fin: date, filtros: dict) -> dict[str, dict]:
    """Universo del mes: cuentas activas del scope con alta <= fin, con su ficha.
    MISMO universo que PROFUNDIDAD — si divergiera, las dos tabs contarían
    clientes distintos y ninguna fallaría."""
    p: dict = {"fin": fin}
    w = _scope(p, alias="c", **filtros)
    return {r["id_cuenta"]: r for r in _q(
        f"SELECT c.id_cuenta, u.denominacion, c.nivel_1, c.nivel_3, "
        f"       o.nombre AS operador_nombre, c.fecha_alta_legajo "
        f"FROM comitentes c "
        f"LEFT JOIN cuentas u ON u.id_cuenta = c.id_cuenta "
        f"LEFT JOIN operadores o ON o.email = c.operador_email "
        f"WHERE {w} AND c.fecha_alta_legajo <= %(fin)s", p)}


def _base(idc: str, fi: dict) -> dict:
    """Las columnas que toda fila de toda lista comparte."""
    f = fi.get(idc) or {}
    return {"id_cuenta": idc, "denominacion": f.get("denominacion") or "—",
            "operador_nombre": f.get("operador_nombre"),
            "nivel_1": f.get("nivel_1"), "nivel_3": f.get("nivel_3")}


def _mediana(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2


# ── El contexto que viaja SIEMPRE ────────────────────────────────────────────
def _contexto(aranceles: dict[str, float], n_clientes: int, factor) -> dict:
    """Las cuatro frases de arriba. La MEDIANA y el PROMEDIO van juntos a
    propósito: si el promedio es 8 veces el del medio, cualquiera entiende que hay
    una ballena adentro sin que haya que explicarle qué es una distribución
    sesgada. Y `cuantos_80` es el corte de Pareto: con cuántos clientes juntás el
    80% del arancel."""
    vals = sorted((v for v in aranceles.values() if v > 0), reverse=True)
    total = sum(vals)
    n = len(vals)
    acum, cuantos_80 = 0.0, 0
    for v in vals:
        acum += v
        cuantos_80 += 1
        if total and acum >= 0.8 * total:
            break
    top10 = sum(vals[:10])
    return {
        "n_clientes": n_clientes,
        "n_operaron": n,
        "arancel_total": _cv(total, factor),
        "mediana": _cv(_mediana(vals), factor),
        "promedio": _cv(total / n, factor) if n else 0.0,
        "top10_pct": round(100 * top10 / total, 1) if total else None,
        "cuantos_80": cuantos_80 if total else 0,
    }


def _arancel_del_mes(ini: date, fin: date, ids_where: str, p0: dict) -> dict[str, float]:
    """{id_cuenta: arancel del mes}. Solo cuentas que operaron (las que no, no
    entran en el dict — el universo lo pone `_ficha`)."""
    p = dict(p0, ini=ini, fin=fin)
    return {r["id_cuenta"]: _f(r["ar"]) for r in _q(
        f"SELECT o.id_cuenta, "
        f"  COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS ar "
        f"FROM operaciones o JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND {ids_where} "
        f"WHERE o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s AND {_act_where('o')} "
        f"GROUP BY o.id_cuenta", p)}


# ── LISTA 1 · QUIÉNES IMPORTAN ───────────────────────────────────────────────
def _quienes_importan(aranceles: dict[str, float], fin: date, ids_where: str, p0: dict,
                      pct_arancel: float, meses_seguido: float,
                      fi: dict, factor) -> tuple[list[dict], dict]:
    """El cuadrado de 4 + la lista. Dos preguntas y nada más: **cuánto deja**
    (¿entra en el X% del arancel?) y **cada cuánto aparece** (¿en cuántos de los
    últimos 12 meses operó?).

    La celda que importa es GRANDE IRREGULAR: mucha plata y poca regularidad.
    Cuando dejan de venir, su silencio parece normal y nadie lo nota en meses —
    con el estado comercial de hoy figuran "activa" o "enfriándose" según el día
    que mires, y nunca disparan nada.
    """
    # Meses distintos operados en los últimos 12 (incluido el actual).
    ini12 = _menos_meses(fin, 11).replace(day=1)
    p = dict(p0, i12=ini12, fin=fin)
    meses_op = {r["id_cuenta"]: int(r["n"]) for r in _q(
        f"SELECT o.id_cuenta, count(DISTINCT date_trunc('month', o.concertacion)) AS n "
        f"FROM operaciones o JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND {ids_where} "
        f"WHERE o.concertacion >= %(i12)s AND o.concertacion <= %(fin)s AND {_act_where('o')} "
        f"GROUP BY o.id_cuenta", p)}

    # El corte de "deja mucho": los que acumulan el X% del arancel, de mayor a menor.
    orden = sorted(((v, k) for k, v in aranceles.items() if v > 0), reverse=True)
    total = sum(v for v, _ in orden)
    deja_mucho: set[str] = set()
    acum = 0.0
    for v, k in orden:
        if total and acum >= (pct_arancel / 100) * total:
            break
        acum += v
        deja_mucho.add(k)

    cuadrante = {"nucleo": 0, "grande_irregular": 0, "habitual": 0, "ocasional": 0}
    filas = []
    for idc, ar in aranceles.items():
        if idc not in fi:                        # operó pero no está en el universo del mes
            continue
        seguido = meses_op.get(idc, 0) >= meses_seguido
        mucho = idc in deja_mucho
        tipo = ("nucleo" if (mucho and seguido) else "grande_irregular" if mucho
                else "habitual" if seguido else "ocasional")
        cuadrante[tipo] += 1
        if mucho:                                # la LISTA es solo la mitad de arriba
            filas.append({**_base(idc, fi), "tipo": tipo,
                          "meses_operados": meses_op.get(idc, 0), "meses_ventana": 12,
                          "arancel_mes": _cv(ar, factor),
                          "pct_arancel": round(100 * ar / total, 2) if total else 0.0})
    filas.sort(key=lambda r: r["arancel_mes"], reverse=True)
    return filas, cuadrante


# ── LISTA 2 · SE ESTÁN APAGANDO ──────────────────────────────────────────────
def _hasta(fin: date) -> date:
    """Hasta dónde se cuenta el tiempo: `fin`, pero **nunca una fecha futura**.

    En el mes en curso `fin` es el último día del mes —el 31— así que un cliente
    que operó anteayer figuraba "hace 20 días" y entraba a la lista por un tiempo
    que todavía no pasó. En un mes ya cerrado `fin` es pasado y manda él: la
    lista de julio tiene que decir lo que se veía el 31 de julio.
    """
    return min(fin, _hoy_art())


def _se_apagan(fin: date, ids_where: str, p0: dict, multiplo: float, min_dias: float,
               fi: dict, retiros: dict[str, float], factor) -> list[dict]:
    """Cada cliente tiene SU ritmo. Entra el que lleva más de `multiplo` veces lo
    que suele tardar.

    Le gana al umbral fijo 45/90 en los dos sentidos: al que opera cada 3 días lo
    ve un mes antes, y al que opera cada 40 no lo marca como enfriándose a los 46
    — y esa falsa alarma es lo que enseña a ignorar la lista.

    El ritmo son DÍAS OPERADOS, no boletos: cinco boletos el mismo día son una
    sola aparición. Sin `min_dias` días distintos no hay ritmo medible y la cuenta
    NO entra (una mediana sobre 2 datos es basura).
    """
    ini12 = _menos_meses(fin, 11).replace(day=1)
    p = dict(p0, i12=ini12, fin=fin)
    rows = _q(
        f"WITH dias AS ("
        f"  SELECT DISTINCT o.id_cuenta, o.concertacion AS d "
        f"  FROM operaciones o JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND {ids_where} "
        f"  WHERE o.concertacion >= %(i12)s AND o.concertacion <= %(fin)s AND {_act_where('o')}), "
        f"gaps AS (SELECT id_cuenta, d, "
        f"  d - lag(d) OVER (PARTITION BY id_cuenta ORDER BY d) AS gap FROM dias) "
        f"SELECT id_cuenta, count(*) AS n_dias, max(d) AS ult, "
        f"  percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS ritmo "
        f"FROM gaps GROUP BY id_cuenta", p)

    # Lo que deja por mes = arancel de los últimos 12 meses / 12. Un solo mes es
    # ruidoso justo en el cliente que dejó de operar (su mes malo daría ~0 y lo
    # mandaría al fondo de la lista, que se ordena por plata en juego).
    ar12 = {r["id_cuenta"]: _f(r["ar"]) for r in _q(
        f"SELECT o.id_cuenta, "
        f"  COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS ar "
        f"FROM operaciones o JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND {ids_where} "
        f"WHERE o.concertacion >= %(i12)s AND o.concertacion <= %(fin)s AND {_act_where('o')} "
        f"GROUP BY o.id_cuenta", p)}

    filas = []
    for r in rows:
        idc = r["id_cuenta"]
        if idc not in fi or r["ritmo"] is None:
            continue
        n_dias, ritmo, ult = int(r["n_dias"]), float(r["ritmo"]), r["ult"]
        if n_dias < min_dias or ritmo <= 0:
            continue
        dias_sin = (_hasta(fin) - ult).days
        if dias_sin <= multiplo * ritmo:
            continue
        ret = retiros.get(idc, 0.0)
        filas.append({
            **_base(idc, fi),
            "ritmo_dias": round(ritmo, 1), "dias_sin_operar": dias_sin,
            "veces_su_ritmo": round(dias_sin / ritmo, 1),
            "dias_operados_12m": n_dias,
            "ultima_op": _iso(ult),
            "deja_por_mes": _cv(ar12.get(idc, 0.0) / 12, factor),
            # La plata se va ANTES que el cliente: si además retiró, va en la fila.
            "retiro": _cv(-ret, factor) if ret < 0 else None,
        })
    filas.sort(key=lambda r: r["deja_por_mes"], reverse=True)
    return filas


# ── LISTA 3 · PERDIERON AuM ──────────────────────────────────────────────────
def _perdieron_aum(fin: date, ids_where: str, p0: dict, caida_pct: float,
                   meses_atras: int, piso: float, fi: dict,
                   retiros: dict[str, float], factor) -> tuple[list[dict], dict]:
    """Cuentas que hoy tienen mucho menos que en la foto de referencia.

    **El piso NO es un detalle**: sin él la lista se llena de cuentas que pasaron
    de $50.000 a $10.000 —una caída del 80% que no le importa a nadie— y deja de
    abrirse.

    Y lo que la hace servir es el cruce con la plata: la diferencia entre "se está
    yendo" y "fue el mercado" no es un modelo, es un hecho — ¿retiró o no retiró?
    El caso peor es el tercero: **cayó a cero sin retirar un peso**. Eso es una
    transferencia de títulos a otro agente, o sea la competencia, y hoy no aparece
    en ninguna pantalla porque no movió plata: movió papeles.
    """
    fin_antes = _menos_meses(fin, meses_atras)
    snap_hoy, snap_antes = _snapshot(fin), _snapshot(fin_antes)
    aum_hoy = _aum_en(snap_hoy, ids_where, p0)
    aum_antes = _aum_en(snap_antes, ids_where, p0)

    filas = []
    for idc, antes in aum_antes.items():
        if idc not in fi or antes < piso or antes <= 0:
            continue
        hoy = aum_hoy.get(idc, 0.0)
        caida = 100 * (1 - hoy / antes)
        if caida <= caida_pct:
            continue
        ret = retiros.get(idc, 0.0)
        retiro = -ret if ret < 0 else 0.0
        # Acá había una columna "QUÉ PASÓ" que rotulaba cada fila ("se está yendo",
        # "fue mercado", "se llevó los títulos"). Se sacó: eran INFERENCIAS mías
        # presentadas con el mismo aspecto que los hechos de al lado. Los cuatro
        # números —tenía, tiene, cuánto cayó y si retiró— dicen lo mismo y no
        # obligan a nadie a creerme.
        filas.append({
            **_base(idc, fi),
            "aum_antes": _cv(antes, factor), "aum_hoy": _cv(hoy, factor),
            "caida_pct": round(caida, 1),
            "retiro": _cv(retiro, factor) if retiro > 0 else None,
        })
    filas.sort(key=lambda r: (r["aum_antes"] - r["aum_hoy"]), reverse=True)

    # Cuánto bajó el LIBRO en el mismo período: sin esto no se puede distinguir un
    # evento de mercado de un evento de cliente.
    t_antes, t_hoy = sum(aum_antes.values()), sum(aum_hoy.values())
    libro = round(100 * (t_hoy / t_antes - 1), 1) if t_antes else None
    return filas, {
        "snapshot_hoy": _iso(snap_hoy), "snapshot_antes": _iso(snap_antes),
        "fin_antes": _iso(fin_antes), "libro_pct": libro,
        "aum_libro_antes": _cv(t_antes, factor), "aum_libro_hoy": _cv(t_hoy, factor),
    }


# ── LO PÚBLICO ───────────────────────────────────────────────────────────────
def analisis_cuantitativo(*, mes: str | None = None, moneda: str = "ARS",
                          limite: int = LIMITE_DEF, operador=None, nivel_1=None,
                          nivel_2=None, nivel_3=None, nivel_4=None, nivel_5=None,
                          referido=None, division=None, **cortes) -> dict:
    """Las tres listas + el contexto, en UNA respuesta.

    Una sola llamada y no tres: los contadores de la sub-nav (17 · 5 · 4) tienen
    que corresponder a las listas que se van a ver, y cambiar de solapa no puede
    salir a buscar de nuevo. Los cortes llegan por `cortes` y se validan contra
    `CORTES` — el backend no confía en lo que le mandan.
    """
    anio, m = _parse_mes(mes, _mes_actual())
    ini, fin = date(anio, m, 1), _fin_de_mes(anio, m)

    c = {k: _corte(k, cortes.get(k)) for k in CORTES}
    lim = max(1, min(int(limite or LIMITE_DEF), LIMITE_MAX))
    factor = _factor_usd(moneda)
    filtros = dict(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
                   nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)

    # El scope de clientes se arma UNA vez y lo comparten todas las queries.
    p0: dict = {}
    ids_where = _scope(p0, alias="c", **filtros)
    fi = _ficha(fin, filtros)

    aranceles = _arancel_del_mes(ini, fin, ids_where, p0)
    contexto = _contexto({k: v for k, v in aranceles.items() if k in fi}, len(fi), factor)

    # Retiros del período que mira PERDIERON AuM (y que se muestran también en
    # SE ESTÁN APAGANDO): la plata se va antes que el cliente.
    desde_flujo = _menos_meses(fin, int(c["meses_atras"]))
    retiros = _cf_sql.neto_por_cuenta(desde=_iso(desde_flujo), hasta=_iso(fin))

    importan, cuadrante = _quienes_importan(
        aranceles, fin, ids_where, p0, c["pct_arancel"], c["meses_seguido"], fi, factor)
    apagan = _se_apagan(fin, ids_where, p0, c["multiplo"], c["min_dias_op"], fi,
                        retiros, factor)
    perdieron, foto = _perdieron_aum(fin, ids_where, p0, c["caida_pct"],
                                     int(c["meses_atras"]), c["piso_aum"], fi,
                                     retiros, factor)

    def _lista(filas):
        # El contador va sobre TODAS; la lista se capea después.
        return {"n": len(filas), "items": filas[:lim], "limite": lim}

    return {
        "mes": f"{anio:04d}-{m:02d}", "label": _label(anio, m),
        "ini": _iso(ini), "fin": _iso(fin),
        "moneda": "USD" if factor else "ARS",
        "cortes": c,
        "cortes_def": {k: {"def": v["def"], "min": v["min"], "max": v["max"], "que": v["que"]}
                       for k, v in CORTES.items()},
        "contexto": contexto,
        "cuadrante": cuadrante,
        "quienes_importan": _lista(importan),
        "se_apagan": _lista(apagan),
        "perdieron_aum": _lista(perdieron),
        "foto_aum": foto,
        "avisos": _avisos(foto, fin, len(fi)),
    }


def _mes_actual() -> str:
    from api.services.comercial import _hoy_art
    h = _hoy_art()
    return f"{h.year:04d}-{h.month:02d}"


def _avisos(foto: dict, fin: date, n_clientes: int) -> list[str]:
    """Lo que las listas NO pueden saber, a la vista. Un cero que en realidad es
    "no pude mirar" es peor que un número feo."""
    out = []
    if not foto.get("snapshot_hoy") or not foto.get("snapshot_antes"):
        out.append("falta alguna de las dos fotos de tenencia: PERDIERON AuM no puede "
                   "compararse y sale vacía (no es que no haya caídas)")
    else:
        for k, cual in (("snapshot_hoy", "la de referencia de hoy"),
                        ("snapshot_antes", "la de hace unos meses")):
            f = foto[k]
            ref = fin if k == "snapshot_hoy" else date.fromisoformat(foto["fin_antes"])
            d = (ref - date.fromisoformat(f)).days
            if d:
                out.append(f"{cual} no cae en el último día del mes: es del {f} "
                           f"({d} días antes)")
    if not n_clientes:
        out.append("el scope elegido no tiene ninguna cuenta activa con alta <= fin de mes")
    out.append("los contadores de las tres solapas NO se suman entre sí: la misma cuenta "
               "puede estar en las tres, y ése es justo el cliente al que hay que llamar")
    return out
