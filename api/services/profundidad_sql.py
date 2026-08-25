"""api/services/profundidad_sql.py — tab PROFUNDIDAD DE CLIENTES (vista OPERADORES).

Una fila por MES (`jul-25`, `ago-25`, …). Contesta "cuánta base tengo, cuánta está
viva y cuánto deja" a lo largo del ejercicio, sin depender del Desde/Hasta de la
barra (esta tab lo IGNORA a propósito: su eje ES el tiempo).

Reglas del cálculo, todas en un solo lugar:

1. **TODO se mide al ÚLTIMO DÍA DEL MES** (jul-25 → 31/07/2025), nunca al primero.
   Los flujos (aranceles, actividad) son el mes COMPLETO `[01, fin]`.
2. **CLIENTES** = cuentas `estado='Activa'` del scope con `fecha_alta_legajo <= fin`
   — mismo universo que `informe_cuentas_por_segmento`. Las cuentas SIN fecha de alta
   no entran (no se puede saber si existían), pero se CUENTAN en `meta.sin_alta`:
   un universo que se achica en silencio es peor que uno chico.
   ⚠️ `estado` no es histórico: la base de un mes viejo se reconstruye con las
   cuentas que HOY están activas. Va dicho en `meta.advertencias`, no escondido.
3. **CON AuM** = cuentas con valuación > 0 en el snapshot de `portafolio.tenencia`
   más reciente <= fin de mes (`aum='si'`). Si no hay ningún snapshot <= fin, la
   celda vale **null (—), no 0**: "no pude mirar" ≠ "no había nada".
4. **ACTIVOS** = cuentas con al menos un boleto en `[01, fin]` bajo el MISMO
   predicado que ESTADO COMERCIAL (`comercial_sql._act_where` = `anulado_en IS NULL`,
   cualquier boleto, sin filtro de tipo/mercado/etapa) → las dos tabs no pueden
   contradecirse.
5. **ARANCELES** = `comercial_sql._arancel_where()` (arancel > 0, etapa <> solicitud,
   cierres INCLUIDOS: el arancel de caución vive solo en el cierre). El arancel se
   guarda SIEMPRE en ARS; en vista USD se divide por el MEP del ÚLTIMO DÍA DE ESE MES
   (no el de hoy) para que el histórico no se mueva solo.

Nada se deriva en el navegador: los ratios, los labels (`jul-25`) y los totales
salen de acá.

Traza: cada celda se puede abrir (`detalle_mes`) y ver las cuentas que la componen,
recalculadas con LAS MISMAS fuentes y predicados — mismo patrón que el modal de
DÍAS SIN OPERAR y que el detalle por celda de Tesorería.
"""
from __future__ import annotations

from datetime import date

from api.services._sql import _q
from api.services.comercial import _cv, _hoy_art
from api.services.comercial_sql import (
    _act_where,
    _arancel_where,
    _comitentes_where,
    _f,
    _fin_de_mes,
    _iso,
)

# Primer mes de la tabla (ejercicio en curso). El ejercicio de la mesa arranca en
# JULIO; el pedido fue explícito: "arrancamos con el ejercicio actual, es decir la
# primera es jul-25". Es un default, no un límite: el endpoint acepta `desde`/`hasta`
# en formato YYYY-MM.
PROFUNDIDAD_INICIO = "2025-07"

# Tope de meses por request (≈ 8 años). Evita que un `desde` mal tipeado dispare un
# scan de toda la historia de operaciones.
MAX_MESES = 96

_MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic")

# Métricas auditables (columnas de la tabla). El valor es lo que el modal usa para
# decidir QUÉ cuentas listar y por qué columna ordenarlas.
METRICAS = ("clientes", "con_aum", "sin_aum", "activos", "ratio_actividad",
            "aranceles", "arancel_por_activo", "aum")


def _label(anio: int, mes: int) -> str:
    """`(2025, 7)` → `jul-25`. El label lo arma el backend: si lo derivara el front,
    dos pantallas podrían nombrar distinto el mismo mes."""
    return f"{_MESES_CORTOS[mes - 1]}-{anio % 100:02d}"


def _parse_mes(v: str | None, default: str) -> tuple[int, int]:
    """'YYYY-MM' → (anio, mes). Acepta también 'YYYY-MM-DD' (se queda con el mes)."""
    s = (v or default).strip()
    try:
        anio, mes = int(s[:4]), int(s[5:7])
        if not (1 <= mes <= 12) or anio < 1900:
            raise ValueError
    except (ValueError, IndexError):
        anio, mes = int(default[:4]), int(default[5:7])
    return anio, mes


def _meses(desde: str | None, hasta: str | None) -> list[dict]:
    """Lista de meses [desde, hasta], ascendente. `hasta` nunca supera el mes en
    curso (no se dibujan filas de meses que todavía no existen)."""
    hoy = _hoy_art()
    a0, m0 = _parse_mes(desde, PROFUNDIDAD_INICIO)
    a1, m1 = _parse_mes(hasta, f"{hoy.year:04d}-{hoy.month:02d}")
    # Techo = mes en curso.
    if (a1, m1) > (hoy.year, hoy.month):
        a1, m1 = hoy.year, hoy.month
    if (a0, m0) > (a1, m1):
        a0, m0 = a1, m1
    out: list[dict] = []
    a, m = a0, m0
    while (a, m) <= (a1, m1) and len(out) < MAX_MESES:
        fin = _fin_de_mes(a, m)
        out.append({
            "mes": f"{a:04d}-{m:02d}",
            "label": _label(a, m),
            "ini": date(a, m, 1),
            "fin": fin,
            # El mes en curso está INCOMPLETO: los flujos van hasta hoy, no hasta
            # fin de mes. Decirlo evita leer una caída que es solo "todavía no pasó".
            "en_curso": (a, m) == (hoy.year, hoy.month),
        })
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return out


def _scope(p: dict, alias: str = "", **filtros) -> str:
    """WHERE de `comitentes` con los filtros madre de la barra (operador + nivel_1..5
    + referido + division). Un solo predicado para las 3 queries y para el modal."""
    return _comitentes_where(
        filtros.get("operador"), p,
        nivel_1=filtros.get("nivel_1"), nivel_3=filtros.get("nivel_3"),
        referido=filtros.get("referido"), alias=alias,
        nivel_4=filtros.get("nivel_4"), nivel_5=filtros.get("nivel_5"),
        nivel_2=filtros.get("nivel_2"), division=filtros.get("division"))


def _mep_de(fecha: date, cache: dict[str, float | None]) -> float | None:
    """MEP del día `fecha` (último con timestamp <= fin de ese día). Se usa el MEP
    DEL MES, no el de hoy: dolarizar un jul-25 al MEP de hoy haría que el histórico
    cambie todos los días sin que haya pasado nada."""
    k = fecha.isoformat()
    if k not in cache:
        from api.services._mep import get_mep_for_date
        cache[k] = get_mep_for_date(k)
    return cache[k]


# ── TABLA ────────────────────────────────────────────────────────────────────
def profundidad_clientes(*, moneda: str = "ARS", desde: str | None = None,
                         hasta: str | None = None, operador=None, nivel_1=None,
                         nivel_2=None, nivel_3=None, nivel_4=None, nivel_5=None,
                         referido=None, division=None) -> dict:
    """Filas mm-aa con: clientes · con AuM · sin AuM · activos · ratio · aranceles ·
    arancel/activo · AuM. Tres queries agregadas para TODA la tabla (una por fuente),
    no una por mes."""
    meses = _meses(desde, hasta)
    if not meses:
        return {"moneda": moneda, "meses": [], "filas": [], "meta": {}}
    filtros = dict(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
                   nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)
    inis = [m["ini"] for m in meses]
    fines = [m["fin"] for m in meses]
    usd = (moneda or "ARS").upper() == "USD"
    mep_cache: dict[str, float | None] = {}

    # ── 1) UNIVERSO: una fila por fecha de alta; el acumulado por mes se arma acá.
    # Es la query más chica de las tres (≈ un par de cientos de fechas distintas) y
    # evita repetir el mismo COUNT una vez por mes.
    p1: dict = {}
    w1 = _scope(p1, **filtros)
    altas = _q(f"SELECT fecha_alta_legajo AS f, count(*) AS n FROM comitentes "
               f"WHERE {w1} GROUP BY 1", p1)
    sin_alta = sum(int(r["n"]) for r in altas if r["f"] is None)
    con_alta = sorted((r["f"], int(r["n"])) for r in altas if r["f"] is not None)
    clientes_por_mes: dict[date, int] = {}
    acum, i = 0, 0
    for fin in fines:                       # fines viene ordenado ascendente
        while i < len(con_alta) and con_alta[i][0] <= fin:
            acum += con_alta[i][1]
            i += 1
        clientes_por_mes[fin] = acum

    # ── 2) ACTIVIDAD + ARANCELES: UN scan de `operaciones` para todos los meses.
    # El JOIN contra la CTE de meses es un rango por mes sobre `ix_ops_concertacion`;
    # los meses son disjuntos, así que cada boleto entra en uno solo.
    # `_fuera` = boletos de cuentas del scope que NO estaban en el universo del mes
    # (sin fecha de alta, o alta posterior): NO se suman, pero se reportan — una
    # diferencia que no se ve es la que se descubre tarde y mirando una pantalla.
    # Se agrega en DOS pasos (por cuenta, después por mes) a propósito: un
    # `count(DISTINCT id_cuenta)` obliga a Postgres a ORDENAR todos los boletos del
    # período (medido: 210k filas → sort en disco). Agrupando primero por
    # (mes, cuenta) el paso caro pasa a ser un HashAggregate de meses × cuentas.
    # `alta` entra en el GROUP BY porque depende solo de la cuenta — no agrega grupos.
    p2: dict = {"inis": inis, "fines": fines}
    w2 = _scope(p2, alias="c", **filtros)
    dentro = "alta IS NOT NULL AND alta <= fin"
    ops = {r["fin"]: r for r in _q(
        f"WITH meses AS (SELECT * FROM unnest(%(inis)s::date[], %(fines)s::date[]) AS t(ini, fin)), "
        f"esc AS (SELECT c.id_cuenta, c.fecha_alta_legajo FROM comitentes c WHERE {w2}), "
        f"por_cuenta AS ("
        f"  SELECT m.fin, o.id_cuenta, e.fecha_alta_legajo AS alta, "
        f"    COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS arancel "
        f"  FROM meses m "
        f"  JOIN operaciones o ON o.concertacion >= m.ini AND o.concertacion <= m.fin "
        f"    AND {_act_where('o')} "
        f"  JOIN esc e ON e.id_cuenta = o.id_cuenta "
        f"  GROUP BY m.fin, o.id_cuenta, e.fecha_alta_legajo) "
        f"SELECT fin, "
        f"  count(*) FILTER (WHERE {dentro}) AS activos, "
        f"  count(*) FILTER (WHERE NOT ({dentro})) AS activos_fuera, "
        f"  COALESCE(SUM(arancel) FILTER (WHERE {dentro}), 0) AS aranceles, "
        f"  COALESCE(SUM(arancel) FILTER (WHERE NOT ({dentro})), 0) AS aranceles_fuera "
        f"FROM por_cuenta GROUP BY fin", p2)}

    # ── 3) AuM: para cada mes, el snapshot de tenencia más reciente <= fin de mes
    # (LATERAL, un max() por índice) y sobre ESE día el AuM por cuenta.
    p3: dict = {"inis": inis, "fines": fines}
    w3 = _scope(p3, alias="c", **filtros)
    aum = {r["fin"]: r for r in _q(
        f"WITH meses AS (SELECT * FROM unnest(%(inis)s::date[], %(fines)s::date[]) AS t(ini, fin)), "
        f"snaps AS (SELECT m.fin, s.snap FROM meses m "
        f"  LEFT JOIN LATERAL (SELECT max(t.fecha) AS snap FROM tenencia t "
        f"                     WHERE t.aum = 'si' AND t.fecha <= m.fin) s ON TRUE), "
        f"esc AS (SELECT c.id_cuenta, c.fecha_alta_legajo FROM comitentes c WHERE {w3}), "
        f"por_cuenta AS (SELECT s.fin, t.id_cuenta, SUM(t.valuacion) AS aum "
        f"  FROM snaps s JOIN tenencia t ON t.fecha = s.snap AND t.aum = 'si' "
        f"  JOIN esc e ON e.id_cuenta = t.id_cuenta AND e.fecha_alta_legajo <= s.fin "
        f"  GROUP BY s.fin, t.id_cuenta) "
        f"SELECT s.fin, s.snap, "
        f"  count(pc.id_cuenta) FILTER (WHERE pc.aum > 0) AS con_aum, "
        f"  COALESCE(SUM(pc.aum), 0) AS aum "
        f"FROM snaps s LEFT JOIN por_cuenta pc ON pc.fin = s.fin "
        f"GROUP BY s.fin, s.snap", p3)}

    filas = []
    for m in meses:
        fin = m["fin"]
        o = ops.get(fin) or {}
        a = aum.get(fin) or {}
        snap = a.get("snap")
        n_cli = clientes_por_mes.get(fin, 0)
        activos = int(o.get("activos") or 0)
        # USD: al MEP del último día DE ESE MES (o del snapshot, para el AuM).
        f_ar = _mep_de(fin, mep_cache) if usd else None
        f_aum = (_mep_de(snap, mep_cache) if usd and snap else f_ar)
        aranceles = _cv(_f(o.get("aranceles")), f_ar) if (not usd or f_ar) else None
        # Sin snapshot no se puede mirar el AuM → null, NO cero.
        con_aum = int(a["con_aum"]) if snap is not None else None
        aum_total = (_cv(_f(a.get("aum")), f_aum) if (snap is not None and (not usd or f_aum))
                     else None)
        filas.append({
            "mes": m["mes"], "label": m["label"],
            "ini": _iso(m["ini"]), "fin": _iso(fin), "en_curso": m["en_curso"],
            "clientes": n_cli,
            "con_aum": con_aum,
            "sin_aum": (n_cli - con_aum) if con_aum is not None else None,
            "activos": activos,
            "ratio_actividad": round(activos / n_cli, 4) if n_cli else None,
            "aranceles": aranceles,
            "arancel_por_activo": (round(aranceles / activos, 2)
                                   if aranceles is not None and activos else None),
            "aum": aum_total,
            # Traza de la foto de AuM: qué día se leyó y cuánto se aleja del fin de mes.
            "aum_snapshot": _iso(snap),
            "aum_desfasaje_dias": (fin - snap).days if snap is not None else None,
            # DOS MEP a la vista y no uno: el arancel se dolariza al del último día
            # del mes y el AuM al del día del snapshot, que puede ser otro. Mostrar
            # uno solo haría que el número no cierre contra la cotización que se ve.
            "mep_aranceles": f_ar, "mep_aum": f_aum,
            # Lo que quedó AFUERA del universo del mes (no suma; se muestra como aviso).
            "fuera_universo": {"activos": int(o.get("activos_fuera") or 0),
                               "aranceles": round(_f(o.get("aranceles_fuera")), 2)},
        })

    advertencias = []
    if sin_alta:
        advertencias.append(
            f"{sin_alta} cuenta(s) del scope no tienen fecha de alta de legajo → no entran "
            f"en el universo de ningún mes")
    advertencias.append(
        "el estado 'Activa' de la cuenta no es histórico: la base de un mes viejo se "
        "reconstruye con las cuentas que HOY están activas")
    if any(f["aum_desfasaje_dias"] not in (None, 0) for f in filas):
        advertencias.append(
            "hay meses cuya foto de AuM no cae exactamente en el último día del mes "
            "(se usa el snapshot de tenencia más reciente <= fin de mes; ver la columna AuM)")
    return {
        "moneda": "USD" if usd else "ARS",
        "desde": meses[0]["mes"], "hasta": meses[-1]["mes"],
        "filas": filas,
        "meta": {
            "sin_alta": sin_alta,
            "advertencias": advertencias,
            "fuentes": {
                "clientes": "clientes.comitentes (estado='Activa', fecha_alta_legajo <= fin de mes)",
                "aum": "portafolio.tenencia (aum='si'), snapshot más reciente <= fin de mes",
                "activos": "operaciones.operaciones — cualquier boleto no anulado en el mes",
                "aranceles": ("operaciones.operaciones — arancel > 0, etapa <> 'solicitud', "
                              "cierres incluidos (la caución cobra en el cierre); se guarda en ARS"),
            },
        },
    }


# ── MODAL: de dónde sale una celda ───────────────────────────────────────────
# Mismo principio que el modal de DÍAS SIN OPERAR: el número se abre y muestra las
# cuentas que lo forman. NO se recalcula con otro criterio — se usan los MISMOS
# predicados y el MISMO snapshot que la tabla, para una sola fila de meses.

# Qué cuentas lista cada métrica, y por qué columna se ordenan.
_FILTRO_METRICA = {
    "clientes":           (lambda r: True,                     "aum"),
    "con_aum":            (lambda r: (r["aum"] or 0) > 0,      "aum"),
    "sin_aum":            (lambda r: (r["aum"] or 0) <= 0,     "arancel"),
    "activos":            (lambda r: r["n_boletos"] > 0,       "arancel"),
    "ratio_actividad":    (lambda r: r["n_boletos"] > 0,       "arancel"),
    "aranceles":          (lambda r: (r["arancel"] or 0) > 0,  "arancel"),
    "arancel_por_activo": (lambda r: (r["arancel"] or 0) > 0,  "arancel"),
    "aum":                (lambda r: (r["aum"] or 0) != 0,     "aum"),
}

_TITULO_METRICA = {
    "clientes": "Clientes", "con_aum": "Cuentas con AuM", "sin_aum": "Cuentas sin AuM",
    "activos": "Cuentas activas", "ratio_actividad": "Ratio de actividad",
    "aranceles": "Aranceles del mes", "arancel_por_activo": "Arancel por cuenta activa",
    "aum": "AuM a fin de mes",
}


def detalle_mes(*, mes: str, metrica: str = "clientes", moneda: str = "ARS",
                limite: int = 500, operador=None, nivel_1=None, nivel_2=None,
                nivel_3=None, nivel_4=None, nivel_5=None, referido=None,
                division=None) -> dict:
    """Cuentas que forman UNA celda (mes × métrica), con su AuM, sus boletos y su
    arancel del mes. Los totales se calculan sobre TODAS las filas y recién después
    se capea la lista → el total del modal no puede diferir de la tabla por el límite."""
    metrica = metrica if metrica in _FILTRO_METRICA else "clientes"
    anio, m = _parse_mes(mes, PROFUNDIDAD_INICIO)
    ini, fin = date(anio, m, 1), _fin_de_mes(anio, m)
    usd = (moneda or "ARS").upper() == "USD"
    filtros = dict(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
                   nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)

    p: dict = {"ini": ini, "fin": fin}
    w = _scope(p, alias="c", **filtros)
    # UNA query: universo del mes + AuM del snapshot + boletos/arancel del mes.
    # Los dos LEFT JOIN son sobre agregados ya reducidos por cuenta, no filas crudas.
    rows = _q(
        f"WITH esc AS (SELECT c.id_cuenta, c.nivel_1, c.nivel_3, c.operador_email, "
        f"                    c.fecha_alta_legajo "
        f"             FROM comitentes c WHERE {w} AND c.fecha_alta_legajo <= %(fin)s), "
        f"snap AS (SELECT max(t.fecha) AS f FROM tenencia t "
        f"         WHERE t.aum = 'si' AND t.fecha <= %(fin)s), "
        f"a AS (SELECT t.id_cuenta, SUM(t.valuacion) AS aum FROM tenencia t, snap "
        f"      WHERE t.fecha = snap.f AND t.aum = 'si' GROUP BY t.id_cuenta), "
        f"o AS (SELECT o.id_cuenta, count(*) AS n_boletos, max(o.concertacion) AS ult, "
        f"             COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS arancel "
        f"      FROM operaciones o WHERE o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s "
        f"        AND {_act_where('o')} GROUP BY o.id_cuenta) "
        f"SELECT e.id_cuenta, u.denominacion, e.nivel_1, e.nivel_3, op.nombre AS operador_nombre, "
        f"       e.fecha_alta_legajo, (SELECT f FROM snap) AS snapshot, "
        f"       COALESCE(a.aum, 0) AS aum, COALESCE(o.n_boletos, 0) AS n_boletos, "
        f"       o.ult, COALESCE(o.arancel, 0) AS arancel "
        f"FROM esc e "
        f"LEFT JOIN cuentas u ON u.id_cuenta = e.id_cuenta "
        f"LEFT JOIN operadores op ON op.email = e.operador_email "
        f"LEFT JOIN a ON a.id_cuenta = e.id_cuenta "
        f"LEFT JOIN o ON o.id_cuenta = e.id_cuenta", p)

    snapshot = rows[0]["snapshot"] if rows else None
    mep_cache: dict[str, float | None] = {}
    f_ar = _mep_de(fin, mep_cache) if usd else None
    f_aum = (_mep_de(snapshot, mep_cache) if usd and snapshot else f_ar)

    items = [{
        "id_cuenta": r["id_cuenta"],
        "denominacion": r["denominacion"] or "—",
        "operador_nombre": r["operador_nombre"],
        "nivel_1": r["nivel_1"], "nivel_3": r["nivel_3"],
        "fecha_alta_legajo": _iso(r["fecha_alta_legajo"]),
        "aum": _cv(_f(r["aum"]), f_aum) if snapshot is not None else None,
        "n_boletos": int(r["n_boletos"] or 0),
        "arancel": _cv(_f(r["arancel"]), f_ar),
        "ultima_op": _iso(r["ult"]),
        "activo": int(r["n_boletos"] or 0) > 0,
    } for r in rows]

    # Totales sobre TODAS las cuentas (antes del límite) → cuadran con la tabla.
    n_clientes = len(items)
    n_con_aum = sum(1 for i in items if (i["aum"] or 0) > 0) if snapshot is not None else None
    n_activos = sum(1 for i in items if i["activo"])
    t_aranceles = round(sum(i["arancel"] or 0 for i in items), 2)
    t_aum = round(sum(i["aum"] or 0 for i in items), 2) if snapshot is not None else None
    totales = {
        "clientes": n_clientes,
        "con_aum": n_con_aum,
        "sin_aum": (n_clientes - n_con_aum) if n_con_aum is not None else None,
        "activos": n_activos,
        "ratio_actividad": round(n_activos / n_clientes, 4) if n_clientes else None,
        "aranceles": t_aranceles,
        "arancel_por_activo": round(t_aranceles / n_activos, 2) if n_activos else None,
        "aum": t_aum,
    }

    pred, orden = _FILTRO_METRICA[metrica]
    sel = [i for i in items if pred(i)]
    sel.sort(key=lambda i: (i[orden] or 0), reverse=True)
    n_total = len(sel)
    lim = max(1, int(limite))

    ecuacion = {
        "clientes": f"cuentas activas con alta <= {fin.strftime('%d/%m/%Y')} = {n_clientes}",
        "con_aum": (f"de {n_clientes} clientes, {n_con_aum} tienen valuación > 0 al "
                    f"{_iso(snapshot) or '—'}" if n_con_aum is not None
                    else "sin snapshot de tenencia <= fin de mes → no se puede mirar"),
        "sin_aum": (f"{n_clientes} clientes − {n_con_aum} con AuM = "
                    f"{n_clientes - n_con_aum}" if n_con_aum is not None
                    else "sin snapshot de tenencia <= fin de mes → no se puede mirar"),
        "activos": (f"{n_activos} cuentas con al menos un boleto entre "
                    f"{ini.strftime('%d/%m/%Y')} y {fin.strftime('%d/%m/%Y')}"),
        "ratio_actividad": (f"{n_activos} activos / {n_clientes} clientes = "
                            f"{round(100 * n_activos / n_clientes, 2) if n_clientes else 0}%"),
        "aranceles": (f"suma de aranceles de los boletos entre {ini.strftime('%d/%m/%Y')} "
                      f"y {fin.strftime('%d/%m/%Y')}"),
        "arancel_por_activo": (f"{t_aranceles:,.2f} de aranceles / {n_activos} activos = "
                               f"{totales['arancel_por_activo'] or 0:,.2f}"),
        "aum": (f"suma del AuM de las {n_clientes} cuentas al {_iso(snapshot) or '—'}"
                if snapshot is not None else "sin snapshot de tenencia <= fin de mes"),
    }[metrica]

    return {
        "mes": f"{anio:04d}-{m:02d}", "label": _label(anio, m),
        "ini": _iso(ini), "fin": _iso(fin),
        "metrica": metrica, "titulo": _TITULO_METRICA[metrica],
        "moneda": "USD" if usd else "ARS",
        "mep_aranceles": f_ar, "mep_aum": f_aum,
        "snapshot_aum": _iso(snapshot),
        "desfasaje_dias": (fin - snapshot).days if snapshot is not None else None,
        "ecuacion": ecuacion,
        "totales": totales,
        "n_total": n_total, "limite": lim,
        "items": sel[:lim],
    }


__all__ = ["METRICAS", "PROFUNDIDAD_INICIO", "detalle_mes", "profundidad_clientes"]
