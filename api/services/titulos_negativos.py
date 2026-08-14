"""api/services/titulos_negativos.py — control de nominales NEGATIVOS en T0 y T1.

Back Office → CONTROL TÍTULOS NEGATIVOS. Un nominal negativo significa que se
comprometió un título que no se tiene: o hay una venta en descubierto, o falta que
entre una compra, o hay un error de carga. Sea cual sea, el back office tiene que
verlo el mismo día, no en la conciliación de mañana.

Se devuelven los DOS horizontes, porque contestan preguntas distintas y las dos
importan:

  · **T0** — liquidada A HOY. Es lo que está en custodia AHORA y se puede
    entregar, garantizar o caucionar. Un negativo acá es un descubierto REAL:
    hoy no se puede cumplir.
  · **T1** — liquidada a MAÑANA, con lo concertado hoy adentro. Un negativo acá
    y no en T0 es un descubierto que se VIENE: todavía hay tiempo de resolverlo.

Fuente: `portafolio.tenencia_live`, que refresca el daemon `jobs/tenencia_live.py`
durante la rueda. **No se persiste nada** — esto es una lectura de esa tabla, y la
frescura la dice `actualizado_at`.

**Se excluyen por default MONEDAS y DERIVADOS**, porque en los dos el negativo es
NORMAL y no un problema:
  · efectivo negativo = descubierto bancario — otro problema, otro dueño;
  · derivados negativos = posición vendida — así se representa un short.
Mezclarlos llena la pantalla de filas que no son el tema, y una pantalla llena de
ruido se deja de mirar.
"""
from __future__ import annotations

from api.cache import cached
from api.services._sql import _f, _q

# Carteras donde un nominal negativo NO es un descubierto. La `cartera` sale de
# `portafolio.assets`, así que un asset sin clasificar la tiene NULL — por eso hay
# un segundo criterio en el predicado.
_CARTERAS_EXCLUIDAS = ("MONEDAS", "MONEDA", "DERIVADOS", "DERIVADO")

_HORIZONTES = ("t0", "t1")

# Cuentas que NO entran al control de saldos, por `clientes.comitentes.nivel_5`.
# CDC y OTC no son clientes a los que haya que perseguirles un descubierto, y el
# criterio ya está cargado en la segmentación — usarlo en vez de hardcodear un
# patrón sobre el nombre significa que reclasificar una cuenta en Manager la saca
# (o la trae) sin tocar código. Es el mismo valor que ya filtra el selector
# NIVEL 5 del Tablero Comercial.
NIVEL5_EXCLUIDOS: tuple[str, ...] = ("CDC", "OTC")

# ⚠️ La CARTERA se lee de `portafolio.assets` EN VIVO, no de la copia congelada en
# `tenencia_live`. Motivo (2026-08-12): el daemon carga el catálogo de assets UNA
# vez al arrancar y lo reusa las 10 horas que corre, así que un título
# reclasificado a mediodía sigue guardándose con la cartera vieja hasta el día
# siguiente. La cartera es una CLASIFICACIÓN (metadato mutable), no un hecho del
# día: tiene que resolverse al momento de mirar. Con el join, corregir un asset en
# Manager se refleja en el próximo poll.
#
# `tl.cartera` queda de respaldo por si la unidad no está en `assets`.
_CARTERA = "upper(coalesce(nullif(a.cartera, ''), tl.cartera, ''))"

# Predicado que deja afuera esas carteras. Dos criterios en OR, y el segundo es la
# red del primero:
#
#   1. `cartera` ∈ excluidas — el dato bueno, cuando está.
#   2. la `unidad` NO empieza con `[` — las unidades de título traen el código de
#      especie entre corchetes (`[8032] MSFT`); el efectivo es la moneda pelada
#      (`ARS`, `USD`, `USDC`). Cubre el asset sin cartera cargada, que si no se
#      colaría como si fuera un título.
#
# El patrón del LIKE va como PARÁMETRO y no interpolado: con `%` literal dentro
# del SQL, psycopg lo toma como placeholder y la query revienta en runtime.
_EXCLUIR = (f"AND NOT ({_CARTERA} = ANY(%(excl)s) "
            "OR tl.unidad NOT LIKE %(pfx)s)")

_COLS = ("tl.id_cuenta, tl.cuenta, tl.unidad, "
         "coalesce(nullif(a.ticker, ''), tl.ticker) AS ticker, "
         f"{_CARTERA} AS cartera, tl.cantidad, tl.actualizado_at")


def _negativos_de(horizonte: str, incluir_todo: bool) -> list[dict]:
    params: dict = {"h": horizonte}
    extra = ""
    if not incluir_todo:
        extra = _EXCLUIR
        params |= {"excl": list(_CARTERAS_EXCLUIDAS), "pfx": "[%"}

    filas = _q(
        f"SELECT {_COLS} "
        "FROM portafolio.tenencia_live tl "
        "LEFT JOIN portafolio.assets a ON a.unidad = tl.unidad "
        "WHERE tl.horizonte = %(h)s "
        "  AND tl.fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
        "  AND tl.cantidad < 0 "
        f"  {extra} "
        "ORDER BY tl.cantidad ASC",
        params,
    )
    return [{
        "id_cuenta": f["id_cuenta"],
        "cuenta": f["cuenta"] or f"[{f['id_cuenta']}]",
        "unidad": f["unidad"],
        "ticker": f["ticker"] or f["unidad"],
        "cartera": f["cartera"] or "",
        "cantidad": _f(f["cantidad"]),
        "actualizado_at": (f["actualizado_at"].isoformat()
                           if f["actualizado_at"] else None),
    } for f in filas]


def _saldos() -> dict:
    """Saldos de EFECTIVO del día, desde `portafolio.control_saldos`.

    Es otra fuente y otra pregunta que la de arriba. `tenencia_live` es una
    posición PROYECTADA (mete adentro lo que todavía no liquidó), así que una
    caución que vence mañana deja la cuenta en falso negativo hoy — y por eso el
    efectivo estaba excluido de esta pantalla: era ruido. `control_saldos` guarda
    lo **liquidado** (endpoint `cuentas/{id}/posiciones`), donde un negativo es un
    descubierto REAL. Verificado contra la cuenta 805 el 2026-08-13.

    El signo ya viene corregido por el daemon: acá `cantidad < 0` es, sin
    interpretación, plata que falta.

    Se devuelven los saldos **POSITIVOS Y NEGATIVOS** (2026-08-13): la pantalla es
    de 50/50, con lo que está a favor de un lado y el descubierto del otro. El
    corte por signo y por moneda lo hace el front sobre estas mismas filas — así
    cambiar de moneda es instantáneo y los totales de las dos mitades no pueden
    contradecirse entre sí por venir de dos consultas distintas.

    ⚠️ Degrada en silencio a `disponible: false` si la tabla todavía no existe.
    La escribe un daemon nuevo y el `apply_schema` puede no haber corrido aún:
    sin esto, una tabla faltante tumbaría con un 500 la pantalla ENTERA de
    control de negativos, que hoy funciona y no depende de esto.
    """
    try:
        # Una sola query trae el saldo, QUIÉN atiende la cuenta y su nivel_5. El
        # operador sale del mismo viaje que el saldo: pedirlo aparte serían 1.900
        # roundtrips a Supabase por nada (el peaje es ~8.5ms cada uno, y lo que
        # cuesta es la CANTIDAD de queries, no su plan).
        filas = _q(
            "SELECT cs.id_cuenta, cs.cuenta, cs.ticker, cs.cantidad, "
            "       cs.actualizado_at, c.nivel_5, c.operador_email, "
            "       o.nombre AS operador_nombre "
            "FROM portafolio.control_saldos cs "
            "LEFT JOIN clientes.comitentes c ON c.id_cuenta = cs.id_cuenta "
            "LEFT JOIN clientes.operadores o ON o.email = c.operador_email "
            "WHERE cs.fecha = (SELECT MAX(fecha) FROM portafolio.control_saldos) "
            "ORDER BY cs.cantidad ASC")
        meta = _q("SELECT MAX(fecha) AS fecha, MAX(actualizado_at) AS ult, "
                  "       count(DISTINCT id_cuenta) AS cuentas "
                  "FROM portafolio.control_saldos "
                  "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.control_saldos)")[0]
    except Exception:
        return {"disponible": False, "n": 0, "n_negativos": 0, "filas": [],
                "fecha": None, "actualizado_at": None, "cuentas_en_control": 0,
                "ocultas": 0, "excluidos": list(NIVEL5_EXCLUIDOS)}

    # El corte CDC/OTC se hace en PYTHON y no en el WHERE a propósito: filtrando en
    # SQL las filas ocultas desaparecen sin dejar rastro y la pantalla no puede
    # decir «además hay 4 que no te muestro». Son pocas filas (solo los negativos),
    # así que el filtro en memoria no cuesta nada y devuelve el número.
    visibles, ocultas = [], 0
    for f in filas:
        if (f["nivel_5"] or "").strip().upper() in NIVEL5_EXCLUIDOS:
            ocultas += 1
            continue
        visibles.append(f)

    return {
        "disponible": True,
        "fecha": meta["fecha"].isoformat() if meta["fecha"] else None,
        "actualizado_at": meta["ult"].isoformat() if meta["ult"] else None,
        "cuentas_en_control": meta["cuentas"],
        "ocultas": ocultas,
        "excluidos": list(NIVEL5_EXCLUIDOS),
        "n": len(visibles),
        # El contador de la solapa: lo que hay que mirar son los descubiertos, no
        # el total de saldos. Se cuenta acá y no en el front para que el número de
        # la solapa no dependa de qué moneda esté elegida.
        "n_negativos": sum(1 for f in visibles if (f["cantidad"] or 0) < 0),
        "filas": [{
            "id_cuenta": f["id_cuenta"],
            "cuenta": f["cuenta"] or f"[{f['id_cuenta']}]",
            "ticker": f["ticker"],
            "cantidad": _f(f["cantidad"]),
            # Sin operador cargado la fila igual se muestra: un descubierto no se
            # oculta porque falte la segmentación — al revés, que no tenga dueño
            # es información.
            "operador": f["operador_nombre"] or f["operador_email"] or "",
            "nivel_5": f["nivel_5"] or "",
            "actualizado_at": (f["actualizado_at"].isoformat()
                               if f["actualizado_at"] else None),
        } for f in visibles],
    }


@cached(ttl=10)
def titulos_negativos(incluir_todo: bool = False) -> dict:
    """Nominales negativos en los DOS horizontes, cada uno ordenado del peor al menos.

    `incluir_todo=True` trae también MONEDAS y DERIVADOS — para el caso en que se
    quiera mirar todo junto, no para el uso diario.

    TTL de 10s: el daemon reescribe una cuenta como mucho cada 60s (debounce) y la
    vista pollea cada 20s — 10s colapsa las ráfagas de varios usuarios sin que
    nadie vea un dato viejo.
    """
    horizontes = {h: _negativos_de(h, incluir_todo) for h in _HORIZONTES}

    # Frescura y fecha salen de una query aparte y NO de las filas devueltas: si no
    # hay ningún negativo (el caso bueno) igual hay que poder decir "mirado a las
    # 14:32", o la pantalla vacía se lee como "no cargó" — o peor, como "está todo
    # bien" con el daemon caído.
    meta = _q(
        "SELECT MAX(fecha) AS fecha, MAX(actualizado_at) AS ult, "
        "       count(DISTINCT id_cuenta) AS cuentas "
        "FROM portafolio.tenencia_live "
        "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)"
    )[0]

    return {
        "fecha": meta["fecha"].isoformat() if meta["fecha"] else None,
        "actualizado_at": (meta["ult"].isoformat() if meta["ult"] else None),
        "cuentas_en_posicion": meta["cuentas"],
        "incluir_todo": incluir_todo,
        "t0": {"n": len(horizontes["t0"]), "filas": horizontes["t0"]},
        "t1": {"n": len(horizontes["t1"]), "filas": horizontes["t1"]},
        # Bloque NUEVO y ADITIVO: el front lo puede ignorar sin romperse. Va en la
        # misma respuesta y no en un endpoint aparte porque es la misma pantalla y
        # un segundo request para dos tablas que se miran juntas es un viaje de más.
        "saldos": _saldos(),
    }
