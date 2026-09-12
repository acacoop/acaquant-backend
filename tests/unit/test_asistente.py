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
                     "operaciones.operaciones", "valuaciones.portfolio_snapshot",
                     "operaciones.acreencias", "clientes.cuentas")


def _cuerpo(nombre: str) -> str:
    """El código de UNA función del archivo, hasta la siguiente. Cortar por
    caracteres se pisa con la función de al lado — y el test pasa a medir otra
    cosa sin que nadie se entere."""
    resto = FUENTE.split(f"\ndef {nombre}(", 1)[1]
    return resto.split("\ndef ", 1)[0]


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
    # Y el docstring de CADA herramienta que devuelve datos del negocio —que es
    # lo que lee el modelo— lo dice. Se recorre la lista real de herramientas:
    # una que se sume mañana y no lo diga cae acá, no dentro de un año.
    from asistente import herramientas as H
    for fn in H.DISPONIBLES:
        doc = fn.__doc__ or ""
        if "cuentas_miradas" not in _cuerpo(fn.__name__):
            continue  # no devuelve datos de cartera (p.ej. el listado de cuentas)
        assert "cuentas_miradas" in doc, (
            f"`{fn.__name__}` devuelve `cuentas_miradas` y su docstring no le "
            "dice al modelo que lo aclare al contestar")


# ── LO QUE SALIÓ MAL EN LA PRIMERA CORRIDA REAL (2026-09-11) ────────────────


def test_la_lista_de_SIN_VENCIMIENTO_trae_solo_bonos():
    """⚠️ El bug: con `LEFT JOIN` contra el catálogo de renta fija, la lista de
    «bonos sin vencimiento cargado» se llenaba con todo lo que no matcheara.

    En la primera corrida real devolvió ARS, USD y USDC (efectivo), MSFT y RKLB
    (acciones) e IBIT y ETHA (ETFs). Y el modelo lo repitió: «sin vencimiento
    cargado: ARS, ETHA, IBIT…». Eso es FALSO — no les falta el dato, es que no
    son bonos. Y es la peor clase de error: no falla, contesta.

    `mercado.curvas` ES el catálogo de renta fija, así que estar adentro es la
    definición de «es un bono». Con INNER JOIN el problema no puede volver, y
    no hace falta una lista de clases de activo que alguien mantenga al día.
    """
    sin_vto = [s for s in _consultas(FUENTE) if "fecha_vencimiento IS NULL" in s]
    assert sin_vto, "no encontré la consulta de los que no tienen vencimiento"
    for sql in sin_vto:
        assert "LEFT JOIN mercado.curvas" not in sql, (
            "vuelve el LEFT JOIN: el efectivo y las acciones se van a colar en "
            "la lista de bonos con la ficha incompleta")
        assert re.search(r"\bJOIN\s+mercado\.curvas\b", sql), (
            "la consulta tiene que cruzar contra el catálogo de renta fija")


def test_un_numero_de_plata_nunca_viaja_sin_su_moneda():
    """Varias filas de `portafolio.tenencia` vienen con `moneda` en null. Con un
    null el modelo simplemente omitía el dato y mostraba la valuación sola —
    un número de plata que no se puede comparar con ningún otro.

    Es la misma regla que la fecha de la foto y las cuentas miradas: **la
    unidad viaja con el dato**. Si no se sabe, se dice que no se sabe.
    """
    assert 'f["moneda"] or "SIN DATO' in FUENTE, (
        "la moneda vacía tiene que decirse, no mandarse como null")
    from asistente.herramientas import bonos_que_vencen
    doc = bonos_que_vencen.__doc__ or ""
    assert "SIN DATO en la tenencia" in doc, (
        "el modelo tiene que saber qué hacer cuando la moneda no está")


# ── COBROS FUTUROS — la plata que entra (2026-09-12) ────────────────────────


def test_la_plata_nunca_se_totaliza_mezclando_monedas():
    """⚠️ **LA GARANTÍA DE `cobros_futuros`.** La respuesta a «¿cuánto cobro?»
    es un diccionario por moneda (`{"ARS": …, "USD": …}`), no un número.

    No es cosmética: si existiera UN total, el modelo podría decirlo, y ese
    número sería pesos sumados con dólares. Al no existir, decir algo mal
    requiere que el modelo invente — que es mucho menos probable que repetir un
    campo que le dieron.

    Lo mismo del lado del SQL: la moneda va en el GROUP BY, así que la base
    nunca colapsa dos monedas en una fila.
    """
    from asistente.herramientas import cobros_futuros

    doc = cobros_futuros.__doc__ or ""
    assert "SEPARADO POR MONEDA" in doc and "NUNCA sumes" in doc, (
        "el docstring tiene que prohibirle al modelo sumar monedas: es lo "
        "único que lee antes de contestar")

    cuerpo = _cuerpo("cobros_futuros")
    for sql in _consultas(cuerpo):
        if "operaciones.acreencias" not in sql or "sum(t.monto)" not in sql:
            continue
        assert "moneda" in sql.lower().split("group by")[1].split("order by")[0], (
            "una suma de plata sin la moneda en el GROUP BY mezcla ARS con USD "
            f"y el resultado parece bueno:\n{sql.strip()[:300]}…")


def test_una_cuenta_pedida_se_INTERSECA_con_el_permiso():
    """El modelo elige el argumento `cuenta`, y puede inventarlo.

    Si ese argumento REEMPLAZARA al permiso, alcanzaría con que el modelo
    escribiera un número para leer una cuenta que nadie habilitó. Y si se
    IGNORARA en silencio, el modelo pediría la cuenta A y le contestaríamos con
    la B — peor todavía, porque contesta seguro.

    Las dos cosas se evitan igual: la cuenta pedida tiene que estar en el
    permiso, y si no está se dice y se corta.
    """
    cuerpo = _cuerpo("cobros_futuros")
    assert "if pedida not in permitido.cuentas():" in cuerpo, (
        "la cuenta pedida tiene que validarse contra el permiso")
    i = cuerpo.index("if pedida not in permitido.cuentas():")
    # Y el rechazo va ANTES de pisar el parámetro de la consulta.
    assert i < cuerpo.index('params["cuentas_permitidas"] = [pedida]')


def test_el_total_no_sale_de_la_lista_que_se_puede_truncar():
    """⚠️ El detalle `pagos` tiene techo (`MAX_PAGOS`). El total NO puede salir
    de sumarlo: el día que una cartera pase el techo, el total daría de menos y
    **nada fallaría** — la respuesta seguiría siendo un número redondo y
    plausible.

    Por eso el total y el calendario salen de su propia consulta sobre el
    período entero, y el corte se declara en `truncado`.
    """
    from asistente import herramientas as H

    assert H.MAX_PAGOS > 0
    cuerpo = _cuerpo("cobros_futuros")
    assert '"truncado": len(filas) > MAX_PAGOS' in cuerpo, (
        "si se corta la lista hay que decirlo")
    # El total se arma desde `meses` (la consulta del período entero), no desde
    # `pagos` (la lista recortada).
    i = cuerpo.index("for mes, moneda, monto in meses:")
    j = cuerpo.index("pagos = [{")
    assert i < j, "el total se calcula antes y aparte del detalle"
    assert "total[m]" in cuerpo[i:j]


def test_lo_que_NO_proyecta_ningun_cobro_se_declara():
    """Un bono que tenés y al que le falta el cronograma no genera ninguna fila
    en `operaciones.acreencias`. Sin decirlo, desaparece de la respuesta y el
    total se lee como completo cuando está corto.

    Es la misma regla que `sin_vencimiento_cargado`: el silencio se declara.
    """
    from asistente.herramientas import cobros_futuros

    assert '"sin_proyeccion"' in FUENTE
    doc = cobros_futuros.__doc__ or ""
    assert "sin_proyeccion" in doc, (
        "el modelo tiene que saber que esa lista significa «el total está corto»")


def test_la_respuesta_dice_de_cuando_son_los_datos():
    """`operaciones.acreencias` la recalcula un cron una vez por día. «Vas a
    cobrar X» sobre una foto de hace cuatro días es otra respuesta.

    Son DOS fechas y no una: cuándo se sacó la foto de cartera
    (`tenencia_del`) y cuándo corrió el cálculo (`calculado_el`). Una compra de
    ayer no está en ninguna de las dos, y el modelo tiene que poder decirlo.
    """
    from asistente.herramientas import cobros_futuros

    assert '"tenencia_del"' in FUENTE and '"calculado_el"' in FUENTE
    doc = cobros_futuros.__doc__ or ""
    assert "tenencia_del" in doc


def test_la_ficha_que_ve_el_modelo_sale_de_la_funcion_y_no_de_una_lista_aparte():
    """Sumar una herramienta es agregarla a `DISPONIBLES` y nada más. Si la
    descripción viviera en una lista paralela, el día que alguien cambie el
    docstring y no la lista, el modelo elegiría con una descripción vieja — y
    no fallaría nada, elegiría mal.
    """
    from asistente import herramientas as H

    assert len(H.FICHAS) == len(H.DISPONIBLES) == len(H.POR_NOMBRE)
    import inspect
    for fn, f in zip(H.DISPONIBLES, H.FICHAS, strict=True):
        assert f["function"]["name"] == fn.__name__
        # La descripción ES el docstring, carácter por carácter. No un resumen,
        # no un texto paralelo: lo mismo que se lee abriendo la función.
        assert f["function"]["description"] == inspect.getdoc(fn), (
            f"la ficha de `{fn.__name__}` no es su docstring")
        assert f["function"]["description"], f"`{fn.__name__}` sin docstring"
        # Y los argumentos salen de la firma, con los obligatorios marcados.
        params = f["function"]["parameters"]
        firma = inspect.signature(fn).parameters
        assert set(params["properties"]) == set(firma)
        assert set(params["required"]) == {
            n for n, p in firma.items() if p.default is inspect.Parameter.empty}
