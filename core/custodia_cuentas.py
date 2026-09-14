"""core/custodia_cuentas.py — QUÉ ES cada cuenta de custodia. Catálogo DECLARADO.

⚠️ **LA CUENTA DE CVSA NO ES UN NÚMERO: ES UN PAR.** `accountNumber` viene
`"80074/222222222"` y las DOS mitades son parte de la identidad. Hasta que esto
existió, el sistema guardaba solo la derecha — y `"74/222222222"` y
`"80074/222222222"` colapsaban al mismo `"222222222"`. Ese es el modo de falla
de la REGLA #9 en su forma más cara: no falla nada, el join encuentra un
comitente que no es, y la pantalla contesta segura el nombre equivocado.

Los ESPACIOS de numeración se DECLARAN, no se adivinan del string:

    74     comitentes    las cuentas de clientes (`clientes.cuentas.id_cuenta`)
    70074  liquidadoras  por donde pasan los títulos al liquidar
    80074  garantías     lo afectado a garantía en la cámara

Un prefijo que no esté acá devuelve `None` y la pantalla lo muestra como
DESCONOCIDO. Es a propósito: si CVSA agrega un espacio nuevo queremos VERLO, no
que el código lo clasifique de prepo con una regla de strings.

⚠️ **El espacio NO alcanza para saber si una cuenta es un comitente.** Hay dos
reglas y las dos importan:

  1. Solo los espacios de `ESPACIOS_COMITENTES` se cruzan con `clientes.cuentas`.
     Una liquidadora o una de garantías joineada por el número pelado matchea con
     el comitente que tenga ese número, si existe, y muestra su nombre.
  2. **Y dentro del espacio 74 hay cuentas que TAMPOCO son comitentes**:
     `74/3` y `74/111111111` son de cuotapartes de FCI Bilaterales. Están
     declaradas en `ESPECIALES` y por eso quedan excluidas del cruce. Sin esa
     segunda regla, `74/3` traería el nombre del comitente 3 —si existe— y sería
     el mismo bug con otra ropa.

O sea: **una cuenta es comitente si su espacio lo es Y no está declarada.** La
declaración siempre gana; es lo único que sabemos con certeza.

Regla de capas: `core/` no importa nada del proyecto.
"""
from __future__ import annotations

COMITENTES = "74"

ESPACIOS: dict[str, str] = {
    COMITENTES: "comitentes",
    "70074": "liquidadoras",
    "80074": "garantias",
}

# ⚠️ Los espacios cuyo número de la derecha ES un `clientes.cuentas.id_cuenta`.
#
# Es una lista aparte de `ESPACIOS` a propósito: conocer un espacio y poder
# cruzarlo con clientes son dos cosas distintas. Un espacio nuevo entra primero
# en `ESPACIOS` (para que se vea) y acá SOLO cuando alguien confirmó que sus
# números son de clientes — coincidir con un comitente no es serlo, y si no lo
# es la pantalla muestra el nombre de alguien que no tiene nada que ver.
ESPACIOS_COMITENTES: frozenset[str] = frozenset({COMITENTES})

# Las cuentas con nombre propio. Las pasó la mesa desde la ficha de CVSA; no se
# derivan de ningún dato, por eso están escritas. Clave: el `accountNumber`
# COMPLETO — que es la identidad.
#
# ⚠️ Estar acá significa DOS cosas: que la cuenta tiene este nombre, y que **NO
# es un comitente** aunque viva en el espacio 74. Las dos primeras son el caso:
# están bajo `74/` y no son de ningún cliente.
ESPECIALES: dict[str, str] = {
    "74/3":            "Cuotapartes FCI Bilaterales",
    "74/111111111":    "Cuotapartes FCI Bilaterales",
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


def es_comitente(account_number: str | None) -> bool:
    """¿El número de esta cuenta ES un `clientes.cuentas.id_cuenta`?

    Recibe la cuenta COMPLETA, no el participante, porque las dos mitades
    deciden: el espacio tiene que ser de comitentes **y** la cuenta no puede
    estar declarada (`74/3` vive en el espacio 74 y es de cuotapartes de FCI).
    """
    acc = (account_number or "").strip()
    partido = partir(acc)
    if partido is None:
        return False
    return partido[0] in ESPACIOS_COMITENTES and acc not in ESPECIALES


def no_comitentes_del_espacio() -> tuple[str, ...]:
    """Las cuentas declaradas que viven en un espacio de comitentes.

    Son las que hay que EXCLUIR del join con `clientes.cuentas` en SQL, donde no
    se puede llamar a `es_comitente()` fila por fila. Hoy: `74/3` y
    `74/111111111`.
    """
    return tuple(a for a in ESPECIALES
                 if (partir(a) or ("", ""))[0] in ESPACIOS_COMITENTES)


def denominacion(account_number: str | None) -> str | None:
    """El nombre declarado de la cuenta. None si no está declarada.

    Solo tienen nombre acá las que NO son comitentes: el nombre de un cliente
    vive en `clientes.cuentas` y duplicarlo sería una segunda copia capaz de
    quedar vieja (REGLA #9 B). Por eso un `74/805` cualquiera devuelve None —
    pero `74/3` sí tiene nombre, porque no es de ningún cliente.
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
            "es_comitente": es_comitente(account_number)}
