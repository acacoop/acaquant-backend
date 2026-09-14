"""core/custodia_cuentas.py — QUÉ ES cada cuenta de custodia. Catálogo DECLARADO.

⚠️ **LA CUENTA DE CVSA NO ES UN NÚMERO: ES UN PAR.** `accountNumber` viene
`"80074/222222222"` y las DOS mitades son parte de la identidad. Hasta que esto
existió, el sistema guardaba solo la derecha — y `"74/222222222"` y
`"80074/222222222"` colapsaban al mismo `"222222222"`. Ese es el modo de falla
de la REGLA #9 en su forma más cara: no falla nada, el join encuentra un
comitente que no es, y la pantalla contesta segura el nombre equivocado.

Los ESPACIOS de numeración son tres y se DECLARAN, no se adivinan del string:

    74     comitentes    las cuentas de clientes (`clientes.cuentas.id_cuenta`)
    70074  liquidadoras  por donde pasan los títulos al liquidar
    80074  garantías     lo afectado a garantía en la cámara

Un prefijo que no esté acá devuelve `None` y la pantalla lo muestra como
DESCONOCIDO. Es a propósito: si CVSA agrega un espacio nuevo queremos VERLO, no
que el código lo clasifique de prepo con una regla de strings.

⚠️ **Solo el espacio `74` se puede cruzar con `clientes.cuentas`.** Las
liquidadoras y las de garantías NO son comitentes: si se las joinea por el
número pelado, `80074/555555555` matchea con el comitente `555555555` si existe.
Quien lea estas tablas tiene que filtrar por participante — no es una
optimización, es lo que separa un dato correcto de uno inventado.

Regla de capas: `core/` no importa nada del proyecto.
"""
from __future__ import annotations

# El espacio de los comitentes. Es el único cruzable con `clientes.cuentas`.
COMITENTES = "74"

ESPACIOS: dict[str, str] = {
    COMITENTES: "comitentes",
    "70074": "liquidadoras",
    "80074": "garantias",
}

# Las cuentas con nombre propio. Las pasó la mesa desde la ficha de CVSA; no se
# derivan de ningún dato, por eso están escritas. Clave: el `accountNumber`
# COMPLETO — que es la identidad.
ESPECIALES: dict[str, str] = {
    "70074/10000":     "Cta. Liquidadora gral.",
    "70074/50000":     "Cta. Liquidadora Licis",
    "80074/555555555": "Cta. Gtías. Clientes",
    "80074/222222222": "Cta. Gtías. House",
    "80074/888888888": "Cta. Gtías. Default funds",
}


def partir(account_number: str | None) -> tuple[str, str] | None:
    """`"80074/222222222"` → `("80074", "222222222")`. None si no tiene la forma.

    Las dos mitades juntas SON la cuenta. Devolver solo la derecha era el bug.
    """
    partes = (account_number or "").split("/")
    if len(partes) != 2:
        return None
    izq, der = partes[0].strip(), partes[1].strip()
    if not izq or not der:
        return None
    return izq, der


def espacio(participante: str | None) -> str | None:
    """`"80074"` → `"garantias"`. None si el prefijo no está declarado.

    None NO es un error a tapar: es un espacio de numeración que apareció y que
    hay que mirar. La pantalla lo muestra como DESCONOCIDO.
    """
    return ESPACIOS.get((participante or "").strip())


def es_comitente(participante: str | None) -> bool:
    """¿Esta cuenta se puede cruzar con `clientes.cuentas`? Solo el espacio 74."""
    return (participante or "").strip() == COMITENTES


def denominacion(account_number: str | None) -> str | None:
    """El nombre de la cuenta si es una de las declaradas. None si no.

    Para las del espacio 74 devuelve None SIEMPRE: su nombre vive en
    `clientes.cuentas` y duplicarlo acá crearía una segunda copia capaz de
    quedar vieja (REGLA #9 B). Este catálogo es solo para las que no son
    comitentes y por eso no tienen dónde más estar.
    """
    return ESPECIALES.get((account_number or "").strip())


def ficha(account_number: str | None) -> dict:
    """Todo lo que se sabe de una cuenta SIN tocar la base.

    `{participante, id_cuenta, espacio, denominacion, es_comitente}`. Con un
    `accountNumber` ilegible devuelve todo en None — que se vea, no que se
    invente.
    """
    partido = partir(account_number)
    if partido is None:
        return {"participante": None, "id_cuenta": None, "espacio": None,
                "denominacion": None, "es_comitente": False}
    part, idc = partido
    return {"participante": part, "id_cuenta": idc, "espacio": espacio(part),
            "denominacion": denominacion(account_number),
            "es_comitente": es_comitente(part)}
