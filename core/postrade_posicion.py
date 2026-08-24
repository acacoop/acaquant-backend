"""core/postrade_posicion.py — PositionReport → posición de FUTUROS, plana.

Traer la posición de la cámara y dejarla usable son dos cosas distintas, y esta
es la segunda. `PosTrade/PositionReport` devuelve una estructura ANIDADA (el
instrumento adentro de un objeto, las cantidades adentro de un array) y mezcla
futuros con lo que no lo es. Acá se aplana, se filtra y se completa lo que la
API omite.

Está separado del transporte para que `aplanar()` sea **pura**: recibe la
respuesta y devuelve filas, sin red y sin base. Eso es lo que la hace testeable
de verdad — las reglas de abajo se verifican sin llamar a producción.

## Tres reglas que no son obvias

**1. La API OMITE el campo cuando la cantidad es cero.** No manda `LongQty: 0`:
directamente no manda `LongQty`. Si eso se guardara como NULL, "no tengo
posición larga" y "no sé si tengo posición larga" quedarían escritos igual —
y solo uno de los dos es cierto. Se normaliza a **0**.

**2. `PositionQty` es un ARRAY y se EXPANDE.** Un mismo instrumento en una
misma cuenta puede traer varios tipos de posición. Guardarlo como blob obligaría
a re-parsear en cada consulta y haría imposible sumar por tipo.

**3. Solo `SecurityType == 'Futuro'`.** Opciones, PAF G y cualquier otro tipo
quedan afuera — y se CUENTAN, no se descartan en silencio: si un día el mix
cambia, el número de descartados es lo único que lo delata.
"""
from __future__ import annotations

import logging
from typing import Any

from core import postrade

logger = logging.getLogger(__name__)

METODO = "PositionReport"
SECURITY_TYPE_FUTURO = "Futuro"

LARGO, CORTO, PLANO = "long", "short", "flat"


def lado(long_qty: float, short_qty: float) -> str:
    """El LADO de la posición. Es parte de su identidad, no un atributo.

    ⚠️ MEDIDO contra producción (2026-08-24): la cámara devuelve la pata larga
    y la corta del mismo instrumento, en la misma cuenta, como **registros
    separados y con su propio precio promedio** (SOJ.ROS/NOV26 en la cuenta
    331000: una pata a 346,7 y la otra a 355,4).

    Sin este campo en la clave, las dos patas colapsan en una y el UPSERT se
    queda con la última — se pierde una posición entera y su precio, **sin que
    nada falle**. Y netearlas tampoco sirve: en agro tener vendida la cosecha
    nueva y comprada otra posición son dos decisiones distintas, y el promedio
    de cada una es justamente lo que se mira.

    `flat` (las dos en cero) existe para no inventar un lado que la cámara no
    afirmó: una posición cerrada que igual se informa es un dato válido.
    """
    if long_qty and not short_qty:
        return LARGO
    if short_qty and not long_qty:
        return CORTO
    if long_qty and short_qty:
        # No se vio en producción. Si aparece, es una fila con las dos patas
        # adentro y hay que mirarla — no adivinar un lado que no existe.
        return f"{LARGO}+{CORTO}"
    return PLANO


def _num(v: Any) -> float:
    """El número, o 0 si la API omitió el campo (que es como manda los ceros)."""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _opcional(v: Any) -> float | None:
    """Igual que `_num` pero preserva el NULL: para precios e importes, donde
    "no vino" y "vale cero" NO son lo mismo (un settlement de 0 es un dato)."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def aplanar(crudo: Any) -> tuple[list[dict], dict[str, int]]:
    """`Value` de PositionReport → (filas planas de futuros, contadores).

    PURA: no toca red ni base. Devuelve también los contadores porque el
    descarte tiene que ser visible — un filtro silencioso es indistinguible de
    un bug que se come la mitad de los datos.
    """
    filas: list[dict] = []
    stats = {"recibidas": 0, "futuros": 0, "descartadas_no_futuro": 0, "sin_position_qty": 0}

    if not isinstance(crudo, list):
        return filas, stats

    for p in crudo:
        if not isinstance(p, dict):
            continue
        stats["recibidas"] += 1

        inst = p.get("Instrument") or {}
        if not isinstance(inst, dict):
            inst = {}
        if str(inst.get("SecurityType") or "").strip() != SECURITY_TYPE_FUTURO:
            stats["descartadas_no_futuro"] += 1
            continue
        stats["futuros"] += 1

        # La fecha viene ISO con o sin hora ("2026-08-21", "2026-08-21T00:00:00").
        business_date = str(p.get("ClearingBusinessDate") or "")[:10]

        cantidades = p.get("PositionQty")
        if not isinstance(cantidades, list) or not cantidades:
            # Un futuro sin cantidades no es una posición. Se cuenta para que
            # no desaparezca sin dejar rastro.
            stats["sin_position_qty"] += 1
            continue

        for q in cantidades:
            if not isinstance(q, dict):
                continue
            largo, corto = _num(q.get("LongQty")), _num(q.get("ShortQty"))
            filas.append({
                "business_date": business_date,
                "account": str(p.get("Account") or "").strip(),
                "symbol": str(inst.get("Symbol") or "").strip(),
                "position_type": str(q.get("PosType") or "").strip(),
                "side": lado(largo, corto),
                "cfi_code": inst.get("CFICode"),
                "unit_of_measure": inst.get("UnitOfMeasure"),
                "currency": p.get("Currency"),
                "avg_px": _opcional(p.get("AvgPX")),
                "daily_settlement": _opcional(p.get("DailySettlement")),
                "settlement_price": _opcional(p.get("SettlPrice")),
                "settlement_currency": p.get("SettlCurrency"),
                "long_qty": largo,
                "short_qty": corto,
            })

    return filas, stats


def familia_de_contrato(symbol: str) -> str:
    """El contrato SIN su vencimiento — `MAI.ROS/MAR27` → `MAI.ROS`, `DLR102026`
    → `DLR`.

    Existe porque el tamaño del contrato es propiedad del CONTRATO, no del mes:
    todos los `MAI.ROS` valen 100 Tn y todos los `DLR` 1.000 USD, venzan cuando
    venzan. Es lo que permite que un vencimiento nuevo herede el multiplicador de
    sus hermanos en vez de aparecer sin él.
    """
    if "/" in symbol:
        return symbol.split("/", 1)[0]
    return symbol[:3]


def multiplicadores(filas: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """Cuánto vale UN contrato de cada símbolo, DESPEJADO de los propios datos.

    ⚠️ **`long_qty`/`short_qty` vienen en CONTRATOS**, no en toneladas ni en
    dólares. El reporte de la mesa habla de "Posición Tn Neta": sin multiplicar,
    el número sale 100 veces más chico **y no falla nada**, porque las cantidades
    igual suman bien entre sí. Un total plausible y equivocado.

    No se hardcodea una tabla de tamaños porque la cámara ya manda la respuesta
    adentro: entre el precio promedio, el de ajuste y el settlement hay una
    identidad exacta::

        daily_settlement = (settlement_price − avg_px) × (long − short) × mult

    **Y la unidad de medida NO alcanza para adivinarlo.** Medido el 2026-08-24:
    dentro de `unit_of_measure = 'Tn'` conviven TRES multiplicadores — 100 en los
    `.ROS`, **10 en los `.MIN`** (los minis) y 5 en los `.CME`. Una regla por
    unidad erraría 10× en los minis. Por símbolo es constante (mismo valor en las
    59 filas de SOJ.ROS/NOV26), y un contrato nuevo aparece sin darlo de alta.

    Devuelve `({symbol: {multiplicador, unit_of_measure, filas_base,
    dispersion, fuente}}, sin_resolver)`.

    Un símbolo que no se puede despejar —settlement 0, posición plana, precios
    ausentes— **hereda el de sus hermanos de contrato** (`MAI.ROS/MAR27` toma el
    de los otros `MAI.ROS`). Eso importa porque un vencimiento nuevo entra sin
    settlement el primer día, y sin la herencia su posición no se podría expresar
    en toneladas: quedaría fuera del total sin que nada falle. La herencia se
    marca en `fuente` para que se sepa que no se midió directo, y solo se aplica
    si **todos** los hermanos coinciden — si difieren, el contrato cambió de
    tamaño y eso lo tiene que mirar una persona.

    Lo que ni así se resuelve se devuelve APARTE. Inventar un 1 por default sería
    peor que no tenerlo, porque el total saldría igual de plausible.
    """
    crudos: dict[str, list[float]] = {}
    unidades: dict[str, str | None] = {}
    simbolos: set[str] = set()

    for f in filas:
        symbol = f.get("symbol") or ""
        if not symbol:
            continue
        simbolos.add(symbol)
        unidades.setdefault(symbol, f.get("unit_of_measure"))

        neta = _num(f.get("long_qty")) - _num(f.get("short_qty"))
        settl, avg, px = f.get("daily_settlement"), f.get("avg_px"), f.get("settlement_price")
        if settl is None or avg is None or px is None:
            continue
        base = (float(px) - float(avg)) * neta
        if not base or not settl:
            continue
        crudos.setdefault(symbol, []).append(float(settl) / base)

    salida: dict[str, dict] = {}
    for symbol, valores in crudos.items():
        # La MEDIANA y no el promedio: un solo redondeo raro de la cámara no
        # puede correr el tamaño del contrato.
        ordenados = sorted(valores)
        medio = ordenados[len(ordenados) // 2]
        mult = round(medio)
        if mult <= 0:
            continue
        salida[symbol] = {
            "symbol": symbol,
            "multiplicador": float(mult),
            "unit_of_measure": unidades.get(symbol),
            "filas_base": len(valores),
            # Cuánto se apartó el peor caso del valor redondeado. Es lo que
            # delata que un símbolo cambió de tamaño: hoy da < 0,002.
            "dispersion": round(max(abs(v - mult) for v in valores), 6),
            "fuente": "derivado",
        }

    # Herencia entre hermanos del mismo contrato, para el vencimiento que todavía
    # no tiene de dónde despejarse. Solo si TODOS los hermanos coinciden: dos
    # tamaños distintos bajo el mismo contrato no es un dato, es una pregunta.
    porfamilia: dict[str, set[float]] = {}
    for symbol, d in salida.items():
        porfamilia.setdefault(familia_de_contrato(symbol), set()).add(d["multiplicador"])

    for symbol in sorted(simbolos - set(salida)):
        hermanos = porfamilia.get(familia_de_contrato(symbol), set())
        if len(hermanos) != 1:
            continue
        salida[symbol] = {
            "symbol": symbol,
            "multiplicador": next(iter(hermanos)),
            "unit_of_measure": unidades.get(symbol),
            "filas_base": 0,
            "dispersion": None,
            "fuente": "hermano",
        }

    sin_resolver = sorted(simbolos - set(salida))
    if sin_resolver:
        logger.info("multiplicador sin resolver en %d símbolos: %s",
                    len(sin_resolver), ", ".join(sin_resolver[:5]))
    return salida, sin_resolver


def traer(fecha: str) -> tuple[list[dict], dict[str, int]]:
    """Pide la posición de `fecha` (AAAAMMDD) y la devuelve aplanada.

    ⚠️ **`viewDetails=false`, y esto se midió (2026-08-24).** El parámetro no
    cambia el formato: cambia QUÉ se devuelve.

    - `viewDetails=true` NO es "la posición con más detalle": son las
      **operaciones individuales**. Cada fila trae su `ExecID`, `TradeNumber` y
      `PX`. Para una sola posición devolvió **1115 filas** — un trade cada una.
    - `viewDetails=false` es la **posición consolidada**, y consolida bien:
      medido contra la suma de esos 1115 trades da idéntico
      (LongQty 1.889,0 y DailySettlement 1.694.750,0 en las dos).

    Se usa el consolidado en vez de sumar el detalle porque sumarlo sería
    reimplementar algo que la cámara ya hace: cualquier diferencia futura entre
    nuestra suma y la de ellos sería un bug nuestro, y encima uno silencioso.
    De yapa el consolidado trae `AvgPX`, que el detalle no tiene.

    **Sin `viewPafg`**: ese parámetro cambia el reporte por el de contratos PAF
    G, que es otra cosa y no es lo que se pide acá.
    """
    crudo = postrade.leer(METODO, {
        "clearingBusinessDate": postrade.fecha_api(fecha),
        "viewDetails": "false",
    })
    filas, stats = aplanar(crudo)
    logger.info(
        "PositionReport %s: %d recibidas, %d futuros → %d filas",
        fecha, stats["recibidas"], stats["futuros"], len(filas),
    )
    return filas, stats
