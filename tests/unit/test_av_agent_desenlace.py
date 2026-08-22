"""La cadena tiene un CICLO y se marca dónde se trabó (§0.bx).

El caso que lo motivó es BPOD7: 20 pasos, encabezado «✘ BLOQUEADO», y doce
renglones más abajo la conclusión decía «no se detecta nada roto». Lo que se
congela acá es que esas dos frases **no puedan volver a contradecirse**.
"""
from api.services.av_agent_alta import (
    APRENDER,
    BLOQUEA,
    CONTEXTO,
    INFO,
    NO_SE,
    OK,
    PRUEBA,
    REVISAR,
    VEREDICTO,
    _desenlace,
    _paso,
    _veredicto,
)


class TestCapa:
    def test_se_deriva_del_estado(self):
        """Un paso nuevo NACE clasificado. Sin derivación caería en la bolsa
        común y la pantalla volvería a mezclar pruebas con contexto."""
        assert _paso("x", "t", OK, "d")["capa"] == PRUEBA
        assert _paso("x", "t", BLOQUEA, "d")["capa"] == PRUEBA
        assert _paso("x", "t", REVISAR, "d")["capa"] == PRUEBA
        assert _paso("x", "t", NO_SE, "d")["capa"] == PRUEBA
        # `info` no juzga nada: describe.
        assert _paso("x", "t", INFO, "d")["capa"] == CONTEXTO

    def test_lo_que_no_se_puede_adivinar_se_declara(self):
        # Una lección es `info` igual que un contexto: la única diferencia la
        # sabe quien la crea.
        assert _paso("l", "t", INFO, "d", capa=APRENDER)["capa"] == APRENDER
        assert _paso("v", "t", OK, "d", capa=VEREDICTO)["capa"] == VEREDICTO


class TestDesenlace:
    def test_la_traba_es_la_PRIMERA_que_no_pasa(self):
        pasos = [_paso("a", "A", OK, ""), _paso("b", "B", REVISAR, ""),
                 _paso("c", "C", BLOQUEA, "")]
        d = _desenlace(pasos)
        assert d["traba"] == "b", "la primera en el orden de la cadena, no la peor"

    def test_el_contexto_y_las_lecciones_no_traban_nada(self):
        pasos = [_paso("ctx", "C", INFO, ""),
                 _paso("lec", "L", INFO, "", capa=APRENDER),
                 _paso("a", "A", OK, "")]
        assert _desenlace(pasos)["clase"] == "listo"

    def test_probado_BIEN_gana_sobre_cualquier_ambar(self):
        """El caso BPOD7. Si se comprobó que el bono está bien, un `revisar`
        suelto no puede hacer que la pantalla anuncie un problema."""
        pasos = [_paso("r", "algo", REVISAR, ""),
                 _paso("cotejo_hoy", "El bono de HOY, contra 1816", BLOQUEA, "",
                       nada_que_hacer=True)]
        d = _desenlace(pasos)
        assert d["clase"] == "viejo"
        assert "VIEJO" in d["titulo"]
        assert d["traba"] == "cotejo_hoy"

    def test_no_se_pudo_verificar_NO_es_esta_bien(self):
        d = _desenlace([_paso("x", "1816", NO_SE, "")])
        assert d["clase"] == "no_se"
        assert "no se sabe" in d["que_hacer"]

    def test_roto_de_verdad_dice_donde(self):
        d = _desenlace([_paso("ok", "A", OK, ""),
                        _paso("cuadro", "El cuadro", BLOQUEA, "")])
        assert d["clase"] == "roto"
        assert "El cuadro" in d["titulo"]


class TestVeredictoYDesenlaceNoSeContradicen:
    def test_bloquea_porque_esta_bien_no_se_anuncia_como_falla(self):
        """⚠️ El bug exacto de BPOD7: «NO se puede aplicar — 1 paso lo bloquea»
        arriba de un bono que coincide con 1816 al bps."""
        v = _veredicto([_paso("cotejo_hoy", "contra 1816", BLOQUEA, "",
                              nada_que_hacer=True)])
        assert v["puede_aplicar"] is False, "sigue sin poder escribirse, y está bien"
        assert "lo bloquean" not in v["texto"]
        assert "quedó viejo" in v["texto"]
        assert v["desenlace"]["clase"] == "viejo"

    def test_el_veredicto_siempre_trae_desenlace_y_conteo_por_capa(self):
        v = _veredicto([_paso("a", "A", OK, ""), _paso("c", "C", INFO, ""),
                        _paso("l", "L", INFO, "", capa=APRENDER)])
        assert v["desenlace"]["clase"] == "listo"
        assert v["conteo"]["prueba"] == 1
        assert v["conteo"]["contexto"] == 1
        assert v["conteo"]["aprender"] == 1

    def test_una_cadena_vacia_no_levanta(self):
        v = _veredicto([])
        assert v["desenlace"]["clase"] == "listo"


class TestElCensoNoPuedeDecirQueHayBotonSiNoLoHay:
    """⚠️ **La mentira más cara del censo** (2026-08-22). Decía «79 ya tienen
    acción → apretar el botón» cuando solo ~30 tenían un arreglo aplicable en
    lote. Los otros 47 declaran `accion="arreglo"`, que **no es una acción**:
    es el MODO del panel con IA, caso por caso.

    El user corrió `agente_aplicar` tres veces, no bajó nada, y no había forma
    de saber por qué — el script solo conoce las 9 de `ACCIONES`.

    Tres vocabularios para la misma pregunta: `accion_de()` devuelve un MODO,
    `ACCIONES` tiene ARREGLOS ejecutables, y el censo trataba al primero como
    si fuera el segundo."""

    def test_arreglo_es_un_modo_y_NO_una_accion_registrada(self):
        from api.services.av_agent_hacer import ACCIONES
        assert "arreglo" not in ACCIONES, (
            "si «arreglo» se vuelve una acción registrada, revisá el censo: "
            "47 hallazgos se mueven de pila")

    def test_el_censo_deriva_del_registro_y_no_de_una_lista(self):
        import inspect

        from scripts import diag_encontro
        src = inspect.getsource(diag_encontro._accion_registrada)
        assert "ACCIONES" in src and "POR_CONTROL" in src, (
            "tiene que salir del registro real: si mañana alguien escribe la "
            "acción de sin_ejes, esos 9 se mueven de pila solos")

    def test_los_MODOS_aplicables_los_conoce_el_censo_desde_UNA_lista(self):
        """⚠️ Segunda mitad del mismo bug: `Accion` NO es el único mecanismo de
        lote. El modo `arreglo` tiene su propio par simular/aplicar y
        `agente_aplicar` lo corre — así que los 45 hallazgos que el censo mandó
        a «abrir de a uno en la pantalla» sí se pueden aplicar en lote.

        La lista vive UNA vez, en el script que los ejecuta. Copiarla al censo
        volvería a mandar al usuario a correr un comando que no hace nada."""
        import inspect

        from scripts import agente_aplicar, diag_encontro
        assert "arreglo" in agente_aplicar._MODOS
        src = inspect.getsource(diag_encontro._accion_registrada)
        assert "from scripts.agente_aplicar import _MODOS" in src, (
            "el censo tiene que leer la lista del que ejecuta, no tener la suya")

    def test_el_lote_de_arreglo_no_re_decide_si_puede_aplicar(self):
        """`aplicar_arreglo` vuelve a simular adentro y se niega solo. Si el
        script tuviera su propio gate sería el cuarto criterio contradiciéndose
        con los otros tres — el patrón que costó toda esta sesión."""
        import inspect

        from scripts import agente_aplicar
        src = inspect.getsource(agente_aplicar._correr_modo)
        assert "aplicar_arreglo" in src
        assert "av_agent_alta.BLOQUEA" in src, (
            "la constante, no el string: una copia dejaría de encontrar los "
            "bloqueos sin fallar")

    def test_las_dos_pilas_existen_y_no_se_confunden(self):
        import inspect

        from scripts import diag_encontro
        src = inspect.getsource(diag_encontro._clasificar)
        assert "APLICABLE_EN_LOTE" in src
        assert "UNO_POR_UNO" in src
        assert "TIENE_PUERTA" not in src, (
            "esa pila mezclaba las dos preguntas: «¿hay modo de pantalla?» y "
            "«¿se puede aplicar en lote?»")


class TestElDryRunTieneQuePoderDECIDIRSE:
    """⚠️ El primer dry-run de `--accion arreglo` salió inservible de dos formas
    a la vez, y las dos eran de presentación:

      · los 4 aplicables mostraban **`→ —`** (sus ejes ya estaban bien; lo que
        el arreglo corrige es la escala o el CER, que no se mostraban);
      · los 38 trabados mostraban el **título** del chequeo — «La métrica vuelve
        al rango»— que se lee como que PASÓ.

    Un dry-run que no deja decidir es peor que no tenerlo: invita a aplicar a
    ciegas justo en la única puerta que pisa datos existentes."""

    def test_el_motivo_sale_del_detalle_y_no_del_titulo(self):
        import inspect

        from scripts import agente_aplicar
        src = inspect.getsource(agente_aplicar._correr_modo)
        assert "c.get('detalle')" in src or 'c.get("detalle")' in src, (
            "el título dice qué se EXIGE; el detalle dice qué se ENCONTRÓ")

    def test_los_motivos_se_agrupan_normalizando_los_numeros(self):
        """20 bonos esperando lo mismo es UN problema; contados de a uno
        parecen veinte."""
        from scripts.agente_aplicar import _motivo_corto
        a = _motivo_corto("TEA 41,2% fuera del rango 3%-15%")
        b = _motivo_corto("TEA 38,9% fuera del rango 3%-15%")
        assert a == b, "dos casos de la misma traba tienen que agrupar juntos"
        assert _motivo_corto("sin precio") != a

    def test_que_cambia_nunca_miente_por_omision(self):
        """Si el simulador no declara ningún cambio, el dry-run lo dice — no
        muestra una línea vacía que parece «no pasa nada»."""
        from scripts.agente_aplicar import _que_cambia
        assert "NO aplicar" in _que_cambia({})
        assert "SIN EJES" in _que_cambia(
            {"ejes_hoy": None, "ejes_propuestos": {"emisor_tipo": "soberano"}})
        # Ejes iguales no se anuncian como cambio, pero la escala sí.
        salida = _que_cambia({"ejes_hoy": {"a": 1}, "ejes_propuestos": {"a": 1},
                              "escala": "pct"})
        assert "ejes" not in salida and "escala pct" in salida
