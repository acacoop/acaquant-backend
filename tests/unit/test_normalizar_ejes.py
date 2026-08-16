"""Los ejes que llegan de un editor: validación y normalización.

Hasta hoy los ejes solo los escribía un script one-shot, así que un bono dado de
alta desde Manager nacía SIN clasificar y **no aparecía en la vista de renta
fija** — sin error y sin aviso. Al abrirlos a la edición, esta función es lo único
que separa un dato bueno de uno que rompe la vista en silencio.

Las dos reglas que no son de tipeo sino de MODELO están congeladas abajo:
`ajuste_alt` no puede existir sin `ajuste`, y no puede ser igual a `ajuste`.
"""
from __future__ import annotations

import pytest

from core.curvas_ejes import normalizar_ejes


def test_normaliza_el_tipeo():
    """El operador escribe a mano: mayúsculas y espacios no pueden decidir si un
    bono entra a su tabla."""
    out = normalizar_ejes({"emisor_tipo": " Soberano ", "moneda_eje": "usd",
                           "ajuste": "FIJA", "ley": " NY "})
    assert out == {"emisor_tipo": "soberano", "moneda_eje": "USD",
                   "ajuste": "fija", "ley": "ny"}


def test_solo_devuelve_lo_que_vino():
    """Lo que el editor no manda NO se toca. Si devolviera las 5 claves siempre,
    guardar el precio de un bono le borraría la clasificación."""
    assert normalizar_ejes({"ajuste": "cer"}) == {"ajuste": "cer"}
    assert normalizar_ejes({"ticker_corto": "AL30", "curva": "soberanos"}) == {}


def test_vacio_explicito_BORRA_el_eje():
    """"Sin clasificar" es un estado válido y visible, no un error. El router hace
    `exclude_none=True`, así que la forma de limpiar un eje es mandar `""`."""
    assert normalizar_ejes({"ajuste": "", "ley": "  "}) == {"ajuste": None, "ley": None}


def test_un_valor_fuera_del_dominio_se_rechaza():
    """Ruidoso, no silencioso: un `ajuste` inventado dejaría al bono sin pill y el
    operador vería el guardado exitoso."""
    with pytest.raises(ValueError, match="ajuste inválido"):
        normalizar_ejes({"ajuste": "inflacion"})
    with pytest.raises(ValueError, match="emisor_tipo inválido"):
        normalizar_ejes({"emisor_tipo": "estado"})
    with pytest.raises(ValueError, match="moneda_eje inválido"):
        normalizar_ejes({"moneda_eje": "PESOS"})
    with pytest.raises(ValueError, match="ley inválido"):
        normalizar_ejes({"ley": "argentina"})


def test_dual_ya_no_es_un_ajuste_cargable():
    """Si se pudiera volver a tipear, el bono se escondería de sus dos tablas —
    exactamente lo que la migración vino a sacar."""
    with pytest.raises(ValueError, match="ajuste inválido"):
        normalizar_ejes({"ajuste": "dual"})


def test_un_dual_consigo_mismo_se_rechaza():
    """LA regla. Ya casi entra a la base una vez: en TMVE8/TTD26/TTS26 las dos
    fuentes decían TAMAR. El daño es MUDO — el bono se ve en una sola tabla, la
    vista suma bien, y nadie detecta que la segunda pata es basura."""
    with pytest.raises(ValueError, match="no puede ser igual"):
        normalizar_ejes({"ajuste": "tamar", "ajuste_alt": "tamar"})
    # y no se escapa por el tipeo
    with pytest.raises(ValueError, match="no puede ser igual"):
        normalizar_ejes({"ajuste": "cer", "ajuste_alt": " CER "})


def test_no_hay_segunda_pata_sin_primera():
    with pytest.raises(ValueError, match="sin `ajuste`"):
        normalizar_ejes({"ajuste": "", "ajuste_alt": "tamar"})


def test_un_dual_valido_pasa():
    assert normalizar_ejes({"ajuste": "cer", "ajuste_alt": "tamar"}) == \
           {"ajuste": "cer", "ajuste_alt": "tamar"}


def test_TMVE8_como_lo_cargaria_la_mesa():
    """Caso real: TAMAR + DOLAR LINKED, el primer bono con una pata de cada lado."""
    assert normalizar_ejes({"emisor_tipo": "soberano", "moneda_eje": "ARS",
                            "ajuste": "tamar", "ajuste_alt": "dolar_linked"}) == \
           {"emisor_tipo": "soberano", "moneda_eje": "ARS",
            "ajuste": "tamar", "ajuste_alt": "dolar_linked"}
