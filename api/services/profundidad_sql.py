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

6. **FILTRO DE OPERACIÓN** (`operacion`, multi): acota **solo lo que se operó** —
   ACTIVOS, RATIO, ARANCELES y ARANC./ACTIVO. **NO toca** CLIENTES, CON AuM, SIN AuM
   ni AuM, que salen de `comitentes`/`tenencia` y son la base entera. Es a propósito:
   si el denominador también se filtrara, el porcentaje dejaría de significar
   "qué parte de mi base usa este producto" y no significaría nada.
   ⚠️ Con el filtro puesto RATIO cambia de sentido (deja de ser actividad y pasa a
   ser penetración del producto), así que el backend manda **los encabezados ya
   escritos** (`columnas`): el navegador no arma ese texto.

Nada se deriva en el navegador: los ratios, los labels (`jul-25`), los encabezados
y los totales salen de acá.

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
METRICAS = ("clientes", "altas", "con_aum", "sin_aum", "activos", "ratio_actividad",
            "aranceles", "arancel_por_activo", "aum")

# Granularidad de la fila. MES es el default (la tabla nació así); TRIMESTRE se pidió
# para leer ALTAS, que a nivel mensual es ruido —3, 1, 4, 2— y a nivel trimestral es
# una tendencia. La granularidad es un PARÁMETRO y no una tabla nueva: el eje de esta
# tab ya ES el tiempo, y dos pantallas contando altas con dos criterios es exactamente
# lo que venimos sacando del sistema.
GRANULARIDADES = ("mes", "trimestre", "ano")
_GRAN_SQL = {"mes": ("month", "1 month"), "trimestre": ("quarter", "3 month"),
             "ano": ("year", "1 year")}
# Meses que avanza cada paso, y cuántos abarca el período.
_GRAN_PASO = {"mes": 1, "trimestre": 3, "ano": 12}

# Las ÚNICAS métricas que el filtro de operación acota. El resto sale de
# `comitentes`/`tenencia` y es la base entera — filtrarlas rompería el porcentaje.
METRICAS_FILTRABLES = ("activos", "ratio_actividad", "aranceles", "arancel_por_activo")

# `operaciones.operaciones.operacion` es un valor del catálogo `tipos_operacion`
# (columna `data->>'operacion'`), no texto libre. Acá viven SOLO las etiquetas para
# mostrar; los valores posibles NUNCA se hardcodean — se leen de la base
# (`_opciones_operacion`), así que un valor nuevo aparece solo y cae al fallback.
_OP_LABEL = {
    "compra": "Compra",
    "venta": "Venta",
    "caucion_tom_ap": "Caución tomadora",
    "caucion_col_ap": "Caución colocadora",
    "suscripcion_fci": "Suscripción FCI",
    "solicitud_suscripcion_fci": "Solicitud de suscripción FCI",
    "rescate_fci": "Rescate FCI",
    "solicitud_rescate_fci": "Solicitud de rescate FCI",
    "otro": "Otro",
}


def _op_label(v: str) -> str:
    """Etiqueta legible de un valor de `operacion`.

    ⚠️ **Solo se prettifica lo que viene como token técnico** (`futuro_dlr` →
    `Futuro dlr`). Si el valor ya es texto para mostrar —y en esta base lo es:
    `Caución tomadora`, `Ventas PPT`— se devuelve TAL CUAL. `.capitalize()` sobre
    un string ya lindo lo rompe: `"Ventas PPT"` salía `"Ventas ppt"`.
    """
    if not v:
        return ""
    txt = str(v)
    if txt in _OP_LABEL:
        return _OP_LABEL[txt]
    # Token técnico = todo minúscula, sin espacios, separado por guiones bajos.
    if "_" in txt and txt == txt.lower() and " " not in txt:
        return txt.replace("_", " ").strip().capitalize()
    return txt.strip()


def _ops_lista(operacion) -> list[str]:
    """Normaliza el filtro de operación: str o lista → lista sin vacíos ni comodines.
    Lista vacía = SIN filtro (mismo criterio que `_comitentes_where`)."""
    if operacion is None:
        return []
    items = [operacion] if isinstance(operacion, str) else list(operacion)
    out: list[str] = []
    for x in items:
        v = str(x) if x else ""
        # Deduplicado preservando el orden: repetido en la query-string, el
        # encabezado diría "Caución + Caución".
        if v and v not in ("__todos__", "todos", "todas") and v not in out:
            out.append(v)
    return out


def _ops_and(ops: list[str], p: dict, alias: str = "o") -> str:
    """Fragmento ` AND <alias>.operacion = ANY(...)` o cadena vacía. Muta `p`."""
    if not ops:
        return ""
    p["ops_filtro"] = ops
    a = f"{alias}." if alias else ""
    return f" AND {a}operacion = ANY(%(ops_filtro)s)"


def _label(anio: int, mes: int, gran: str = "mes") -> str:
    """`(2025, 7)` → `jul-25`, `Q3-25` o `2025`. El label lo arma el backend: si lo
    derivara el front, dos pantallas podrían nombrar distinto el mismo período."""
    if gran == "ano":
        return f"{anio:04d}"
    if gran == "trimestre":
        return f"Q{(mes - 1) // 3 + 1}-{anio % 100:02d}"
    return f"{_MESES_CORTOS[mes - 1]}-{anio % 100:02d}"


def _gran(v: str | None) -> str:
    """Granularidad válida; cualquier otra cosa cae a `mes` (el comportamiento viejo)."""
    g = (v or "mes").strip().lower()
    return g if g in GRANULARIDADES else "mes"


def _mes_ancla(mes: int, gran: str) -> int:
    """Primer mes del período que contiene a `mes` (trimestral: 1/4/7/10; anual: 1)."""
    if gran == "ano":
        return 1
    return ((mes - 1) // 3) * 3 + 1 if gran == "trimestre" else mes


def _fin_de_periodo(anio: int, mes: int, gran: str) -> date:
    """Último día del período que ARRANCA en (anio, mes)."""
    fin_mes = mes + _GRAN_PASO.get(gran, 1) - 1
    a, m = (anio + 1, fin_mes - 12) if fin_mes > 12 else (anio, fin_mes)
    return _fin_de_mes(a, m)


def _clave(anio: int, mes: int, gran: str) -> str:
    """Clave del período: `2025-07` (mes), `2025-Q3` (trimestre) o `2025` (año). Es lo
    que viaja al modal, así que tiene que volver a (anio, mes) sin ambigüedad."""
    if gran == "ano":
        return f"{anio:04d}"
    if gran == "trimestre":
        return f"{anio:04d}-Q{(mes - 1) // 3 + 1}"
    return f"{anio:04d}-{mes:02d}"


def _parse_clave(v: str | None, gran: str, default: str) -> tuple[int, int]:
    """Inversa de `_clave`. Acepta `2026-Q1` y también `2026-01` (por si el front manda
    un mes con la tabla en trimestral: se ancla al trimestre que lo contiene)."""
    txt = (v or "").strip().upper()
    if gran == "ano":
        try:
            a = int(txt[:4])
            if a >= 1900:
                return a, 1
        except ValueError:
            pass
        return _parse_mes(v, default)[0], 1
    if gran == "trimestre" and "Q" in txt:
        try:
            anio, q = int(txt[:4]), int(txt.split("Q", 1)[1][:1])
            if 1 <= q <= 4 and anio >= 1900:
                return anio, (q - 1) * 3 + 1
        except (ValueError, IndexError):
            pass
    anio, mes = _parse_mes(v, default)
    return anio, _mes_ancla(mes, gran)


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


def _meses(desde: str | None, hasta: str | None, gran: str = "mes") -> list[dict]:
    """Lista de períodos [desde, hasta], ascendente. `hasta` nunca supera el período en
    curso (no se dibujan filas de períodos que todavía no existen).

    En trimestral los extremos se ANCLAN al trimestre que los contiene: pedir
    `desde=2025-08` arranca en Q3-25 igual, porque medio trimestre no es un trimestre
    y una fila a la que le faltan dos meses se lee como una caída del negocio."""
    hoy = _hoy_art()
    a0, m0 = _parse_mes(desde, PROFUNDIDAD_INICIO)
    a1, m1 = _parse_mes(hasta, f"{hoy.year:04d}-{hoy.month:02d}")
    m0, m1 = _mes_ancla(m0, gran), _mes_ancla(m1, gran)
    hoy_m = _mes_ancla(hoy.month, gran)
    # Techo = período en curso.
    if (a1, m1) > (hoy.year, hoy_m):
        a1, m1 = hoy.year, hoy_m
    if (a0, m0) > (a1, m1):
        a0, m0 = a1, m1
    paso = _GRAN_PASO.get(gran, 1)
    out: list[dict] = []
    a, m = a0, m0
    while (a, m) <= (a1, m1) and len(out) < MAX_MESES:
        out.append({
            "mes": _clave(a, m, gran),
            "label": _label(a, m, gran),
            "ini": date(a, m, 1),
            "fin": _fin_de_periodo(a, m, gran),
            # El período en curso está INCOMPLETO: los flujos van hasta hoy, no hasta
            # su último día. Decirlo evita leer una caída que es solo "todavía no pasó".
            "en_curso": (a, m) == (hoy.year, hoy_m),
        })
        m += paso
        if m > 12:
            a, m = a + 1, m - 12
    return out


def _scope(p: dict, alias: str = "", solo_activas: bool = True, **filtros) -> str:
    """WHERE de `comitentes` con los filtros madre de la barra (operador + nivel_1..5
    + referido + division). Un solo predicado para las 3 queries y para el modal.

    `solo_activas=False` solo lo usa el histórico de ALTAS (ver `altas_historico`)."""
    return _comitentes_where(
        filtros.get("operador"), p,
        nivel_1=filtros.get("nivel_1"), nivel_3=filtros.get("nivel_3"),
        referido=filtros.get("referido"), alias=alias,
        nivel_4=filtros.get("nivel_4"), nivel_5=filtros.get("nivel_5"),
        nivel_2=filtros.get("nivel_2"), division=filtros.get("division"),
        solo_activas=solo_activas)


def _mep_de(fecha: date, cache: dict[str, float | None]) -> float | None:
    """MEP del día `fecha` (último con timestamp <= fin de ese día). Se usa el MEP
    DEL MES, no el de hoy: dolarizar un jul-25 al MEP de hoy haría que el histórico
    cambie todos los días sin que haya pasado nada."""
    k = fecha.isoformat()
    if k not in cache:
        from api.services._mep import get_mep_for_date
        cache[k] = get_mep_for_date(k)
    return cache[k]


def _opciones_operacion(ini: date, fin: date, filtros: dict) -> list[dict]:
    """Valores de `operacion` que EXISTEN en el rango y en el scope de clientes.

    ⚠️ **NO recibe el filtro de operación, y no puede recibirlo.** Si lo llevara, al
    elegir "caución" el desplegable se quedaría con una sola opción y no habría forma
    de volver — el bug clásico de poblar un filtro con su propio resultado.

    Sí respeta los filtros madre (operador/niveles/referido/división), igual que el
    resto de la barra: así nunca ofrece un valor que la tabla no puede mostrar.
    """
    p: dict = {"ini": ini, "fin": fin}
    w = _scope(p, alias="c", **filtros)
    rows = _q(
        f"SELECT o.operacion AS v, count(*) AS n "
        f"FROM operaciones o "
        f"JOIN comitentes c ON c.id_cuenta = o.id_cuenta AND {w} "
        f"WHERE o.concertacion >= %(ini)s AND o.concertacion <= %(fin)s "
        f"  AND {_act_where('o')} AND o.operacion IS NOT NULL AND o.operacion <> '' "
        f"GROUP BY o.operacion ORDER BY count(*) DESC", p)
    return [{"valor": r["v"], "label": _op_label(r["v"]), "n_boletos": int(r["n"])}
            for r in rows]


def _columnas(ops: list[str], disponibles: list[dict]) -> dict:
    """Los encabezados de la tabla, YA ESCRITOS. Con filtro puesto, RATIO deja de ser
    "actividad" y pasa a ser "penetración del producto": si el encabezado no lo dice,
    alguien lee ese 5% como que se derrumbó el negocio. El texto se arma UNA vez, acá,
    y no en el navegador."""
    if not ops:
        return {"activos": "Activos", "ratio_actividad": "Ratio activ.",
                "aranceles": "Aranceles", "arancel_por_activo": "Aranc. / activo",
                "sufijo": None}
    etiquetas = {d["valor"]: d["label"] for d in disponibles}
    nombres = [etiquetas.get(v) or _op_label(v) for v in ops]
    # Con 3 o más se corta: un encabezado de tres renglones no se lee.
    sufijo = nombres[0].lower() if len(nombres) == 1 else (
        " + ".join(n.lower() for n in nombres) if len(nombres) == 2
        else f"{len(nombres)} operaciones")
    return {
        "activos": f"Operaron {sufijo}",
        "ratio_actividad": f"% que operó {sufijo}",
        "aranceles": f"Aranceles de {sufijo}",
        "arancel_por_activo": "Aranc. / operó",
        "sufijo": sufijo,
    }


# ── ALTA DE CUENTAS (tab propia) ─────────────────────────────────────────────
#
# El HISTÓRICO COMPLETO de altas, que la tabla de PROFUNDIDAD no puede dar: aquella
# arranca en `PROFUNDIDAD_INICIO` (el ejercicio en curso) porque su pregunta es "cómo
# viene el año". Acá la pregunta es otra —"cómo se construyó la base"— y recortarla al
# ejercicio la deja sin sentido.
#
# TRES diferencias con la tabla, y las tres son a propósito:
#
#   1. **Arranca en la PRIMERA alta que existe**, no en una constante. El rango sale
#      de los datos.
#   2. **Cuenta TODAS las comitentes por default, no solo las `Activa`.** Una cuenta
#      abierta en 2019 y cerrada en 2022 FUE un alta de 2019. Filtrar por estado haría
#      que el pasado se achique cada vez que alguien cierra una cuenta — un gráfico
#      histórico que cambia hacia atrás no se puede usar para nada. `solo_activas`
#      existe para poder mirar las dos cosas, y la respuesta dice cuál se usó.
#   3. **Devuelve el ACUMULADO además del flujo**, que es lo que se pidió ("en
#      sumatoria"): la curva de cómo se construyó la base.
#
# Una sola query, chica: una fila por FECHA distinta de alta (no por cuenta). El
# bucketeo por período se hace en Python — con `date_trunc` habría que elegir la
# granularidad en SQL y volver a pegarle a la base por cada cambio de pill.

def altas_historico(*, granularidad: str | None = None, solo_activas: bool = False,
                    operador=None, nivel_1=None, nivel_2=None, nivel_3=None,
                    nivel_4=None, nivel_5=None, referido=None, division=None) -> dict:
    """Altas de cuentas por período, desde la primera que existe hasta hoy.

    Devuelve por fila: `altas` (el flujo del período) y `acumulado` (la base construida
    hasta su último día). Los dos salen de la MISMA lista de fechas, así que el
    acumulado de una fila es siempre el de la anterior más sus altas."""
    gran = _gran(granularidad)
    filtros = dict(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
                   nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)
    p: dict = {}
    w = _scope(p, solo_activas=solo_activas, **filtros)
    rows = _q(f"SELECT fecha_alta_legajo AS f, count(*) AS n FROM comitentes "
              f"WHERE {w} GROUP BY 1", p)
    sin_alta = sum(int(r["n"]) for r in rows if r["f"] is None)
    con_alta = sorted((r["f"], int(r["n"])) for r in rows if r["f"] is not None)
    total = sum(n for _, n in con_alta)

    vacio = {"granularidad": gran, "solo_activas": bool(solo_activas),
             "filas": [], "total": 0, "sin_alta": sin_alta,
             "primera_alta": None, "ultima_alta": None,
             "meta": {"advertencias": []}}
    if not con_alta:
        return vacio

    hoy = _hoy_art()
    primera, ultima = con_alta[0][0], con_alta[-1][0]
    # El techo es HOY, no la última alta: si hace dos trimestres que no entra nadie,
    # eso son dos filas en CERO y es exactamente el dato que hay que ver. Terminar en
    # la última alta lo escondería dibujando el gráfico como si siguiera creciendo.
    tope = max(ultima, hoy)
    periodos = _meses(_clave(primera.year, primera.month, "mes"),
                      f"{tope.year:04d}-{tope.month:02d}", gran)
    if not periodos:
        return vacio

    filas, acum, i = [], 0, 0
    for per in periodos:
        ini, fin = per["ini"], per["fin"]
        nuevas = 0
        while i < len(con_alta) and con_alta[i][0] <= fin:
            if con_alta[i][0] >= ini:
                nuevas += con_alta[i][1]
            acum += con_alta[i][1]
            i += 1
        filas.append({
            "periodo": per["mes"], "label": per["label"],
            "ini": _iso(ini), "fin": _iso(fin), "en_curso": per["en_curso"],
            "altas": nuevas, "acumulado": acum,
            # Qué parte de la base de HOY se construyó en este período. Lo calcula el
            # backend sobre el mismo total que devuelve.
            "pct_del_total": round(100 * nuevas / total, 2) if total else 0.0,
        })

    advertencias = []
    if sin_alta:
        advertencias.append(
            f"{sin_alta} cuenta(s) del scope NO tienen fecha de alta de legajo: no entran "
            f"en ninguna barra ni en el acumulado")
    advertencias.append(
        "cuenta TODAS las comitentes del scope, cerradas incluidas — un alta de 2019 sigue "
        "siendo un alta de 2019" if not solo_activas else
        "⚠️ SOLO cuentas hoy Activas: el pasado se ve más chico de lo que fue, porque las "
        "que se cerraron desde entonces no figuran")
    return {
        "granularidad": gran, "solo_activas": bool(solo_activas),
        "filas": filas, "total": total, "sin_alta": sin_alta,
        "primera_alta": _iso(primera), "ultima_alta": _iso(ultima),
        "meta": {"advertencias": advertencias},
    }


# ── TABLA ────────────────────────────────────────────────────────────────────
def profundidad_clientes(*, moneda: str = "ARS", desde: str | None = None,
                         hasta: str | None = None, operador=None, nivel_1=None,
                         nivel_2=None, nivel_3=None, nivel_4=None, nivel_5=None,
                         referido=None, division=None, operacion=None,
                         granularidad: str | None = None) -> dict:
    """Filas mm-aa (o Qn-aa) con: clientes · altas · con AuM · sin AuM · activos ·
    ratio · aranceles · arancel/activo · AuM. Cuatro queries agregadas para TODA la
    tabla (una por fuente + las opciones del filtro), no una por período.

    `granularidad`: `mes` (default) o `trimestre`.

    `altas` = cuentas del scope cuya `fecha_alta_legajo` cae DENTRO del período. Es el
    FLUJO; `clientes` es el STOCK acumulado a su último día. Salen las dos de la misma
    lista de fechas —una sola query—, así que no pueden discrepar: el stock de un
    período es el del anterior más sus altas.

    `operacion` (multi) acota SOLO lo que se operó — ver regla 6 del docstring del
    módulo. Vacío = sin filtro = comportamiento idéntico al de siempre."""
    gran = _gran(granularidad)
    meses = _meses(desde, hasta, gran)
    ops_f = _ops_lista(operacion)
    if not meses:
        return {"moneda": moneda, "desde": None, "hasta": None, "filas": [],
                "granularidad": gran,
                "operacion": ops_f, "operaciones_disponibles": [],
                "columnas": _columnas([], []),
                "columnas_filtradas": list(METRICAS_FILTRABLES),
                "meta": {"sin_alta": 0, "advertencias": [], "fuentes": {}}}
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
    altas_por_mes: dict[date, int] = {}
    acum, i = 0, 0
    for m in meses:                         # meses viene ordenado ascendente
        fin, alta_ini = m["fin"], m["ini"]
        nuevas = 0
        while i < len(con_alta) and con_alta[i][0] <= fin:
            if con_alta[i][0] >= alta_ini:
                nuevas += con_alta[i][1]
            acum += con_alta[i][1]
            i += 1
        clientes_por_mes[fin] = acum
        # ALTAS del período. Se cuentan en la MISMA pasada que el acumulado, así el
        # stock y el flujo no pueden contarse distinto. ⚠️ Las altas ANTERIORES al
        # primer período de la tabla suman al stock y NO son altas de ninguna fila:
        # por eso el total de altas no tiene por qué dar el total de clientes.
        altas_por_mes[fin] = nuevas

    # ── 2) ACTIVIDAD + ARANCELES: UN scan de `operaciones` para todos los meses.
    #
    # El mes lo pone `date_trunc`, NO un join contra una lista de meses. Medido con
    # 300k boletos: joinear contra la lista hacía que el planner materializara los
    # boletos y los comparara contra LOS 14 MESES (798.039 filas descartadas por el
    # join filter, O(meses × boletos)). Con un rango cerrado en el WHERE la query
    # entra por `ix_ops_concertacion` una sola vez y el mes sale de una función —
    # O(boletos), y no se degrada al agrandar la ventana.
    # Los meses de la tabla son contiguos y completos, así que cada boleto del rango
    # cae en exactamente uno: `date_trunc('month', concertacion)` ES su `ini`.
    #
    # Se agrega en DOS pasos (por cuenta, después por mes) a propósito: un
    # `count(DISTINCT id_cuenta)` obliga a Postgres a ORDENAR todos los boletos del
    # período (medido: 210k filas → sort en disco). Agrupando primero por
    # (mes, cuenta) el paso caro pasa a ser un HashAggregate de meses × cuentas.
    # `alta` entra en el GROUP BY porque depende solo de la cuenta — no agrega grupos.
    #
    # `_fuera` = boletos de cuentas del scope que NO estaban en el universo del mes
    # (sin fecha de alta, o alta posterior): NO se suman, pero se reportan — una
    # diferencia que no se ve es la que se descubre tarde y mirando una pantalla.
    #
    # El filtro de operación entra ACÁ y en ningún otro lado: es la única query de
    # la tabla que mira `operaciones`. Las de universo y AuM ni se enteran.
    p2: dict = {"rango_ini": meses[0]["ini"], "rango_fin": meses[-1]["fin"]}
    w2 = _scope(p2, alias="c", **filtros)
    f_ops = _ops_and(ops_f, p2, "o")
    dentro = "alta IS NOT NULL AND alta <= fin"
    _trunc, _paso = _GRAN_SQL[gran]
    _mes_ini = f"date_trunc('{_trunc}', o.concertacion)"
    ops = {r["fin"]: r for r in _q(
        f"WITH esc AS (SELECT c.id_cuenta, c.fecha_alta_legajo FROM comitentes c WHERE {w2}), "
        f"por_cuenta AS ("
        f"  SELECT ({_mes_ini} + interval '{_paso}' - interval '1 day')::date AS fin, "
        f"    o.id_cuenta, e.fecha_alta_legajo AS alta, "
        f"    COALESCE(SUM(CASE WHEN {_arancel_where('o')} THEN abs(o.arancel) END), 0) AS arancel "
        f"  FROM operaciones o "
        f"  JOIN esc e ON e.id_cuenta = o.id_cuenta "
        f"  WHERE o.concertacion >= %(rango_ini)s AND o.concertacion <= %(rango_fin)s "
        f"    AND {_act_where('o')}{f_ops} "
        f"  GROUP BY 1, o.id_cuenta, e.fecha_alta_legajo) "
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
            "altas": int(altas_por_mes.get(fin, 0)),
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
    if filas and any(f["altas"] for f in filas):
        advertencias.append(
            f"ALTAS es el flujo DEL período; CLIENTES es el stock acumulado a su último "
            f"día. La suma de altas de la tabla ({sum(f['altas'] for f in filas)}) no da "
            f"el total de clientes: las cuentas dadas de alta antes de "
            f"{filas[0]['label']} ya estaban en la base")
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
    # Las opciones del desplegable salen de la base y SIN el filtro puesto (ver
    # `_opciones_operacion`). Se calculan sobre el rango entero de la tabla.
    disponibles = _opciones_operacion(meses[0]["ini"], meses[-1]["fin"], filtros)
    if ops_f:
        advertencias.append(
            "hay un filtro de operación puesto: ACTIVOS, RATIO y ARANCELES cuentan solo "
            "esa operación; CLIENTES, CON AuM, SIN AuM y AuM son la base entera")
    sufijo_fuente = f" — SOLO {', '.join(ops_f)}" if ops_f else ""
    return {
        "moneda": "USD" if usd else "ARS",
        "desde": meses[0]["mes"], "hasta": meses[-1]["mes"],
        "granularidad": gran,
        "filas": filas,
        # El TOTAL de altas del rango, sumado ACÁ y no en el navegador: es la respuesta
        # a "cuántas cuentas se dieron de alta", y tiene que salir de las mismas filas
        # que la tabla dibuja.
        "total_altas": sum(f["altas"] for f in filas),
        # Qué filtro quedó aplicado (normalizado) y qué se podía elegir.
        "operacion": ops_f,
        "operaciones_disponibles": disponibles,
        # Encabezados ya escritos + qué columnas acota el filtro (el resto va en gris).
        "columnas": _columnas(ops_f, disponibles),
        "columnas_filtradas": list(METRICAS_FILTRABLES),
        "meta": {
            "sin_alta": sin_alta,
            "advertencias": advertencias,
            "fuentes": {
                "clientes": "clientes.comitentes (estado='Activa', fecha_alta_legajo <= fin del período)",
                "altas": "clientes.comitentes (estado='Activa', fecha_alta_legajo DENTRO del período)",
                "aum": "portafolio.tenencia (aum='si'), snapshot más reciente <= fin de mes",
                "activos": ("operaciones.operaciones — cualquier boleto no anulado en el mes"
                            + sufijo_fuente),
                "aranceles": ("operaciones.operaciones — arancel > 0, etapa <> 'solicitud', "
                              "cierres incluidos (la caución cobra en el cierre); se guarda en ARS"
                              + sufijo_fuente),
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
    # ALTAS: las que se dieron de alta DENTRO del período (el resto ya estaba).
    "altas":              (lambda r: bool(r.get("es_alta")),   "aum"),
}

_TITULO_METRICA = {
    "clientes": "Clientes", "con_aum": "Cuentas con AuM", "sin_aum": "Cuentas sin AuM",
    "activos": "Cuentas activas", "ratio_actividad": "Ratio de actividad",
    "aranceles": "Aranceles del mes", "arancel_por_activo": "Arancel por cuenta activa",
    "aum": "AuM a fin de mes",
    "altas": "Altas del período",
}


def detalle_mes(*, mes: str, metrica: str = "clientes", moneda: str = "ARS",
                limite: int = 500, operador=None, nivel_1=None, nivel_2=None,
                nivel_3=None, nivel_4=None, nivel_5=None, referido=None,
                division=None, operacion=None, granularidad: str | None = None) -> dict:
    """Cuentas que forman UNA celda (mes × métrica), con su AuM, sus boletos y su
    arancel del mes. Los totales se calculan sobre TODAS las filas y recién después
    se capea la lista → el total del modal no puede diferir de la tabla por el límite.

    `operacion` tiene que llegar SIEMPRE con el mismo valor que la tabla: si el modal
    no filtrara igual, se abriría una celda de 47 y saldrían 389 cuentas."""
    metrica = metrica if metrica in _FILTRO_METRICA else "clientes"
    gran = _gran(granularidad)
    # `granularidad` tiene que llegar con el MISMO valor que la tabla: si el modal
    # abriera un mes cuando la fila es un trimestre, mostraría un tercio de las
    # cuentas y el total no cerraría — el mismo modo de falla que `operacion`.
    anio, m = _parse_clave(mes, gran, PROFUNDIDAD_INICIO)
    ini, fin = date(anio, m, 1), _fin_de_periodo(anio, m, gran)
    usd = (moneda or "ARS").upper() == "USD"
    ops_f = _ops_lista(operacion)
    filtros = dict(operador=operador, nivel_1=nivel_1, nivel_2=nivel_2, nivel_3=nivel_3,
                   nivel_4=nivel_4, nivel_5=nivel_5, referido=referido, division=division)

    p: dict = {"ini": ini, "fin": fin}
    w = _scope(p, alias="c", **filtros)
    f_ops = _ops_and(ops_f, p, "o")
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
        f"        AND {_act_where('o')}{f_ops} GROUP BY o.id_cuenta) "
        f"SELECT e.id_cuenta, u.denominacion, e.nivel_1, e.nivel_3, op.nombre AS operador_nombre, "
        f"       e.fecha_alta_legajo, (e.fecha_alta_legajo >= %(ini)s) AS es_alta, "
        f"       (SELECT f FROM snap) AS snapshot, "
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
        # Se dio de alta DENTRO del período (vs. las que ya estaban) — es lo que
        # separa el FLUJO del stock, y lo decide SQL con el mismo rango de la fila.
        "es_alta": bool(r["es_alta"]),
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
    n_altas = sum(1 for i in items if i["es_alta"])
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
        "altas": n_altas,
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
        "activos": (f"{n_activos} cuentas con al menos un boleto"
                    + (f" de {' + '.join(_op_label(v).lower() for v in ops_f)}" if ops_f else "")
                    + f" entre {ini.strftime('%d/%m/%Y')} y {fin.strftime('%d/%m/%Y')}"),
        "ratio_actividad": (f"{n_activos} activos / {n_clientes} clientes = "
                            f"{round(100 * n_activos / n_clientes, 2) if n_clientes else 0}%"),
        "aranceles": (f"suma de aranceles de los boletos entre {ini.strftime('%d/%m/%Y')} "
                      f"y {fin.strftime('%d/%m/%Y')}"),
        "arancel_por_activo": (f"{t_aranceles:,.2f} de aranceles / {n_activos} activos = "
                               f"{totales['arancel_por_activo'] or 0:,.2f}"),
        "aum": (f"suma del AuM de las {n_clientes} cuentas al {_iso(snapshot) or '—'}"
                if snapshot is not None else "sin snapshot de tenencia <= fin de mes"),
        "altas": (f"{n_altas} cuenta(s) con fecha de alta de legajo entre "
                  f"{ini.strftime('%d/%m/%Y')} y {fin.strftime('%d/%m/%Y')}"),
    }[metrica]

    return {
        "mes": _clave(anio, m, gran), "label": _label(anio, m, gran),
        "granularidad": gran, "ini": _iso(ini), "fin": _iso(fin),
        "metrica": metrica, "titulo": _TITULO_METRICA[metrica],
        # Contra qué filtro se calculó ESTE detalle. Va a la vista para que no se
        # pueda confundir un modal filtrado con uno que no lo está.
        "operacion": ops_f,
        "operacion_label": (" + ".join(_op_label(v) for v in ops_f) if ops_f else None),
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
