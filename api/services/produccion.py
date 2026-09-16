"""api/services/produccion.py — PRODUCCIÓN DEL OPERADOR: el catálogo de lo que le suma.

EL PUNTO
========

Hasta 2026-09-16 la producción de un comercial era UNA sola cosa: el `arancel` de
`operaciones.operaciones`. Pero la mesa genera plata por más de una vía, y las
otras existían sin sumarle a nadie — MESA DE DINERO la mostraba en su propia tab
y ahí moría. Medido antes de unificar (`scripts/diag_produccion_operador`): eran
ARS 45,4M sobre ARS 208,6M de arancel (+21,8%) y **daba vuelta el ranking en dos
posiciones**, con un comercial cuya producción real era 5,4× la que se le contaba.

⚠️ **NO ES TODO "ARANCEL".** El arancel es lo que paga el CLIENTE por operar. Lo
de Mesa de Dinero es RESULTADO DE INTERMEDIACIÓN: no lo paga un cliente y no
baja a nivel cliente. Sumarlos dentro de un campo llamado `arancel` daría un
número que mezcla dos conceptos y que nadie podría explicar de dónde sale. Por eso
esto devuelve **los componentes por separado Y el total**: el total es para medir,
los componentes son para poder contestar "¿por qué da esto?".

CÓMO SE AGREGA UNA FUENTE
=========================

Se declara una `Fuente` en `FUENTES` y se escribe su `fn`. La fila obliga a decir
QUÉ aporta, de DÓNDE sale y CÓMO llega al operador — que es lo que hace auditable
un número que reparte plata entre personas. Nada queda suelto (misma ley que
`agente/catalogo.py` y `core/duplicados.py`: se declara, no se descubre).

LAS INVARIANTES
===============

1. **`total` == suma de los componentes.** Congelado por test. Es lo que impide
   que el total y su desglose se contradigan.
2. **"Sin datos" ≠ "cero".** Cada fuente publica su COBERTURA real (primer y
   último día con datos). Mesa arranca 2026-07-01: pedir 2025 no es que los
   operadores no produjeron, es que la fuente no existía. Leer un dato faltante
   como una caída es exactamente el error que esto evita.
3. **Todo en ARS adentro**, cada fuente convierte con SU propia regla (ver
   `_mesa`), y una sola vez.
4. **Lo que no se puede filtrar, se DECLARA — no se filtra a medias.** Ver
   `mesa_aplica`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from api.services.comercial import _arancel_expr
from api.services.comercial_sql import _arancel_where, _f, _q
from api.services.comisiones_fci import INICIO_HISTORICO

# id de cada fuente. El del arancel de mercado se usa además como clave de
# compatibilidad: era el ÚNICO número que existía antes de este módulo.
MERCADO = "mercado"
MESA = "mesa"
FCI = "fci_tenencia"


@dataclass(frozen=True)
class Fuente:
    """Una vía por la que un operador genera plata.

    `atribucion` es prosa a propósito: es la cadena exacta que lleva un peso hasta
    una persona, y tiene que poder leerla alguien que no lee SQL cuando pregunte
    por qué un número es suyo.
    """

    id: str
    etiqueta: str        # cómo se llama en pantalla
    concepto: str        # QUÉ es esta plata (no todo es "arancel")
    atribucion: str      # la cadena que la lleva hasta el operador
    tabla: str           # de dónde sale
    fn: Callable         # (desde, hasta, moneda, mep_hoy, op_de, ids) -> {email: monto}


# ── FUENTE 1 · ARANCEL DE MERCADO ────────────────────────────────────────────
def _mercado(desde: date, hasta: date, moneda: str, mep_hoy: float | None,
             op_de: dict[str, str], ids: list[str] | None) -> dict[str, float]:
    """Σ arancel de boletos, por operador de la CUENTA del boleto.

    Qué boleto suma lo decide `_arancel_where()` (un solo lugar en todo el repo:
    `arancel > 0 AND etapa <> 'solicitud'`, cierres incluidos porque el arancel de
    caución vive SOLO en el cierre). La conversión a USD usa el MEP DEL BOLETO
    (`_arancel_expr`), que es el TC real de esa operación."""
    arax = _arancel_expr(moneda, mep_hoy)
    scope = " AND id_cuenta = ANY(%(ids)s)" if ids is not None else ""
    p: dict = {"d": desde, "h": hasta}
    if ids is not None:
        p["ids"] = ids
    out: dict[str, float] = {}
    for r in _q(f"SELECT id_cuenta, COALESCE(SUM({arax}),0) AS m FROM operaciones "
                f"WHERE {_arancel_where()} AND anulado_en IS NULL "
                f"AND concertacion >= %(d)s AND concertacion <= %(h)s{scope} "
                f"GROUP BY id_cuenta", p):
        op = op_de.get(r["id_cuenta"])
        if op:
            out[op] = out.get(op, 0.0) + _f(r["m"])
    return out


# ── FUENTE 2 · INTERMEDIACIÓN (MESA DE DINERO) ───────────────────────────────
# El 50% viene de la REGLA 50/50 que la tab RESULTADOS ya aplica desde siempre
# (`mesa_dinero.resultados`): el trade que originó un comercial se reparte mitad
# para él y mitad para la Mesa. Acá se toma SOLO la mitad del comercial — la otra
# no es de ningún operador. Que el divisor sea el mismo importa: si este módulo
# dijera 100% y la vista de Mesa 50%, las dos pantallas mostrarían números
# distintos para el mismo hecho y ninguna estaría "mal".
_SQL_MESA = """
SELECT o.observacion_email                                   AS op,
       SUM(o.resultado) / 2.0                                AS ars,
       SUM(o.resultado / 2.0 / t.tc) FILTER (WHERE t.tc IS NOT NULL AND t.tc <> 0) AS usd,
       count(*) FILTER (WHERE t.tc IS NULL OR t.tc = 0)      AS sin_tc
FROM operaciones.mesa_dinero o
LEFT JOIN operaciones.mesa_dinero_tc t ON t.fecha = o.fecha
WHERE o.observacion_email IS NOT NULL
  AND o.fecha >= %(d)s AND o.fecha <= %(h)s
GROUP BY 1
"""


def _mesa(desde: date, hasta: date, moneda: str, mep_hoy: float | None,
          op_de: dict[str, str], ids: list[str] | None) -> dict[str, float]:
    """50% del resultado de intermediación, por `observacion_email`.

    ⚠️ El USD NO se calcula con el MEP como el arancel: se usa el **TC manual del
    día** (`mesa_dinero_tc`), que es con lo que la propia vista de Mesa muestra sus
    dólares (decisión 2026-07-29: TC de carga manual, no auto-MEP). Convertir esto
    con MEP daría, para el MISMO hecho, un número distinto al que muestra Mesa — y
    ninguna de las dos pantallas estaría equivocada, que es la peor forma de
    equivocarse. Cada fuente se convierte con su propia verdad.

    Los días SIN TC cargado no suman USD (no se inventa un tipo de cambio): eso
    viaja en `cobertura()['mesa']['sin_tc']` para que la vista pueda avisar que el
    dólar está incompleto, en vez de mostrar un total corto en silencio.

    `ids` se ignora a propósito — ver `mesa_aplica` en `comisiones_por_operador`:
    esta plata no cuelga de una cuenta, así que un filtro por cuenta no se le puede
    aplicar. El caller decide si la fuente entra o no; a medias nunca.
    """
    usd = (moneda or "ARS").upper() == "USD"
    rows = _q(_SQL_MESA, {"d": desde, "h": hasta})
    return {r["op"]: _f(r["usd"] if usd else r["ars"]) for r in rows if r["op"]}


def _fci(desde: date, hasta: date, moneda: str, mep_hoy: float | None,
         op_de: dict[str, str], ids: list[str] | None) -> dict[str, float]:
    """Comision por stock FCI, atribuida por cuenta al operador comercial."""
    from api.services.comercial_sql import _fci_por_cuenta

    scope = "id_cuenta = ANY(%(ids)s)" if ids is not None else None
    p = {"ids": ids} if ids is not None else {}
    mes_ini = hasta.replace(day=1).isoformat()
    por_cuenta = _fci_por_cuenta(
        desde=desde.isoformat(), hasta=hasta.isoformat(), mes_ini=mes_ini,
        moneda=moneda, scope=scope, p=p)
    out: dict[str, float] = {}
    for idc, row in por_cuenta.items():
        op = op_de.get(idc)
        if op:
            out[op] = out.get(op, 0.0) + row["fci_total"]
    return out


FUENTES: tuple[Fuente, ...] = (
    Fuente(
        id=MERCADO,
        etiqueta="ARANCEL DE MERCADO",
        concepto="lo que paga el CLIENTE por operar (arancel del boleto)",
        atribucion="boleto → id_cuenta → comitentes.operador_email",
        tabla="operaciones.operaciones",
        fn=_mercado),
    Fuente(
        id=MESA,
        etiqueta="INTERMEDIACIÓN (MESA DE DINERO)",
        concepto="50% del resultado de las ops de intermediación que originó "
                 "(la otra mitad es de la Mesa, no de un operador)",
        atribucion="mesa_dinero.observacion_email → clientes.operadores.email",
        tabla="operaciones.mesa_dinero",
        fn=_mesa),
    Fuente(
        id=FCI,
        etiqueta="FCI (TENENCIA)",
        concepto="arancel por stock de fondos, no por boleto",
        atribucion="foto de tenencia → id_cuenta → comitentes.operador_email",
        tabla="portafolio.tenencia + portafolio.assets",
        fn=_fci),
)

# Fuentes que NO cuelgan de una cuenta comitente: un filtro por segmento/nivel de
# cuenta no se les puede aplicar. Declarado acá y no adentro de cada `fn` para que
# el caller pueda DECIRLO en pantalla antes de mostrar un número recortado.
SIN_CUENTA: frozenset[str] = frozenset({MESA})

_COBERTURA_SQL: dict[str, str] = {
    MERCADO: ("SELECT min(concertacion) AS d, max(concertacion) AS h, 0 AS sin_tc "
              f"FROM operaciones WHERE {_arancel_where()} AND anulado_en IS NULL"),
    MESA: ("SELECT min(o.fecha) AS d, max(o.fecha) AS h, "
           "       count(*) FILTER (WHERE t.tc IS NULL OR t.tc = 0) AS sin_tc "
           "FROM operaciones.mesa_dinero o "
           "LEFT JOIN operaciones.mesa_dinero_tc t ON t.fecha = o.fecha "
           "WHERE o.observacion_email IS NOT NULL"),
            FCI: (f"SELECT min(t.fecha) AS d, max(t.fecha) AS h, 0 AS sin_tc "
            "FROM portafolio.tenencia t "
            "JOIN portafolio.assets a ON a.unidad = t.unidad "
                f"WHERE t.fecha >= DATE '{INICIO_HISTORICO.isoformat()}' "
            "AND upper(btrim(coalesce(t.cartera, ''))) IN ('FCI', 'CARTERA FCI') "
            "AND a.fee_admin IS NOT NULL"),
}


def cobertura() -> dict[str, dict]:
    """Desde/hasta con datos REALES de cada fuente (+ `sin_tc` en Mesa).

    Existe para que "sin datos" y "cero" no se lean igual. Mesa arranca el
    2026-07-01: si alguien mira 2025, sus operadores no tuvieron una caída de
    intermediación — la fuente todavía no existía. Se MIDE contra la tabla en vez
    de declararse a mano porque una fecha hardcodeada envejece y miente sola.

    Nunca levanta: es metadato de una vista, y no puede tumbarla si falla.
    """
    out: dict[str, dict] = {}
    for f in FUENTES:
        try:
            r = _q(_COBERTURA_SQL[f.id])[0]
            out[f.id] = {"desde": r["d"].isoformat() if r["d"] else None,
                         "hasta": r["h"].isoformat() if r["h"] else None,
                         "sin_tc": int(r["sin_tc"] or 0)}
        except Exception:
            # "No pude mirar" se declara: un None acá se distingue de un rango real.
            out[f.id] = {"desde": None, "hasta": None, "sin_tc": 0, "error": True}
    return out


def catalogo() -> list[dict]:
    """El catálogo, serializable — lo consume la vista para explicar cada columna."""
    return [{"id": f.id, "etiqueta": f.etiqueta, "concepto": f.concepto,
             "atribucion": f.atribucion, "tabla": f.tabla} for f in FUENTES]


def comisiones_por_operador(
    desde: date, hasta: date, *, moneda: str = "ARS", mep_hoy: float | None = None,
    op_de: dict[str, str], ids: list[str] | None = None, mesa_aplica: bool = True,
) -> dict[str, dict[str, float]]:
    """`{operador_email: {'mercado': …, 'mesa': …, 'total': …}}`, en la moneda destino.

    `op_de` (id_cuenta → operador_email) lo pasa el caller y no se resuelve acá: es
    un mapa cacheado que el caller ya tiene, y pedirlo evita un import circular con
    `control_comercial_sql`.

    ⚠️ `mesa_aplica=False` EXCLUYE la intermediación. Va en False cuando hay un
    filtro por CUENTA activo (nivel_1..5, referido, división): esa plata no cuelga
    de una cuenta comitente, así que no hay forma de recortarla por segmento. Las
    opciones eran sumarla entera —inflando cualquier vista filtrada— o dejarla
    afuera y decirlo. Se deja afuera: un número que no se puede filtrar bien no se
    filtra a medias en silencio. El caller publica el hecho vía `mesa_aplica` para
    que la pantalla lo aclare. Filtrar por OPERADOR sí es compatible (la fuente ya
    está atribuida por operador), y por eso ese filtro no la apaga.
    """
    out: dict[str, dict[str, float]] = {}
    for f in FUENTES:
        if f.id in SIN_CUENTA and not mesa_aplica:
            continue
        for op, monto in f.fn(desde, hasta, moneda, mep_hoy, op_de, ids).items():
            out.setdefault(op, {})[f.id] = monto
    # El total se deriva SIEMPRE de los componentes presentes — nunca se calcula
    # por otro camino. Es la invariante que impide que el total y su desglose
    # puedan contradecirse (congelada por tests/unit/test_produccion.py).
    for comp in out.values():
        for f in FUENTES:
            comp.setdefault(f.id, 0.0)
        comp["total"] = round(sum(comp[f.id] for f in FUENTES), 2)
    return out


def vacio() -> dict[str, float]:
    """Fila neutra: todos los componentes en cero + total en cero. Que la arme este
    módulo evita que cada caller invente su propio dict y se olvide una fuente el
    día que se agregue la tercera."""
    base = {f.id: 0.0 for f in FUENTES}
    base["total"] = 0.0
    return base


# ── INFORME: intermediación con las MISMAS ventanas TOTAL/MES ────────────────
# El INFORME (vista OPERADORES → INFORME) no pide un rango y ya: pide DOS recortes
# a la vez sobre la misma pasada —TOTAL = [desde, hasta] y MES = el mes calendario
# del `hasta`— y así arma las columnas ARANC. TOTAL y ARANC. MES. La intermediación
# tiene que entrar por las MISMAS dos ventanas o las columnas no cerrarían entre sí.
#
# La ventana se replica de `comercial_sql._rollup_por_cuenta` a propósito y con el
# mismo criterio: sin `desde` el TOTAL es histórico; sin `hasta` corre hasta hoy; el
# MES arranca el día 1 del mes de `hasta`. Si esas dos definiciones se separaran, la
# fila INTERMEDIACIÓN estaría midiendo otro período que el resto de la tabla y la
# suma daría mal sin que nada avise.
_SQL_MESA_VENTANAS = """
SELECT lower(btrim(o.observacion_email)) AS op,
       SUM(CASE WHEN {lb} THEN o.resultado / 2.0 ELSE 0 END)            AS ars_total,
       SUM(CASE WHEN o.fecha >= %(mes_ini)s THEN o.resultado / 2.0 ELSE 0 END) AS ars_mes,
       SUM(CASE WHEN {lb} AND t.tc IS NOT NULL AND t.tc <> 0
                THEN o.resultado / 2.0 / t.tc ELSE 0 END)               AS usd_total,
       SUM(CASE WHEN o.fecha >= %(mes_ini)s AND t.tc IS NOT NULL AND t.tc <> 0
                THEN o.resultado / 2.0 / t.tc ELSE 0 END)               AS usd_mes
FROM operaciones.mesa_dinero o
LEFT JOIN operaciones.mesa_dinero_tc t ON t.fecha = o.fecha
WHERE o.observacion_email IS NOT NULL{ub}{lo}{scope}
GROUP BY 1
"""


def mesa_ventanas(*, desde: str | None, hasta: str | None, mes_ini: str,
                  moneda: str = "ARS",
                  operadores: list[str] | None = None) -> dict[str, dict[str, float]]:
    """`{operador_email: {'total': x, 'mes': y}}` de intermediación, YA en la moneda.

    ⚠️ Viene convertida — el caller NO debe aplicarle su `_cv`. El USD sale del TC
    manual de Mesa (`mesa_dinero_tc`), no del MEP, para que este número no
    contradiga al que muestra la propia vista de Mesa (ver `_mesa`). Los días sin
    TC no suman USD: no se inventa un tipo de cambio.

    `operadores` (lista de emails) acota a un subconjunto — es el ÚNICO filtro que
    esta fuente admite, porque la plata se atribuye al operador y no a una cuenta.
    """
    p: dict = {"mes_ini": mes_ini}
    ub = lo = scope = ""
    lb = "TRUE"
    if hasta:
        p["hasta"] = hasta
        ub = " AND o.fecha <= %(hasta)s"
    if desde:
        p["desde"] = desde
        lb = "o.fecha >= %(desde)s"
        # Piso del scan = min(desde, mes_ini): hacen falta las filas del TOTAL y las
        # del MES. Mismo criterio que `_rollup_por_cuenta`.
        p["lo"] = min(desde, mes_ini)
        lo = " AND o.fecha >= %(lo)s"
    if operadores is not None:
        p["ops"] = [e.lower().strip() for e in operadores]
        scope = " AND lower(btrim(o.observacion_email)) = ANY(%(ops)s)"
    usd = (moneda or "ARS").upper() == "USD"
    sql = _SQL_MESA_VENTANAS.format(lb=lb, ub=ub, lo=lo, scope=scope)
    return {
        r["op"]: {"total": _f(r["usd_total"] if usd else r["ars_total"]),
                  "mes": _f(r["usd_mes"] if usd else r["ars_mes"])}
        for r in _q(sql, p) if r["op"]
    }


# Etiqueta de la fila de intermediación en ARANCELES POR SEGMENTO. La tabla agrupa
# por `nivel_1` de la CUENTA y esta plata no cuelga de ninguna, así que no pertenece
# a ningún segmento: va como fila propia. Vive acá —y no en el front— para que la
# fila y el total que la incluye no puedan llamarse distinto.
SEGMENTO_INTERMEDIACION = "INTERMEDIACIÓN (MESA)"


__all__ = ["FUENTES", "MERCADO", "MESA", "FCI", "SEGMENTO_INTERMEDIACION", "SIN_CUENTA",
           "Fuente", "catalogo", "cobertura", "comisiones_por_operador",
           "mesa_ventanas", "vacio"]
