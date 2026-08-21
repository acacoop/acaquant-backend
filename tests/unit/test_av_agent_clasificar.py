"""«NO ENTIENDO CÓMO CLASIFICA A UNO SOLO SI HAY UN MONTÓN ASÍ.»

El user (2026-08-21), mirando la propuesta sobre 19 assets sin cartera y viendo
UNA sola sugerencia:

    QUÉ PROPONÉS
       * ✔ [OTC - DLR052027] — CARTERA = DERIVADOS

    *«No funciona bien. No entiendo cómo clasifica a uno solo como OTC si hay un
    montón así, y las commodities también, que eran derivados — y ya lo habíamos
    hablado.»*

**MEDIDO, y la causa no era la tabla de patrones.** Las unidades de Aunesa vienen
en DOS formas de corchete:

    [42932] OTC SOJ.        el corchete es un PREFIJO (el id de especie)
    [OTC - MAI.ROS/ENE27]   el corchete ENVUELVE al nombre entero

`_nombre()` solo sacaba la primera. Los patrones de contrato de cámara están
anclados con `^` —a propósito, para que un «GIR.» adentro de una razón social no
convierta un bono en derivado— y **con el `[` adelante ninguno podía matchear**.

El único que salió fue `[OTC - DLR052027]`, y **de casualidad**: pegó con
`\\bDLR\\s*\\d`, el único patrón sin ancla. Por eso salía exactamente uno.
"""
from __future__ import annotations

import re

from api.services.av_agent_hacer import _PATRONES, _nombre


def _cartera(unidad: str) -> str:
    n = _nombre(unidad)
    return next((c for p, c, _ in _PATRONES if re.search(p, n)), "")


# ── las dos formas del corchete ─────────────────────────────────────────────

def test_el_corchete_que_ENVUELVE_al_nombre_se_saca():
    assert _nombre("[OTC - MAI.ROS/ENE27]") == "OTC - MAI.ROS/ENE27"


def test_el_corchete_de_PREFIJO_tambien():
    assert _nombre("[42932] PBJ26") == "PBJ26"


def test_los_dos_juntos():
    assert _nombre("[28902] CAFCI1910-6461 - FONDO X").startswith("CAFCI1910")


# ── los 9 contratos de cámara REALES que se estaban perdiendo ───────────────

def test_TODOS_los_contratos_de_camara_se_clasifican():
    """Son los del caso real: 8 de estos 9 no salían."""
    reales = [
        "[OTC - DLR052027]", "[TRI.ROS/ENE27 212 P]", "[OTC - MAI.ROS/ENE27]",
        "[SOJ.MIN/MAY27]", "[MAI.ROS/ABR27 204 C]", "[TRI.ROS/MAR27 248 C]",
        "[TRI.ROS/ENE27 244 C]", "[OTC - TRI.ROS/ENE27 244 C]",
        "[SOJ.ROS/JUL27 372 C]",
    ]
    perdidos = [u for u in reales if _cartera(u) != "DERIVADOS"]
    assert not perdidos, f"siguen sin clasificar: {perdidos}"


def test_el_UNICO_que_salia_sigue_saliendo():
    """Salía por el patrón sin ancla (`DLR` + número), no por ser OTC."""
    assert _cartera("[OTC - DLR052027]") == "DERIVADOS"


# ── y la guarda que el `^` protegía NO se perdió ────────────────────────────

def test_un_GIR_en_el_MEDIO_de_una_razon_social_NO_es_un_derivado():
    """El ancla `^` existe por esto. Sacar el corchete no puede convertirla en
    una búsqueda libre: ahí un «GIRO» adentro de un nombre haría derivado a
    cualquier bono."""
    assert _cartera("[12345] BANCO DEL GIRO S.A. ON 2030") != "DERIVADOS"
    assert _cartera("[12345] AGROGIR. SA ON") != "DERIVADOS"


def test_un_bono_comun_sigue_sin_propuesta():
    """No inventar es parte del trabajo: sin patrón, la regla se calla y el caso
    va al modelo o a una persona."""
    assert _cartera("[59615] AEC3O") == ""
    assert _cartera("[42932] PBJ26") == ""


def test_el_FCI_por_codigo_CAFCI_sigue_andando():
    assert _cartera("[28902] CAFCI1910-6461 - FONDO PESOS") == "FCI"


# ── la CONTRAPARTE necesita su segmento ─────────────────────────────────────

def _accion():
    from api.services.av_agent_hacer import ACCIONES
    return ACCIONES["contrapartes.alta"]


def test_sin_SEGMENTO_no_se_da_de_alta():
    """El user: *«no toma en cuenta todos los casilleros… no pidió si es fondo,
    ALYC o qué»*. `BCO CREDICOOP TERCEROS` vino con segmento `None` y se dio de
    alta igual: quedó SIN CLASIFICAR, que es el hallazgo de mañana."""
    import pytest

    from api.services.av_agent_hacer import Propuesta
    p = Propuesta(sujeto="X", campo="contraparte", propuesto="Credicoop",
                  porque="", extra={"denominacion": "BANCO CREDICOOP"})
    with pytest.raises(RuntimeError, match="SEGMENTO"):
        _accion().aplicar(p)


def test_el_valor_lleva_NOMBRE_y_SEGMENTO_para_poder_corregir_los_dos():
    assert _accion()._partir("Credicoop · Bancos") == ("Credicoop", "Bancos")
    assert _accion()._partir("Credicoop | Fondos") == ("Credicoop", "Fondos")
    assert _accion()._partir("Credicoop - ALYC") == ("Credicoop", "ALYC")
    assert _accion()._partir("Credicoop") == ("Credicoop", "")


def test_verificar_exige_que_el_SEGMENTO_haya_quedado():
    """Antes solo miraba el nombre: un alta a medias salía «verificado» y el
    libro decía «contraparte = Credicoop» como si estuviera completa."""
    import inspect
    src = inspect.getsource(_accion().verificar)
    assert "segmento" in src and "SIN SEGMENTO" in src


def test_el_codigo_MAE_se_declara_pendiente_en_vez_de_quedar_en_silencio():
    """No se puede adivinar —lo asigna el MAE— pero callarlo hace que el Excel
    MAE salga con la celda vacía y nadie sepa por qué."""
    import inspect
    src = inspect.getsource(_accion().proponer)
    assert "MAE" in src


# ── el re-chequeo tiene que PISAR la cadena vieja ───────────────────────────

def test_recontrolar_devuelve_el_diagnostico_RECALCULADO():
    """El user: *«el volver a chequear dice que sí pero no corta el resto del
    mensaje ni nada, mantiene todo en vez de decir que ya está resuelto»*. El
    backend SÍ los había cerrado — lo que faltaba era devolver la cadena nueva.
    Dos verdades contradiciéndose en la misma tarjeta se leen como que el
    sistema no se enteró."""
    import inspect

    from api.services.av_agent_salud import recontrolar
    src = inspect.getsource(recontrolar)
    assert '"diagnostico"' in src and "diagnosticar(" in src
    assert '"resuelto"' in src
