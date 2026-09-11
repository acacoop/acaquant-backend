"""EL ASISTENTE NO VE LA BASE ENTERA.

El asistente consulta datos del negocio —tenencias, valuaciones, cuentas— y lo
hace a pedido de un modelo, que decide solo qué herramienta usar y con qué
argumentos. Eso quiere decir que el alcance NO puede depender de que el que
escribe una herramienta se acuerde de filtrar: se acuerda las primeras veces.

Estos tests congelan las dos garantías:

  1. Toda consulta que toque datos de cuentas lleva el filtro de permiso.
  2. Sin cuentas habilitadas no se muestra NADA (y no "todo").
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
FUENTE = (RAIZ / "asistente" / "herramientas.py").read_text(encoding="utf-8")

# Las tablas que tienen datos de una cuenta. Una consulta que las nombre y no
# filtre está mostrando cuentas que nadie habilitó.
TABLAS_DE_CUENTAS = ("portafolio.tenencia", "portafolio.tenencia_live",
                     "operaciones.operaciones", "valuaciones.portfolio_snapshot")


def _consultas(src: str) -> list[str]:
    """Los bloques de SQL del archivo: todo lo que va entre triples comillas y
    tiene un SELECT adentro."""
    return [b for b in re.findall(r'"""(.*?)"""', src, re.S)
            if re.search(r"\bSELECT\b", b, re.I)]


def test_toda_consulta_a_datos_de_cuentas_lleva_el_permiso():
    """⚠️ **LA GARANTÍA PRINCIPAL.** Si una herramienta nueva consulta
    tenencias y se olvida del filtro, devuelve la cartera de TODA la casa — y
    no falla: contesta, con números que parecen buenos.

    Se mira el texto del SQL y no el resultado a propósito: probar el resultado
    necesitaría una base con datos de varias cuentas, y esto tiene que fallar
    en CI, antes de que la consulta llegue a correr una sola vez.
    """
    from asistente import permitido

    # Vale de las dos formas: el filtro escrito tal cual, o interpolado desde
    # la constante en un f-string. La segunda es incluso mejor —no se puede
    # desincronizar de `permitido.py`—, y es la que usa el código hoy.
    #
    # (Este test se cazó a sí mismo la primera vez: buscaba sólo el valor
    # expandido y el código usa `f"""…{permitido.FILTRO_SQL}…"""`, así que en
    # el fuente está el nombre, no el valor.)
    formas = (permitido.FILTRO_SQL, "{permitido.FILTRO_SQL}")

    for sql in _consultas(FUENTE):
        toca = [t for t in TABLAS_DE_CUENTAS if t in sql]
        if not toca:
            continue
        assert any(f in sql for f in formas), (
            f"esta consulta lee {toca} y NO lleva `permitido.FILTRO_SQL`:\n"
            f"{sql.strip()[:300]}…")


def test_sin_cuentas_habilitadas_no_se_muestra_NADA():
    """No se muestra todo: no se muestra nada, y se dice por qué.

    Es la diferencia entre un permiso y un filtro. Un filtro que no está
    configurado deja pasar todo; un permiso que falta corta.
    """
    # Con la lista vacía, `parametros()` LEVANTA — no devuelve un filtro que
    # matchee todo, que es el error que este test existe para impedir.
    import os

    from asistente import permitido
    antes = os.environ.get(permitido.CLAVE_ENV)
    try:
        os.environ[permitido.CLAVE_ENV] = ""
        assert permitido.cuentas() == []
        with pytest.raises(permitido.SinPermiso):
            permitido.parametros()
        # Y el mensaje que le llega al modelo le prohíbe estimar.
        e = permitido.como_error()
        assert e["error"] and "NO estimes" in e["que_hacer"]
    finally:
        if antes is None:
            os.environ.pop(permitido.CLAVE_ENV, None)
        else:
            os.environ[permitido.CLAVE_ENV] = antes


def test_el_permiso_se_declara_FUERA_del_codigo():
    """Las cuentas van en el `.env` del Droplet, no en un archivo del repo.

    Dos motivos: los números de cuenta no quedan en la historia de git para
    siempre, y para ampliarlos hay que entrar al servidor — el mismo nivel de
    acceso que hace falta para correr el asistente.
    """
    fuente = (RAIZ / "asistente" / "permitido.py").read_text(encoding="utf-8")
    assert 'os.getenv(CLAVE_ENV' in fuente, "el permiso tiene que salir del entorno"
    # Y ninguna cuenta escrita a mano: un id de cuenta en el código es una
    # cuenta que alguien puede ver sin haberla habilitado.
    assert not re.search(r'CUENTAS\s*=\s*[\[(]\s*["\']', fuente), (
        "hay una lista de cuentas escrita en el código")


def test_el_resultado_dice_QUE_cuentas_miro():
    """«Te vencen 29 bonos» sin decir de qué cuentas se lee como si fuera toda
    la casa. El alcance viaja con el dato, igual que la moneda."""
    assert '"cuentas_miradas"' in FUENTE
    # Y el docstring de la herramienta —que es lo que lee el modelo— lo dice.
    from asistente.herramientas import bonos_que_vencen
    doc = bonos_que_vencen.__doc__ or ""
    assert "cuentas habilitadas" in doc and "cuentas_miradas" in doc
