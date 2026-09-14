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

from core.postgres import get_pool

# Todo lo que no es AVAILABLE es tenencia que NO se puede entregar ni garantizar.
# Es la información que Aunesa no da, así que la vista la cuenta aparte.
DISPONIBLE = "AVAILABLE"

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


def tenencias(*, fecha: date | None = None) -> dict[str, Any]:
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
    f = fecha or ultima_fecha()
    if f is None:
        return {"fecha": None, "filas": [], "total_filas": 0, "cuentas": 0,
                "sin_asset": 0, "trabado": 0, "difieren": 0, "sin_comparar": 0,
                "truncado": False, "actualizado_at": None, "fecha_aunesa": None,
                "actualizado_aunesa": None,
                "estados": [],
                "aviso": "todavía no entró ninguna foto de la Caja de Valores"}

    sql = """
        WITH byma AS (
            SELECT id_cuenta, unidad, cvsa_id,
                   SUM(cantidad)                                   AS vn_byma,
                   SUM(cantidad) FILTER (WHERE sub_balance_type <> %(disp)s) AS trabado,
                   string_agg(DISTINCT sub_balance_type, ' · ' ORDER BY sub_balance_type)
                                                                   AS estados,
                   max(actualizado_at)                             AS actualizado_at
              FROM portafolio.custodia_cvsa
             WHERE fecha = %(f)s
             GROUP BY id_cuenta, unidad, cvsa_id
        )
        SELECT b.id_cuenta, cu.denominacion, b.cvsa_id, b.unidad, a.ticker,
               b.estados, b.vn_byma, b.trabado, b.actualizado_at,
               t.cantidad, t.gar_cantidad, t.fecha, t.actualizado_at AS act_aunesa
          FROM byma b
          LEFT JOIN clientes.cuentas   cu ON cu.id_cuenta = b.id_cuenta
          LEFT JOIN portafolio.assets   a ON a.unidad     = b.unidad
          -- El join con Aunesa solo puede existir si sabemos cómo se llama el
          -- papel de este lado: con `unidad` NULL no matchea y queda sin comparar.
          LEFT JOIN portafolio.tenencia_live t
                 ON t.id_cuenta = b.id_cuenta
                AND t.unidad    = b.unidad
                AND t.horizonte = 't0'
                AND t.fecha     = (SELECT max(fecha) FROM portafolio.tenencia_live)
         ORDER BY b.id_cuenta, a.ticker NULLS LAST, b.unidad NULLS LAST, b.cvsa_id
         LIMIT %(lim)s
    """

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"f": f, "disp": DISPONIBLE, "lim": LIMITE_FILAS})
        crudas = cur.fetchall()

    filas = []
    cuentas: set[str] = set()
    por_estado: dict[str, int] = {}
    sin_asset = difieren = sin_comparar = 0
    trabado_filas = 0
    ultimo = None
    fecha_aunesa = None
    ultimo_aunesa = None

    for r in crudas:
        (cta, denom, cvsa_id, unidad, ticker, estados,
         vn_byma, trabado, act, cant, gar, f_aun, act_aun) = r

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

        cuentas.add(cta)
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
        "estados": sorted(({"estado": k, "n": v} for k, v in por_estado.items()),
                          key=lambda x: -x["n"]),
    }
