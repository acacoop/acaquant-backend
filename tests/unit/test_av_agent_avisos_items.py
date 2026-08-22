"""LO QUE EL AGENTE MANDA TAMBIÉN ES UN OBJETO (§0.bk).

Quinta migración. Hasta acá el modelo cubría lo que el agente **encuentra**
(hallazgos, controles, centinela) y no lo que **manda**: avisos, filas de aviso
y preguntas vivían con su propio `resuelto bool` / `hecho bool` /
`estado text`, sin memoria.

Y no son otra cosa. *«A este bono le falta el CER de emisión»* es
**(qué cosa, qué le pasa)**: la misma identidad que un hallazgo. Tanto que,
cuando el detector encuentra ese mismo dato faltando, los dos terminan
escribiendo en el MISMO objeto — que es exactamente lo correcto, porque es un
solo problema visto dos veces.
"""
from __future__ import annotations

from ._fuente import codigo

# ── los AVISOS ───────────────────────────────────────────────────────────────

def test_crear_un_aviso_crea_su_objeto():
    from api.services.av_agent_vista import crear_avisos
    assert "_espejar_aviso(" in codigo(crear_avisos)


def test_avisarle_a_una_persona_crea_su_objeto():
    from api.services.av_agent_vista import avisar_a
    assert "_espejar_aviso(" in codigo(avisar_a)


def test_cerrar_un_aviso_cierra_su_objeto():
    from api.services.av_agent_vista import resolver_aviso, resolver_aviso_propio
    assert "_cerrar_aviso(" in codigo(resolver_aviso)
    assert "_cerrar_aviso(" in codigo(resolver_aviso_propio)


def test_reabrir_es_VOLVIO_y_no_borra_la_vuelta():
    """El vocabulario solo deja salir de `resuelto` por `volvio`, y además es lo
    que pasó: alguien lo dio por hecho y el pendiente sigue. Marcarlo `nuevo`
    borraría esa vuelta, que es justo lo que el seguimiento tiene que contar."""
    from api.services.av_agent_vista import _cerrar_aviso
    src = codigo(_cerrar_aviso)
    assert "ciclo.VOLVIO if reabrir else ciclo.RESUELTO" in src


def test_el_espejo_NUNCA_puede_tumbar_la_lista_de_pendientes():
    """Es memoria, no es la lista. Si el objeto no se puede escribir, el aviso
    tiene que existir igual — al revés sería cambiar una funcionalidad que anda
    por una que estamos estrenando."""
    from api.services.av_agent_vista import _cerrar_aviso, _espejar_aviso
    for f in (_espejar_aviso, _cerrar_aviso):
        assert "except Exception" in codigo(f)


def test_el_mensaje_sin_ticker_no_colapsa_con_los_demas():
    """Sin sujeto, todos los mensajes de un mismo tema serían UN objeto y se
    taparían entre ellos. El destinatario hace de sujeto."""
    from api.services.av_agent_vista import _sujeto_aviso
    assert _sujeto_aviso("AL30", "x@y.com") == "AL30"
    assert _sujeto_aviso("", "X@Y.com") == "x@y.com"
    assert _sujeto_aviso("", "") == ""


def test_el_aviso_y_el_hallazgo_del_MISMO_dato_son_UN_objeto():
    """La prueba de que la identidad es (sujeto, causa) y no quién lo vio: el
    alta deja el aviso `cer_emision` sobre AL30 y el detector nocturno emite la
    misma causa sobre el mismo bono. **Un problema, un objeto.**"""
    from api.services.av_agent_items import clave_de_problema
    assert (clave_de_problema("AL30", "cer_emision", "aviso")
            == clave_de_problema("AL30", "cer_emision", "soberanos"))


# ── las FILAS de un aviso ────────────────────────────────────────────────────

def test_cada_fila_del_mensaje_es_su_propio_objeto():
    """Espejar el aviso entero no alcanza: se rearma en cada corrida, así que
    nunca podría decir «esta cuenta lleva CUATRO DÍAS descubierta»."""
    from api.services.av_agent_mensajes import enviar_tabla
    assert "_espejar_filas(" in codigo(enviar_tabla)


def test_tildar_una_fila_cierra_su_objeto_y_destildar_lo_devuelve():
    from api.services.av_agent_mensajes import _cerrar_fila, marcar_item
    assert "_cerrar_fila(" in codigo(marcar_item)
    assert "ciclo.RESUELTO if hecho else ciclo.VOLVIO" in codigo(_cerrar_fila)


def test_el_espejo_de_las_filas_tampoco_puede_tumbar_el_envio():
    from api.services.av_agent_mensajes import _cerrar_fila, _espejar_filas
    for f in (_espejar_filas, _cerrar_fila):
        assert "except Exception" in codigo(f)


# ── las PREGUNTAS ────────────────────────────────────────────────────────────

def test_registrar_una_pregunta_crea_su_objeto():
    from api.services.av_agent_preguntas import registrar
    assert "_espejar_pregunta(" in codigo(registrar)


def test_responder_cierra_su_objeto():
    from api.services.av_agent_preguntas import responder
    assert "_cerrar_pregunta(" in codigo(responder)


def test_la_identidad_NO_sale_de_partir_la_clave():
    """⚠️ La clave es `falta:AL30` y separarla con un `split(':')` sería trivial
    — y ataría la identidad a una convención de texto (REGLA #9). El día que un
    sujeto traiga `:` adentro partiría mal, en silencio."""
    from api.services import av_agent_preguntas as p
    src = codigo(p._espejar_pregunta) + codigo(p._cerrar_pregunta)
    assert "split" not in src
    assert 'q.get("sujeto")' in src and 'q.get("causa")' in src


def test_una_pregunta_sin_identidad_NO_se_espeja():
    """Prefiero que le falte la memoria a que la tenga mal: un objeto con el
    sujeto equivocado es peor que no tenerlo, porque se lee como cierto."""
    from api.services.av_agent_preguntas import _cerrar_pregunta, _espejar_pregunta
    # No toca la base: sale antes de importar el store.
    _espejar_pregunta({"clave": "x", "pregunta": "?"})
    _cerrar_pregunta({"clave": "x"})


def test_las_preguntas_que_se_generan_declaran_sujeto_y_causa():
    """Si el constructor no los pone, el espejo se saltea en silencio y la
    migración queda escrita pero apagada."""
    from api.services.av_agent_preguntas import preguntas_de_hallazgos
    qs = preguntas_de_hallazgos([
        {"tipo": "falta_en_base", "ticker": "TZXD8", "evidencia": {}},
        {"tipo": "sin_curva", "ticker": "AL30",
         "evidencia": {"ajuste": "badlar", "tickers": ["AL30"]}},
    ])
    assert qs, "el fixture dejó de generar preguntas"
    for q in qs:
        assert q.get("sujeto") and q.get("causa"), q["clave"]


def test_la_identidad_se_PERSISTE_o_responder_no_puede_cerrar_nada():
    """`responder()` lee la fila de la base, no el dict original. Si `sujeto` y
    `causa` no fueran columnas, cerraría el objeto de nadie."""
    from api.services import av_agent_preguntas as p
    assert "sujeto" in p._COLS and "causa" in p._COLS
    assert "sujeto, causa" in codigo(p.registrar)


# ── el avance, contado sin mentir ────────────────────────────────────────────

def test_las_que_espejan_estan_DECLARADAS_y_no_adivinadas():
    """El conteo sale de un campo (`Forma.espeja`) y no de buscar la palabra
    «espeja» adentro del texto: una barra de progreso que se calcula leyendo un
    comentario miente el día que alguien reescribe el comentario."""
    from core import ciclo
    assert set(ciclo.espejan()) == {
        "agente.av_agent_centinela", "manager.controles_datos",
        "agente.av_agent_avisos", "agente.av_agent_aviso_items",
        "agente.av_agent_preguntas", "agente.av_agent_ignorados",
    }
    assert all(t in ciclo.sin_migrar() for t in ciclo.espejan()), (
        "espejar NO es haber migrado: la columna vieja sigue ahí")


# ── el «no me interesa», también en los objetos ──────────────────────────────

def test_ignorar_un_ticker_apaga_TODOS_sus_objetos():
    """Sin esto, `av_agent_ignorados` sacaba el hallazgo de la pantalla pero el
    objeto seguía abierto sumando días. La pantalla decía «no hay nada» y el
    contador «lleva 20 días»: dos verdades sobre lo mismo."""
    from api.services import av_agent_preguntas as p
    assert "_ignorar_objetos(" in codigo(p.ignorar)
    assert "_ignorar_objetos(" in codigo(p.designorar)
    # También la otra puerta: ignorar contestando la pregunta `falta:<TICKER>`.
    assert "_ignorar_objetos(" in codigo(p._aplicar_efecto)


def test_ignorar_es_por_SUJETO_y_no_por_causa():
    """Si un papel no interesa, no interesa en ninguna de sus formas: ver que
    «le faltan los flujos» a un bono que ya descartaste es el mismo ruido con
    otro nombre. Es la misma regla que ya aplicaba `av_agent_ignorados`."""
    from api.services.av_agent_items import ignorar_sujeto
    src = codigo(ignorar_sujeto)
    assert "lower(sujeto) = lower(%s)" in src
    assert "regla" not in src


def test_ignorar_NO_pisa_los_resueltos():
    """Ignorar es «no me lo muestres más», no «borrá su historia»: un arreglo en
    seguimiento tiene que seguir contando sus hitos."""
    from api.services.av_agent_items import ignorar_sujeto
    from core import ciclo
    src = codigo(ignorar_sujeto)
    assert "ciclo.RESUELTO, ciclo.IGNORADO" in src
    # Y desandar solo devuelve lo que estaba ignorado, nunca revive otro cierre.
    assert "[ciclo.IGNORADO] if desandar" in src
    assert ciclo.puede_pasar(ciclo.IGNORADO, ciclo.NUEVO)


# ── lo que NO se migra, y por qué ────────────────────────────────────────────

def test_hay_tablas_con_estado_que_NO_son_problemas():
    """⚠️ La lista de deuda las metía a todas en la misma bolsa: sobrestimaba el
    trabajo y, peor, apuntaba a un objetivo equivocado. Un run, una acción, el
    termómetro de Aunesa y el acuse de recibo de un admin tienen estado y no son
    problemas — migrarlos convertiría al modelo en un cajón."""
    from core import ciclo
    clases = {f.tabla: f.clase for f in ciclo.REGISTRO}
    assert clases["agente.av_agent_runs"] == "bitacora"
    assert clases["agente.av_agent_propuestas"] == "bitacora"
    assert clases["agente.av_agent_seguimiento"] == "meta"
    assert clases["manager.proveedor_estado"] == "sensor"
    assert clases["manager.salud_vistos"] == "acuse"
    for t in ("agente.av_agent_runs", "manager.proveedor_estado",
              "manager.salud_vistos"):
        assert t not in ciclo.deuda_de_problemas()


def test_el_acuse_es_POR_PERSONA_y_por_eso_no_entra():
    """`salud_vistos` es (email, evento). `av_agent_items.visto_at` es uno solo
    para todos: migrarla haría que el segundo admin no viera nunca el modal que
    cerró el primero. Perder un permiso o un aviso por una migración de modelo
    es exactamente lo que no puede pasar."""
    from api.services import salud
    assert "_T_VISTOS" in codigo(salud.marcar_vistos)
    assert "email" in codigo(salud.marcar_vistos)


def test_TODO_lo_que_es_un_problema_ya_tiene_objeto():
    """El hito de esta tanda. No dice «migrado» —las columnas viejas siguen—
    dice que ya no queda un problema sin memoria."""
    from core import ciclo
    crudas = [t for t in ciclo.deuda_de_problemas() if t not in ciclo.espejan()]
    assert crudas == [], f"sin objeto todavía: {crudas}"


# ── el nombre legible, que ya estaba escrito (§0.bq) ─────────────────────────

def test_el_titulo_del_control_sale_del_CATALOGO_y_no_del_id():
    """⚠️ `salud._chequeos_controles` armaba el título con
    `cid.replace("_", " ")` — fabricaba uno feo teniendo el bueno al lado. En
    una columna angosta el ID se cortaba (`control:comitentes_sin_nive…`) y la
    fila dejaba de decir qué es. **No hacía falta un LLM: hacía falta leer el
    campo.**"""
    from api.services.salud import _titulo_control
    assert _titulo_control("patas_dolar_sin_pedir") == "Patas en dólares que nadie pide"
    assert _titulo_control("comitentes_sin_nivel1") == "Comitentes activos sin nivel 1"


def test_un_control_que_no_esta_en_el_catalogo_NO_rompe_la_pantalla():
    """Puede pasar con uno viejo cuya fila sigue en la tabla. Un nombre feo es
    mejor que una fila sin nombre."""
    from api.services.salud import _titulo_control
    assert _titulo_control("no_existe_este") == "no existe este"


def test_el_nombre_para_la_PANTALLA_lo_decide_el_backend():
    """Si lo resolviera cada vista, dos pantallas nombrarían distinto al mismo
    hallazgo — que es REGLA #9 aplicada a un texto."""
    from api.services.av_agent_vista import vista
    src = codigo(vista)
    assert 'h["nombre"]' in src
    assert '"titulo"' in src
