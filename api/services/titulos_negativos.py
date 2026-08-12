"""api/services/titulos_negativos.py — control de nominales NEGATIVOS en T0.

Back Office → CONTROL TÍTULOS NEGATIVOS. Un nominal negativo en la posición
liquidada A HOY (T0 = lo que está en custodia y se puede entregar) significa que
se comprometió un título que no se tiene: o hay una venta en descubierto, o falta
que entre una compra, o hay un error de carga. Sea cual sea, el back office tiene
que verlo el mismo día, no en la conciliación de mañana.

Fuente: `portafolio.tenencia_live`, horizonte `t0`, que refresca el daemon
`jobs/tenencia_live.py` durante la rueda. **No se persiste nada** — esto es una
lectura de esa tabla, y la frescura la dice `actualizado_at` (por cuenta).

Por qué T0 y no T1: T1 incluye lo concertado hoy que todavía no liquidó, así que
una venta de hoy ya aparecería restando aunque el título siga en custodia. T0 es
la foto de lo que REALMENTE se tiene ahora, que es lo que hay que poder entregar.

**Las MONEDAS se excluyen por default.** Un saldo de caja negativo es un
descubierto, no un título en descubierto: es otro problema, con otro dueño, y
mezclarlo tapa lo que esta vista existe para mostrar. Se pueden incluir con
`incluir_monedas=True`.
"""
from __future__ import annotations

from api.cache import cached
from api.services._sql import _f, _q

# Carteras que SON efectivo. La `cartera` sale de `portafolio.assets`, así que un
# asset sin clasificar la tiene NULL — por eso hay un segundo criterio abajo.
_CARTERAS_CASH = ("MONEDAS", "MONEDA")


# Predicado que deja afuera el efectivo. Dos criterios en OR, y el segundo es la
# red del primero:
#
#   1. `cartera` ∈ (MONEDAS, MONEDA) — el dato bueno, cuando está.
#   2. la `unidad` NO empieza con `[` — las unidades de título traen el código de
#      especie entre corchetes (`[8032] MSFT`); el efectivo es la moneda pelada
#      (`ARS`, `USD`, `USDC`). Cubre el asset sin cartera cargada, que si no se
#      colaría como si fuera un título.
#
# Se excluye si CUALQUIERA de los dos da efectivo, así un asset a medio clasificar
# no ensucia la vista. El patrón del LIKE va como PARÁMETRO y no interpolado: con
# `%` literal dentro del SQL, psycopg lo toma como placeholder y revienta.
_SIN_MONEDAS = ("AND NOT (upper(coalesce(cartera, '')) = ANY(%(cash)s) "
                "OR unidad NOT LIKE %(pfx)s)")


@cached(ttl=10)
def titulos_negativos(incluir_monedas: bool = False, solo_aum: bool = False) -> dict:
    """Posiciones con `cantidad < 0` en T0, ordenadas de la más negativa a la menos.

    `solo_aum` NO va prendido por default a propósito: el flag `aum` excluye
    contrapartes, FCI, OTC y cuentas propias de cash — pero un nominal negativo
    ahí sigue siendo un descubierto que alguien tiene que resolver. Filtrar por
    AuM escondería justo las cuentas operativas.

    TTL de 10s: el daemon reescribe una cuenta como mucho cada 60s (debounce), y
    la vista pollea cada 20s — 10s colapsa las ráfagas de varios usuarios sin que
    nadie vea un dato viejo.
    """
    params: dict = {}
    extra = ""
    if not incluir_monedas:
        extra = _SIN_MONEDAS
        params = {"cash": list(_CARTERAS_CASH), "pfx": "[%"}
    if solo_aum:
        extra += " AND aum = 'si'"

    filas = _q(
        "SELECT id_cuenta, cuenta, unidad, ticker, cartera, cantidad, precio, "
        "       valuacion, moneda, aum, actualizado_at, desde_consultado, origen "
        "FROM portafolio.tenencia_live "
        "WHERE horizonte = 't0' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live) "
        "  AND cantidad < 0 "
        f"  {extra} "
        "ORDER BY cantidad ASC",
        params,
    )

    # Frescura y fecha salen de la MISMA tabla en una query aparte y NO de las
    # filas devueltas: si no hay ningún negativo (el caso bueno) igual hay que
    # poder decir "mirado a las 14:32", o la vista vacía se lee como "no cargó".
    meta = _q(
        "SELECT MAX(fecha) AS fecha, MAX(actualizado_at) AS ult, "
        "       count(DISTINCT id_cuenta) AS cuentas, count(*) AS filas "
        "FROM portafolio.tenencia_live "
        "WHERE horizonte = 't0' "
        "  AND fecha = (SELECT MAX(fecha) FROM portafolio.tenencia_live)"
    )[0]

    return {
        "fecha": meta["fecha"].isoformat() if meta["fecha"] else None,
        "horizonte": "t0",
        # Última vez que el daemon tocó CUALQUIER cuenta. Si esto envejece, la
        # vista no es "no hay negativos": es "no sabemos".
        "actualizado_at": (meta["ult"].isoformat() if meta["ult"] else None),
        "cuentas_en_posicion": meta["cuentas"],
        "filas_en_posicion": meta["filas"],
        "incluir_monedas": incluir_monedas,
        "solo_aum": solo_aum,
        "n": len(filas),
        "negativos": [{
            "id_cuenta": f["id_cuenta"],
            "cuenta": f["cuenta"] or f"[{f['id_cuenta']}]",
            "unidad": f["unidad"],
            "ticker": f["ticker"] or f["unidad"],
            "cartera": f["cartera"] or "",
            "cantidad": _f(f["cantidad"]),
            "precio": _f(f["precio"]),
            "valuacion": _f(f["valuacion"]),
            "moneda": f["moneda"] or "",
            "aum": f["aum"] or "",
            "origen": f["origen"] or "",
            "desde_consultado": (f["desde_consultado"].isoformat()
                                 if f["desde_consultado"] else None),
            "actualizado_at": (f["actualizado_at"].isoformat()
                               if f["actualizado_at"] else None),
        } for f in filas],
    }
