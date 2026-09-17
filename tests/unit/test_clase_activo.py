"""`core/clase_activo.py` — PURO: sin base, sin red. Doc: `docs/AGENT.md` §0.ei."""
from __future__ import annotations

from core import clase_activo as ca


def test_de_derivado_reconoce_call_y_put_con_o_sin_otc():
    assert ca.de_derivado("DERIVADOS", "[OTC - SOJ.ROS/NOV26 380 C]", "") == ca.CALL
    assert ca.de_derivado("DERIVADOS", "[SOJ.ROS/MAY27 340 P]", "") == ca.PUT
    # Un futuro sin C/P: no hay letra que diga qué es, no se propone nada.
    assert ca.de_derivado("DERIVADOS", "[SOJ.ROS/MAY27]", "") == ""
    # Otra cartera: la regla es SOLO de DERIVADOS.
    assert ca.de_derivado("FCI", "[SOJ.ROS/MAY27 340 P]", "") == ""


def test_de_derivado_prueba_el_ticker_si_la_unidad_no_matchea():
    assert ca.de_derivado("DERIVADOS", "[1024] algo raro", "SOJ.ROS/MAY27 340 C") == ca.CALL


def test_de_fci_segun_subyacente_y_moneda():
    assert ca.de_fci("Mercado de Dinero", "USD") == "MM USD"
    assert ca.de_fci("Renta Fija", "ARS") == "ARS T1"
    assert ca.de_fci("Renta Fija", "USD") == "HD T1"
    assert ca.de_fci("Renta Variable", "ARS") == ca.RENTA_VARIABLE
    # Renta Mixta va a T1 como Renta Fija: manda la moneda (§0.fn).
    assert ca.de_fci("Renta Mixta", "ARS") == "ARS T1"
    assert ca.de_fci("Renta Mixta", "USD") == "HD T1"
    assert ca.de_fci("Renta Mixta", "EUR") == ""
    # Cualquier otro subyacente (Retorno Total, …): no se propone.
    assert ca.de_fci("Retorno Total", "USD") == ""
    assert ca.de_fci("Lo que sea", "ARS") == ""
    # Moneda distinta de ARS/USD: no se propone, aunque el subyacente sea válido.
    assert ca.de_fci("Mercado de Dinero", "EUR") == ""
    assert ca.de_fci("Renta Variable", "EUR") == ""


def test_normalizar_nombre():
    assert ca.normalizar_nombre("  Sbs   Pesos  Plus - Clase A  ") == \
        "SBS PESOS PLUS - CLASE A"
    assert ca.normalizar_nombre("Ciclo Nóva Ahórro") == "CICLO NOVA AHORRO"


def test_de_cartera_es_copia_directa_de_las_tres_carteras():
    assert ca.de_cartera("HD") == "HD"
    assert ca.de_cartera("DL") == "DL"
    assert ca.de_cartera("RENTA VARIABLE") == ca.RENTA_VARIABLE
    # upper/strip: minúsculas y espacios no rompen la copia.
    assert ca.de_cartera("  hd  ") == "HD"
    assert ca.de_cartera("renta variable") == ca.RENTA_VARIABLE
    # Otra cartera no tiene copia — la deriva `de_curva`, no `de_cartera`.
    assert ca.de_cartera("ARS") == ""
    assert ca.de_cartera("") == ""


def test_de_futuro_por_el_prefijo_del_contrato():
    # Ejemplos reales del user, futuros y OTC de agro.
    assert ca.de_futuro("DERIVADOS", "[MAI.ROS/JUL27]", "") == "FUTUROS DE MAIZ"
    assert ca.de_futuro("DERIVADOS", "[SOJ.ROS.P/DIS26]", "") == "FUTUROS DE SOJA"
    assert ca.de_futuro("DERIVADOS", "[SOY.CME/ABR27]", "") == "FUTUROS DE SOJA"
    assert ca.de_futuro("DERIVADOS", "[TRI.MIN/DIC26]", "") == "FUTUROS DE TRIGO"
    assert ca.de_futuro("DERIVADOS", "[OTC - CRN.CME/NOV26]", "") == "OTC MAIZ"
    assert ca.de_futuro("DERIVADOS", "[OTC - DLR012027]", "") == "OTC DOLAR"
    # Por ticker suelto, sin corchetes.
    assert ca.de_futuro("DERIVADOS", "", "MAI.ROS/SEP27") == "FUTUROS DE MAIZ"
    # Es una OPCIÓN (C/P al final): esa regla va primero, acá no se propone nada.
    assert ca.de_futuro("DERIVADOS", "[SOJ.ROS/MAY27 364 C]", "") == ""
    # El dólar SOLO se propone bajo OTC.
    assert ca.de_futuro("DERIVADOS", "DLR/ENE27", "") == ""
    # Otra cartera: la regla es SOLO de DERIVADOS.
    assert ca.de_futuro("FCI", "[MAI.ROS/JUL27]", "") == ""
    # Prefijo desconocido: no se propone nada.
    assert ca.de_futuro("DERIVADOS", "[GFG.ROS/JUL27]", "") == ""


def test_en_lista_cerrada_tolera_grafia():
    assert ca.en_lista_cerrada("OTC MAIZ", ["OTC MAÍZ"]) == "OTC MAÍZ"
    assert ca.en_lista_cerrada("OTC MAIZ", ["OTC TRIGO"]) == ""
    assert ca.en_lista_cerrada("", ["OTC MAIZ"]) == ""


def test_de_curva_solo_ars_ars_y_por_el_ajuste():
    assert ca.de_curva("ARS", "ARS", "cer", None) == "CER"
    assert ca.de_curva("ARS", "ARS", "fija", None) == "FIJA"
    assert ca.de_curva("ARS", "ARS", "tamar", "") == "TAMAR"
    # `ajuste_alt` manda DUAL aunque el `ajuste` primario sea uno de los tres.
    assert ca.de_curva("ARS", "ARS", "cer", "tamar") == ca.DUAL
    # `moneda_eje` distinto de ARS: no se propone, aunque la cartera sea ARS.
    assert ca.de_curva("ARS", "USD", "cer", None) == ""
    # Otra cartera: esta regla es solo de la cartera ARS.
    assert ca.de_curva("HD", "ARS", "cer", None) == ""
    # Ajustes sin clase (badlar/tpm/caucion/dolar_linked) o sin ejes: nada.
    assert ca.de_curva("ARS", "ARS", "badlar", None) == ""
    assert ca.de_curva("ARS", "ARS", "tpm", None) == ""
    assert ca.de_curva("ARS", "ARS", "caucion", None) == ""
    assert ca.de_curva("ARS", "ARS", "dolar_linked", None) == ""
    assert ca.de_curva("ARS", "ARS", "", None) == ""


def test_opciones_sobre_acciones_con_nomenclatura_byma():
    """§0.fn: `GFG C 4600 DI` es un call de Galicia; V es put. Medido sobre la
    base antes de escribirlo: 13 sin clase, todas con esta forma."""
    assert ca.de_derivado("DERIVADOS", "[GFGC4600DI]", "GFGC4600DI") == ca.CALL
    assert ca.de_derivado("DERIVADOS", "", "GFGV6600AB") == ca.PUT
    assert ca.de_derivado("DERIVADOS", "[COMC55.0AB]", "COMC55.0AB") == ca.CALL
    assert ca.de_derivado("DERIVADOS", "[METC2900FE]", "") == ca.CALL
    # Mes que no es un código BYMA: no se adivina.
    assert ca.de_derivado("DERIVADOS", "[GFGC4600XX]", "GFGC4600XX") == ""
    # Fuera de la cartera DERIVADOS, la forma no alcanza.
    assert ca.de_derivado("RENTA VARIABLE", "[GFGC4600DI]", "GFGC4600DI") == ""
    # Y `de_futuro` no la pisa: es una opción.
    assert ca.es_opcion("[GFGC4600DI]", "") and ca.de_futuro("DERIVADOS", "[GFGC4600DI]", "") == ""
    # Un futuro de dólar o de agro no tiene la forma: no es opción.
    assert not ca.es_opcion("[DLR102026]", "DLR102026") and not ca.es_opcion("[SOJ.ROS/MAY27]", "")
