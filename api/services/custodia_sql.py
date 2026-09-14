"""api/services/custodia_sql.py — lectura de la tenencia de CVSA. PURO (sin FastAPI).

Sirve la tab CUSTODIA de `/back-office`. Los datos los escribe la PC de oficina
(`scripts/byma_feed.py` → `POST /api/ingest/custodia/holdings`). Doc:
`docs/BYMA_CUSTODIA.md`.

UNA SOLA QUERY, la foto entera del día, y los contadores se cuentan sobre esas
mismas filas. Antes eran tres queries (lista + totales + estados) y cada cambio
de filtro en la pantalla disparaba las tres de nuevo: con ~2.800 filas eso es un
segundo de espera para tildar un chip.

La foto de un día es un CONJUNTO CERRADO y chico. Traerla una vez y filtrarla en
memoria es más rápido y, sobre todo, **hace imposible que un contador contradiga
a su lista**: salen del mismo array. Si algún día una foto no entrara en un
payload razonable, la decisión se revisa — `LIMITE_FILAS` deja el techo a la
vista en vez de que el día que pase nadie sepa por qué faltan filas.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from core import custodia_cuentas as cuentas_cvsa
from core.postgres import get_pool

# Todo lo que no es AVAILABLE es tenencia que NO se puede entregar ni garantizar.
# Es la información que Aunesa no da, así que la vista la cuenta aparte.
DISPONIBLE = "AVAILABLE"

# Las dos formas de mirar la tenencia de Aunesa, y cuándo sirve cada una.
#
# BYMA actualiza sus tenencias DESPUÉS DE LAS 21. Durante el día, la foto de la
# Caja refleja el cierre anterior: un bono comprado el viernes en T+1 liquida
# hoy, Hygirus ya lo muestra y BYMA todavía no. Comparar contra T0 a las 15
# marca ese desfasaje como si fueran descalces.
#
#   t0      → `tenencia_live` horizonte t0: liquidada a HOY. Es la correcta para
#             la CONCILIACIÓN NOCTURNA, cuando las dos fotos ya son del mismo
#             momento.
#   cierre  → `portafolio.tenencia`, la foto conciliada e inmutable. Es lo
#             comparable con BYMA DURANTE EL DÍA.
#
# Las dos tienen `gar_cantidad`, así que la regla de descontar garantías es la
# misma en ambas: no hay caso especial.
FUENTES = ("t0", "cierre")

# Dos nominales que difieren en centavos no son un descalce: es redondeo.
TOLERANCIA = 0.01

# Techo duro del payload. Medido: una foto real son ~2.800 filas de BYMA, que
# agrupadas por (cuenta, papel) dan menos. Si alguna vez se toca, la respuesta
# lo dice (`truncado`) en vez de mentir por lo bajo.
LIMITE_FILAS = 20_000


def ultima_fecha() -> date | None:
    """La fecha más reciente con datos. None si todavía no entró ninguna foto."""
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(fecha) FROM portafolio.custodia_cvsa")
        return (cur.fetchone() or [None])[0]


def tenencias(*, fecha: date | None = None, fuente: str = "t0") -> dict[str, Any]:
    """La foto de CVSA de un día, cruzada contra la tenencia de Aunesa (T0).

    EL UNIVERSO LO DEFINE BYMA. Se parte de lo que trae la Caja y se le busca su
    contraparte en Aunesa, no al revés: en Hygirus hay un montón de cosas que no
    están en CVSA (FCI, por ejemplo) y arrastrarlas acá sería llenar la pantalla
    de descalces que no son descalces. La Caja es la fuente de verdad; lo que
    ella no registra, esta vista no lo discute.

    EL GRANO ES (cuenta, papel). CVSA informa una fila por `sub_balance_type`
    —el mismo papel puede estar parte AVAILABLE y parte EMBARGO—; Aunesa informa
    una sola. Se suman los estados de CVSA antes de comparar, porque si no el
    valor de Aunesa se repetiría en cada fila y la diferencia daría mal en todas.

    ⚠️ **`VN AUNESA` NO ES `cantidad` A SECAS: es `cantidad - gar_cantidad`.**
    BYMA informa la tenencia sin lo que está afectado en garantía, así que
    comparar contra el total de Aunesa marcaría en rojo toda cuenta con algo
    caucionado. Sin `gar_cantidad` (NULL) se usa `cantidad` tal cual.

    Las filas sin `unidad` (el código de la Caja no tiene instrumento en
    `assets`) no tienen contra qué cruzarse: viajan con `vn_aunesa = None` y la
    vista las marca como "sin comparar". No son una diferencia — decir que lo
    son sería inventar un descalce donde lo que falta es una traducción.

    El cruce se calcula en la LECTURA y no se guarda: es un derivado de dos
    tablas vivas, y persistirlo crearía una tercera copia capaz de quedar vieja
    mientras las otras dos se mueven.
    """
    if fuente not in FUENTES:
        fuente = "t0"
    f = fecha or ultima_fecha()
    if f is None:
        return {"fecha": None, "fuente": fuente, "filas": [], "total_filas": 0, "cuentas": 0,
                "sin_asset": 0, "trabado": 0, "difieren": 0, "sin_comparar": 0,
                "truncado": False, "actualizado_at": None, "fecha_aunesa": None,
                "actualizado_aunesa": None,
                "espacios": [], "estados": [],
                "aviso": "todavía no entró ninguna foto de la Caja de Valores"}

    sql = """
        WITH byma AS (
            SELECT participante, id_cuenta, unidad, cvsa_id,
                   SUM(cantidad)                                   AS vn_byma,
                   SUM(cantidad) FILTER (WHERE sub_balance_type <> %(disp)s) AS trabado,
                   string_agg(DISTINCT sub_balance_type, ' · ' ORDER BY sub_balance_type)
                                                                   AS estados,
                   max(actualizado_at)                             AS actualizado_at
              FROM portafolio.custodia_cvsa
             WHERE fecha = %(f)s
             GROUP BY participante, id_cuenta, unidad, cvsa_id
        )
        SELECT b.participante, b.id_cuenta, cu.denominacion, b.cvsa_id, b.unidad, a.ticker,
               b.estados, b.vn_byma, b.trabado, b.actualizado_at,
               t.cantidad, t.gar_cantidad, t.fecha, t.actualizado_at AS act_aunesa
          FROM byma b
          -- ⚠️ SOLO el espacio 74 se cruza con comitentes. Sin ese filtro, la
          -- cuenta de garantías `80074/555555555` matchea con el comitente
          -- `555555555` y la pantalla muestra el nombre de un cliente que no
          -- tiene nada que ver — sin que falle nada. Ídem con Aunesa: compararía
          -- la tenencia de un cliente contra la cámara.
          LEFT JOIN clientes.cuentas   cu ON cu.id_cuenta = b.id_cuenta
                                         AND b.participante = %(comitentes)s
          LEFT JOIN portafolio.assets   a ON a.unidad     = b.unidad
          -- El join con Aunesa solo puede existir si sabemos cómo se llama el
          -- papel de este lado: con `unidad` NULL no matchea y queda sin comparar.
          LEFT JOIN ({aunesa}) t
                 ON t.id_cuenta = b.id_cuenta
                AND t.unidad    = b.unidad
                AND b.participante = %(comitentes)s
         ORDER BY b.participante, b.id_cuenta, a.ticker NULLS LAST,
                  b.unidad NULLS LAST, b.cvsa_id
         LIMIT %(lim)s
    """

    # Cada fuente trae las MISMAS cuatro columnas, así que el resto de la query
    # no sabe de dónde salieron. `actualizado_at` no existe en la foto
    # conciliada (es inmutable y no se refresca): va NULL y la vista muestra
    # solo la fecha.
    if fuente == "cierre":
        aunesa = ("SELECT id_cuenta, unidad, cantidad, gar_cantidad, fecha, "
                  "       NULL::timestamptz AS actualizado_at "
                  "  FROM portafolio.tenencia "
                  " WHERE fecha = (SELECT max(fecha) FROM portafolio.tenencia)")
    else:
        aunesa = ("SELECT id_cuenta, unidad, cantidad, gar_cantidad, fecha, actualizado_at "
                  "  FROM portafolio.tenencia_live "
                  " WHERE horizonte = 't0' "
                  "   AND fecha = (SELECT max(fecha) FROM portafolio.tenencia_live)")

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql.format(aunesa=aunesa),
                    {"f": f, "disp": DISPONIBLE, "lim": LIMITE_FILAS,
                     "comitentes": cuentas_cvsa.COMITENTES})
        crudas = cur.fetchall()

    filas = []
    cuentas: set[str] = set()
    por_estado: dict[str, int] = {}
    sin_asset = difieren = sin_comparar = 0
    trabado_filas = 0
    ultimo = None
    fecha_aunesa = None
    ultimo_aunesa = None

    espacios: dict[str, int] = {}
    for r in crudas:
        (part, cta, denom, cvsa_id, unidad, ticker, estados,
         vn_byma, trabado, act, cant, gar, f_aun, act_aun) = r

        # La cuenta se nombra por su FICHA, no por el número: una liquidadora y
        # un comitente pueden compartir el número y son cosas distintas.
        account_number = f"{part}/{cta}" if part else cta
        espacio = cuentas_cvsa.espacio(part)
        denom = denom or cuentas_cvsa.denominacion(account_number)
        espacios[espacio or "desconocido"] = espacios.get(espacio or "desconocido", 0) + 1

        vn_byma = float(vn_byma or 0)
        trabado = float(trabado or 0)

        # LA REGLA: BYMA informa sin garantías, así que del lado de Aunesa hay
        # que descontarlas. `gar_cantidad` NULL = no hay nada afectado.
        vn_aunesa = None if cant is None else float(cant) - float(gar or 0)
        dif = None if vn_aunesa is None else vn_byma - vn_aunesa

        if unidad is None:
            sin_asset += 1
        if vn_aunesa is None:
            sin_comparar += 1
        elif abs(dif or 0) > TOLERANCIA:
            difieren += 1

        cuentas.add(account_number)
        for e in (estados or "").split(" · "):
            if e:
                por_estado[e] = por_estado.get(e, 0) + 1
        # CUENTA DE FILAS, no suma de nominales: el chip que lo muestra está al
        # lado de los de estado, que cuentan filas. Un total en nominales ahí
        # se lee como si fueran filas y no significa nada sumado entre papeles
        # distintos (¿mil bonos más mil acciones son dos mil qué?).
        if trabado:
            trabado_filas += 1
        if ultimo is None or (act and act > ultimo):
            ultimo = act
        if f_aun and (fecha_aunesa is None or f_aun > fecha_aunesa):
            fecha_aunesa = f_aun
        if act_aun and (ultimo_aunesa is None or act_aun > ultimo_aunesa):
            ultimo_aunesa = act_aun

        filas.append({
            "id_cuenta": cta, "cuenta": denom, "cvsa_id": cvsa_id,
            # La identidad completa, para que la pantalla no muestre un número
            # suelto que puede ser de tres cuentas distintas.
            "participante": part, "account_number": account_number,
            "espacio": espacio,
            "comitente": cuentas_cvsa.es_comitente(part),
            "unidad": unidad, "ticker": ticker, "estados": estados,
            "vn_byma": vn_byma,
            "vn_aunesa": vn_aunesa,
            "dif": dif,
            "trabado": trabado,
            # Se manda el crudo también: cuando una diferencia aparece, lo
            # primero que se pregunta es si viene de la garantía.
            "aunesa_cantidad": None if cant is None else float(cant),
            "aunesa_garantia": None if gar is None else float(gar),
        })

    return {
        "fecha": f.isoformat(),
        "fuente": fuente,
        # La fecha de la OTRA foto. Si no coinciden, la comparación mezcla dos
        # momentos y la pantalla tiene que poder decirlo.
        "fecha_aunesa": fecha_aunesa.isoformat() if fecha_aunesa else None,
        "actualizado_aunesa": ultimo_aunesa.isoformat() if ultimo_aunesa else None,
        "filas": filas,
        "total_filas": len(filas),
        "cuentas": len(cuentas),
        "sin_asset": sin_asset,
        "trabado": trabado_filas,
        "difieren": difieren,
        "sin_comparar": sin_comparar,
        "truncado": len(filas) >= LIMITE_FILAS,
        "actualizado_at": ultimo.isoformat() if ultimo else None,
        # Cuántas filas hay en cada espacio de numeración. Es lo que contesta
        # «¿tenemos la tenencia de las liquidadoras y las de garantías?».
        "espacios": sorted(({"espacio": k, "n": v} for k, v in espacios.items()),
                           key=lambda x: -x["n"]),
        "estados": sorted(({"estado": k, "n": v} for k, v in por_estado.items()),
                          key=lambda x: -x["n"]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MOVIMIENTOS — la tabla guarda PATAS, la pantalla muestra MOVIMIENTOS
# ─────────────────────────────────────────────────────────────────────────────
# Cada `instructionReference` viene dos veces con `volumen` de signo opuesto: una
# por la cuenta que entrega y otra por la que recibe. Mostrar las filas crudas es
# mostrar cada movimiento duplicado con signos distintos — el usuario deja de
# confiar en la tabla en diez segundos, y con razón.
#
# El plegado se hace ACÁ y no en el front: el front de esta app no deriva ni suma
# nada (su CLAUDE.md es explícito). Además así el contador y la lista salen del
# mismo lugar y no pueden contradecirse.
LIMITE_MOVIMIENTOS = 5_000


def movimientos(*, fecha: str | date | None = None, dias: int = 1) -> dict:
    """Movimientos de custodia de una fecha (o de los últimos `dias`), plegados.

    `dias=1` es el día solo. Se ofrece una ventana porque `/transactions/today`
    corre varias veces al día y una liquidación puede aparecer tarde: mirar solo
    hoy a las 9 de la mañana muestra una tabla vacía que no significa nada.
    """
    dias = max(1, min(int(dias or 1), 30))
    with get_pool().connection() as conn, conn.cursor() as cur:
        f = _fecha_movimientos(cur, fecha)
        if f is None:
            return {"fecha": None, "dias": dias, "movimientos": [], "total": 0,
                    "patas": 0, "sin_par": 0, "descalces": 0, "sin_asset": 0,
                    "cuentas": {}, "estados": [],
                    "truncado": False, "actualizado_at": None}

        cur.execute(
            "SELECT fecha_liq, referencia, participante, id_cuenta, cvsa_id, unidad, "
            "       sub_balance_type, volumen, monto, moneda, moneda_codigo, "
            "       contraparte, contraparte_cta, estado, estado_motivo, "
            "       fuente, actualizado_at "
            "  FROM portafolio.custodia_movimientos "
            " WHERE fecha_liq <= %s AND fecha_liq > %s - %s::int "
            " ORDER BY fecha_liq DESC, referencia, volumen DESC "
            " LIMIT %s",
            (f, f, dias, LIMITE_MOVIMIENTOS))
        patas = cur.fetchall()

    return _plegar(patas, fecha=f, dias=dias)


def _fecha_movimientos(cur, fecha: str | date | None) -> date | None:
    """La fecha pedida, o la última con movimientos. None si la tabla está vacía."""
    if fecha:
        return date.fromisoformat(fecha) if isinstance(fecha, str) else fecha
    cur.execute("SELECT max(fecha_liq) FROM portafolio.custodia_movimientos")
    fila = cur.fetchone()
    return fila[0] if fila else None


def _cuenta_ficha(participante: str | None, id_cuenta: str) -> dict:
    """Qué ES esta cuenta, sin tocar la base. Ver `core/custodia_cuentas.py`.

    Las liquidadoras y las de garantías NO son comitentes: su nombre sale del
    catálogo declarado, no de `clientes.cuentas` — joinearlas por el número
    pelado devolvería el nombre de un cliente que no tiene nada que ver.
    """
    acc = f"{participante}/{id_cuenta}" if participante else id_cuenta
    return {"account_number": acc, "participante": participante,
            "id_cuenta": id_cuenta,
            "espacio": cuentas_cvsa.espacio(participante),
            "denominacion": cuentas_cvsa.denominacion(acc),
            "comitente": cuentas_cvsa.es_comitente(participante)}


def _una_cuenta(ctas: set) -> str | None:
    """La cuenta de ese lado. `None` si no hay pata nuestra; si hubiera más de
    una cuenta, lo DICE en vez de elegir una al azar."""
    if not ctas:
        return None
    return next(iter(ctas)) if len(ctas) == 1 else f"{len(ctas)} cuentas"


def _plegar(patas: list, *, fecha: date, dias: int) -> dict:
    """Agrupa las patas por `(fecha, referencia)` en un movimiento cada una.

    ⚠️ El signo ES el dato: la pata negativa es la que ENTREGA, la positiva la
    que RECIBE. No se usa `abs()` en ningún lado — perder el signo es perder de
    qué lado está cada cuenta.

    ⚠️ `sin_par` NO es un error: un movimiento contra una cuenta de otro agente
    solo tiene UNA pata nuestra. Se cuenta para que la pantalla lo pueda mostrar
    tal cual, en vez de aparentar una partida rota.

    ⚠️ **UN MOVIMIENTO PUEDE TENER MÁS DE DOS PATAS, y está MEDIDO.** El primer
    lote real (116 filas, 87 referencias) dio 106 combinaciones distintas de
    `referencia+cuenta+instrumento` y 116 sumando `sub_balance_type`: o sea **10
    filas donde la MISMA cuenta liquida el MISMO papel repartido en dos
    sub-balances** (igual que en tenencias, parte AVAILABLE y parte trabado).
    Por eso el volumen del movimiento se ACUMULA por lado (`entra` / `sale`) y no
    se toma de la primera pata: tomándola, esos 10 movimientos mostrarían un
    nominal PARCIAL —más chico que el real— sin que nada falle.
    """
    grupos: dict[tuple, dict] = {}
    fichas: dict[str, dict] = {}     # account_number → qué es esa cuenta
    sin_asset = 0
    por_estado: dict[str, int] = {}
    ultimo = None

    for (fliq, ref, part, cta, cvsa, unidad, sub, vol, monto, mon, mon_cod,
         cparte, cparte_cta, estado, motivo, fuente, act) in patas:
        # La cuenta se identifica por el PAR. `555555555` puede ser la cuenta de
        # garantías de clientes (80074) o un comitente: no son la misma.
        cuenta = _cuenta_ficha(part, cta)
        fichas.setdefault(cuenta["account_number"], cuenta)
        if ultimo is None or (act and act > ultimo):
            ultimo = act
        if not unidad:
            sin_asset += 1

        clave = (fliq, ref)
        g = grupos.get(clave)
        if g is None:
            g = grupos[clave] = {
                "fecha_liq": fliq.isoformat(),
                "referencia": ref,
                "unidad": unidad,
                "cvsa_id": cvsa,
                "moneda": mon,
                "moneda_codigo": mon_cod,
                "estado": estado,
                "estado_motivo": motivo,
                "fuente": fuente,
                "entrega": None,       # la cuenta que sale (volumen < 0)
                "recibe": None,        # la cuenta que entra (volumen > 0)
                "volumen": None,       # nominales del movimiento, en positivo
                "monto": None,
                "contraparte": cparte,
                "contraparte_cta": cparte_cta,
                "patas": 0,
                "neto": 0.0,           # tiene que dar 0 si las dos patas están
                # Acumuladores por lado. Privados: se resuelven al cerrar.
                "_entra": 0.0, "_sale": 0.0,
                "_monto_entra": 0.0, "_monto_sale": 0.0,
                "_ctas_entrega": set(), "_ctas_recibe": set(),
            }
        # Lo que solo trae un método no puede quedar afuera por el orden de las
        # patas: se completa con lo primero que no sea None.
        for campo, valor in (("unidad", unidad), ("moneda", mon), ("estado", estado),
                             ("estado_motivo", motivo), ("contraparte", cparte),
                             ("contraparte_cta", cparte_cta)):
            if g[campo] is None and valor is not None:
                g[campo] = valor

        g["patas"] += 1
        v = None if vol is None else float(vol)
        if v is not None:
            g["neto"] += v
            # El signo ES el dato: sale la cuenta con volumen < 0, entra la de > 0.
            if v < 0:
                g["_sale"] += -v
                g["_ctas_entrega"].add(cuenta["account_number"])
                if monto is not None:
                    g["_monto_sale"] += abs(float(monto))
            elif v > 0:
                g["_entra"] += v
                g["_ctas_recibe"].add(cuenta["account_number"])
                if monto is not None:
                    g["_monto_entra"] += abs(float(monto))
        if sub:
            por_estado[sub] = por_estado.get(sub, 0) + 1

    movs = list(grupos.values())
    for m in movs:
        # Redondeo: los volúmenes traen 4 decimales y la resta de dos floats
        # deja residuos de 1e-9 que no son un descalce.
        m["neto"] = round(m["neto"], 6)
        m["descalce"] = m["patas"] > 1 and abs(m["neto"]) > 1e-6
        # El nominal del movimiento es el TOTAL de un lado, no el de una pata.
        # Con las dos puntas nuestras `entra == sale`; con una sola, el lado que
        # exista es el movimiento entero.
        m["volumen"] = round(max(m.pop("_entra"), m.pop("_sale")), 6) or None
        m["monto"] = round(max(m.pop("_monto_entra"), m.pop("_monto_sale")), 6) or None
        # Con el papel repartido en dos sub-balances, las dos patas son de la
        # MISMA cuenta y el set colapsa solo. Si de verdad hubiera dos cuentas de
        # un lado, decirlo es más honesto que mostrar una al azar.
        m["entrega"] = _una_cuenta(m.pop("_ctas_entrega"))
        m["recibe"] = _una_cuenta(m.pop("_ctas_recibe"))

    return {
        "fecha": fecha.isoformat(),
        "dias": dias,
        "movimientos": movs,
        "total": len(movs),
        "patas": len(patas),
        # Las fichas de todas las cuentas que aparecen, para que la pantalla
        # muestre «Cta. Gtías. House» y no `222222222` a secas.
        "cuentas": fichas,
        "sin_par": sum(1 for m in movs if m["patas"] == 1),
        "descalces": sum(1 for m in movs if m["descalce"]),
        "sin_asset": sin_asset,
        "truncado": len(patas) >= LIMITE_MOVIMIENTOS,
        "actualizado_at": ultimo.isoformat() if ultimo else None,
        "estados": sorted(({"estado": k, "n": v} for k, v in por_estado.items()),
                          key=lambda x: -x["n"]),
    }
