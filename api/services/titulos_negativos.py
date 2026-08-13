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


def _saldos_negativos() -> dict:
    """Saldos de EFECTIVO en descubierto, desde `portafolio.control_saldos`.

    Es otra fuente y otra pregunta que la de arriba. `tenencia_live` es una
    posición PROYECTADA (mete adentro lo que todavía no liquidó), así que una
    caución que vence mañana deja la cuenta en falso negativo hoy — y por eso el
    efectivo estaba excluido de esta pantalla: era ruido. `control_saldos` guarda
    lo **liquidado** (endpoint `cuentas/{id}/posiciones`), donde un negativo es un
    descubierto REAL. Verificado contra la cuenta 805 el 2026-08-13.

    El signo ya viene corregido por el daemon: acá `cantidad < 0` es, sin
    interpretación, plata que falta.

    ⚠️ Degrada en silencio a `disponible: false` si la tabla todavía no existe.
    La escribe un daemon nuevo y el `apply_schema` puede no haber corrido aún:
    sin esto, una tabla faltante tumbaría con un 500 la pantalla ENTERA de
    control de negativos, que hoy funciona y no depende de esto.
    """
    try:
        filas = _q(
            "SELECT id_cuenta, cuenta, ticker, cantidad, cantidad_pendiente, "
            "       filas_origen, actualizado_at "
            "FROM portafolio.control_saldos "
            "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.control_saldos) "
            "  AND cantidad < 0 "
            "ORDER BY cantidad ASC")
        meta = _q("SELECT MAX(fecha) AS fecha, MAX(actualizado_at) AS ult, "
                  "       count(DISTINCT id_cuenta) AS cuentas "
                  "FROM portafolio.control_saldos "
                  "WHERE fecha = (SELECT MAX(fecha) FROM portafolio.control_saldos)")[0]
    except Exception:
        return {"disponible": False, "n": 0, "filas": [], "fecha": None,
                "actualizado_at": None, "cuentas_en_control": 0}
    return {
        "disponible": True,
        "fecha": meta["fecha"].isoformat() if meta["fecha"] else None,
        "actualizado_at": meta["ult"].isoformat() if meta["ult"] else None,
        "cuentas_en_control": meta["cuentas"],
        "n": len(filas),
        "filas": [{
            "id_cuenta": f["id_cuenta"],
            "cuenta": f["cuenta"] or f"[{f['id_cuenta']}]",
            "ticker": f["ticker"],
            "cantidad": _f(f["cantidad"]),
            "cantidad_pendiente": _f(f["cantidad_pendiente"]),
            "filas_origen": f["filas_origen"],
            "actualizado_at": (f["actualizado_at"].isoformat()
                               if f["actualizado_at"] else None),
        } for f in filas],
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
        "saldos": _saldos_negativos(),
    }
