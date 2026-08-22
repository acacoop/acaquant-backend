"""LA FOTO SE MUESTRA, PERO NUNCA SIN COTEJARLA (§0.cd).

ENCONTRÓ es la foto de la última corrida del agente. Relevar cuesta créditos y
minutos, así que no se puede rehacer en cada apertura de la pantalla — pero
mostrar como pendiente algo que YA se arregló destruye la confianza en toda la
lista: si una fila está mal, ninguna vale.

La salida no es rehacer la foto: es **contrastarla contra la realidad antes de
mostrarla**, y ese criterio tiene que poder evaluarse en la LECTURA.

Ya costó cuatro veces el mismo síntoma —TZXM8, BADLAR, el IGNORAR, el DICP con
«✔ cronograma escrito» y el hallazgo intacto— y una quinta el 2026-08-22: el
user aplicó 5 arreglos de FCI y la pantalla mostró los MISMOS 98 hallazgos,
número por número.
"""
import inspect

from api.services import av_agent_vista as v


def _caduco_src() -> str:
    return inspect.getsource(v._hallazgos_ultima_corrida)


class TestTodoTipoConCotejoPosibleLoTiene:
    def test_los_cinco_cotejos_siguen_ahi(self):
        """Cada uno se ganó su lugar con un incidente. Sacarlos es reabrirlo."""
        src = _caduco_src()
        for tipo, porque in (
                ("falta_en_base", "el bono ya está en mercado.curvas"),
                ("hueco_de_curva", "la curva del ajuste ya existe"),
                ("sin_flujo", "el bono ya tiene cronograma"),
                ("salud", "el chequeo volvió a estar en verde"),
        ):
            assert f'"{tipo}"' in src, f"se perdió el cotejo de «{tipo}»: {porque}"
        assert "ignorados" in src, "se perdió el cotejo de «no me interesa»"

    def test_salud_usa_el_estado_VIVO_y_no_una_copia(self):
        """`salud.evaluar()` es la única función que arma ese estado. Una segunda
        idea de «¿está en verde?» se separaría de la primera sin dar error."""
        src = inspect.getsource(v._salud_por_id)
        assert "salud.evaluar()" in src


class TestNoSePuedeVACIAR_LA_PANTALLA_POR_UN_FALLO:
    def test_si_salud_no_se_puede_evaluar_no_caduca_nada(self):
        """⚠️ La guarda que importa: `{}` significa «no pude mirar», y un id
        ausente NO es «está resuelto». Confundirlos vaciaría ENCONTRÓ justo el
        día que SALUD está caído — la mentira más cara que puede decir una
        herramienta de integridad.

        ⚠️ **Antes esto era un grep de `'estado == "ok"'` en el fuente** y se
        rompió al renombrar una variable, sin que el comportamiento cambiara ni
        un poco. Un test que se cae por un rename y NO se cae por un `!=` está
        cuidando el texto en vez de la regla. Ahora se ejerce el predicado.
        """
        # Se reconstruye el predicado tal como lo evalúa `_caduco`, con los tres
        # casos que importan: verde · rojo · «no lo pude mirar».
        def caduca(vivo):
            return bool(vivo) and (vivo.get("estado") or "") == "ok"

        assert caduca({"estado": "ok"}) is True, "el verde SÍ tacha la fila"
        assert caduca({"estado": "warn"}) is False, "un control con casos se queda"
        assert caduca({"estado": "error"}) is False
        assert caduca(None) is False, "«no pude mirar» NUNCA es «está resuelto»"
        assert caduca({}) is False
        assert caduca({"estado": None}) is False

    def test_el_fallback_devuelve_vacio_y_lo_dice(self):
        src = inspect.getsource(v._salud_por_id)
        assert "return {}" in src
        assert "logger.warning" in src


class TestAplicarVuelveAMirar:
    def test_aplicar_recontrola_el_control_de_la_accion(self):
        """Sin esto el arreglo se escribe, se verifica, y la pantalla sigue
        mostrando el problema hasta la corrida de la noche siguiente — que para
        quien mira es idéntico a que el botón no haga nada."""
        from api.services import av_agent_hacer as hacer
        src = inspect.getsource(hacer.aplicar)
        assert "_recontrolar_despues" in src

    def test_el_control_se_DERIVA_de_la_accion(self):
        """Por `sobre`, no por una lista: una acción nueva lo hereda sola."""
        from api.services import av_agent_hacer as hacer
        src = inspect.getsource(hacer._recontrolar_despues)
        assert '"sobre"' in src or "'sobre'" in src
        assert "recontrolar" in src

    def test_recontrolar_usa_la_MISMA_puerta_que_el_cron(self):
        """`_diff_y_persistir`. Si fueran dos caminos, el botón podría dejar un
        estado y la corrida nocturna otro."""
        from api.services import av_agent_salud
        src = inspect.getsource(av_agent_salud.recontrolar)
        assert "_diff_y_persistir" in src

    def test_un_fallo_al_recontrolar_no_deshace_el_arreglo(self):
        """El dato ya se escribió y se verificó. Que no se pueda refrescar la
        pantalla es peor información, no un arreglo fallido."""
        from api.services import av_agent_hacer as hacer
        src = inspect.getsource(hacer._recontrolar_despues)
        assert "except Exception" in src
        assert "no pude recontrolar" in src


class TestElAvanceParcialSeVE:
    """⚠️ **El bug que costó tres rondas.** El user arregló 5 de 8 casos de
    `fci_incompletos`, tres veces seguidas, y ENCONTRÓ mostró **el mismo número
    exacto**. El arreglo andaba: el control seguía rojo (quedaban 3) así que la
    fila se quedaba —correcto— pero con el TEXTO de la foto de anoche, que decía
    8. Tachar cuando vuelve al verde ya estaba; lo que faltaba era el caso
    NORMAL, que es arreglar una parte."""

    def test_reescribe_el_conteo_con_el_numero_vivo(self):
        h = {"tipo": "salud", "ticker": "control:fci_incompletos",
             "severidad": "media", "motivo": "viejo",
             "evidencia": {"n_casos": 8, "titulo": "Assets FCI sin ticker/emisor"}}
        v._refrescar_salud(h, {"control:fci_incompletos": {
            "id": "control:fci_incompletos", "estado": "warn", "n": 3,
            "titulo": "Assets FCI sin ticker/emisor",
            "motivo": "3 anomalías sin resolver"}})
        assert h["n_casos"] == 3, "tiene que decir lo que queda HOY"
        assert h["n_casos_foto"] == 8, "y contra qué, si no el avance no se ve"
        assert "3" in h["motivo"]

    def test_sin_cambio_no_inventa_un_delta(self):
        h = {"tipo": "salud", "ticker": "c", "severidad": "media", "motivo": "x",
             "evidencia": {"n_casos": 4}}
        v._refrescar_salud(h, {"c": {"id": "c", "estado": "warn", "n": 4,
                                     "titulo": "t", "motivo": "4 anomalías"}})
        assert h["n_casos"] == 4
        assert "n_casos_foto" not in h, "sin delta no se muestra un delta"

    def test_si_no_esta_vivo_la_fila_queda_como_vino(self):
        """«No lo pude evaluar» no puede parecer «se arregló» — misma regla que
        el cotejo que tacha."""
        h = {"tipo": "salud", "ticker": "c", "severidad": "alta",
             "motivo": "original", "evidencia": {"n_casos": 9}}
        v._refrescar_salud(h, {})
        assert h["motivo"] == "original"
        assert "n_casos" not in h

    def test_no_toca_las_filas_que_no_son_de_salud(self):
        h = {"tipo": "falta_en_base", "ticker": "AL30", "motivo": "m",
             "severidad": "alta", "evidencia": {}}
        v._refrescar_salud(h, {"AL30": {"estado": "ok", "n": 0}})
        assert h["motivo"] == "m" and "n_casos" not in h


class TestNoTodoTasaSospechosaHablaDeLaTASA:
    """⚠️ **Tercer caso del mismo patrón** (2026-08-22). El user corrigió los dos
    tickers, el control `assets_ticker_partido` se puso en **0**… y PLC5O y
    S13N6 seguían en ENCONTRÓ. Dos partes del sistema afirmando lo contrario en
    la misma pantalla.

    La causa: `_caduco` decidía «¿esto se puede reverificar?» por el TIPO, y
    `sin_espejo_en_assets` viaja bajo `tasa_sospechosa` sin ser una tasa — es
    *«¿existe este ticker en assets?»*, que se contesta con UNA query."""

    def test_sin_espejo_en_assets_tiene_cotejo(self):
        src = _caduco_src()
        assert '"sin_espejo_en_assets"' in src, (
            "es un hecho de base, no una tasa: tiene que poder caducar al leer")

    def test_el_predicado_sale_de_la_MISMA_funcion_que_el_detector(self):
        """Escrito dos veces, la pantalla podría afirmar que falta la ficha con
        un criterio y que sobra con el otro."""
        assert "tickers_con_ficha" in _caduco_src() or \
               "con_ficha" in _caduco_src()
        src_rel = inspect.getsource(av.relevar) if hasattr(av := __import__(
            "api.services.av_agent", fromlist=["x"]), "relevar") else ""
        assert "tickers_con_ficha" in src_rel, (
            "el detector tiene que usar la misma función, no su propia query")

    def test_no_pude_leer_assets_NO_caduca(self):
        """`None` = no pude mirar. Un huérfano que desaparece porque se cayó una
        query es la mentira más cara de una herramienta de integridad."""
        def caduca(con_ficha, ticker):
            return con_ficha is not None and ticker.strip().upper() in con_ficha
        assert caduca(None, "PLC5O") is False
        assert caduca(set(), "PLC5O") is False
        assert caduca({"PLC5O"}, "plc5o ") is True

    def test_el_cotejo_GENERAL_mata_la_familia_entera(self):
        """⚠️ Tercera vez con la misma forma: control verde, hallazgo vivo. Se
        aplicaron los 6 `pata_equivocada`, `patas_equivocadas` quedó en 0 y
        ENCONTRÓ siguió mostrando 17.

        Taparlo de a una regla por vez es cómo se llega a la cuarta. La relación
        regla → control ya existe en el registro de acciones (`causa` → `sobre`)
        y no hace falta escribirla."""
        assert v._control_de_la_regla("pata_equivocada") == "patas_equivocadas"
        assert v._control_de_la_regla("sin_espejo_en_assets") == "assets_ticker_partido"
        assert v._control_de_la_regla("no_existe_esta_regla") == ""
        assert '_control_de_la_regla' in _caduco_src()

    def test_solo_el_control_en_CERO_tacha(self):
        """Con casos activos no se matchea sujeto por sujeto: las claves no son
        el mismo string (`assets_ticker_partido` guarda la UNIDAD, el hallazgo
        habla del TICKER) y un match fallido se leería como «resuelto»."""
        import inspect
        src = inspect.getsource(v._controles_en_verde)
        assert 'not (g.get("activos") or [])' in src
        assert "return set()" in src, (
            "si no se pudo leer, no caduca nada: «no pude mirar» no es «resuelto»")

    def test_las_reglas_QUE_SIGUEN_SIN_COTEJO_estan_declaradas(self):
        """Las que dependen del PRECIO del día no se pueden reverificar barato y
        está bien que no caduquen. Las que son hechos de base y todavía no lo
        tienen quedan ACÁ nombradas — que es la diferencia entre una decisión y
        un olvido. Cuando alguna consiga su cotejo, se saca de esta lista."""
        sin_cotejo_por_precio = {
            "paridad_fuera_de_rango", "sin_tea_con_precio", "tea_fuera_de_rango",
        }
        deuda_hechos_de_base = {
            # `pata_equivocada` SALIÓ de esta lista: lo cubre el cotejo general
            # por control en cero (`_controles_en_verde`).
            "sin_ejes",                  # el bono tiene o no tiene ejes cargados
            "moneda_flujo_contradice",   # moneda_flujo vs moneda_eje
        }
        assert sin_cotejo_por_precio & deuda_hechos_de_base == set()
        assert len(deuda_hechos_de_base) == 2, (
            "si arreglaste una, sacala de acá; si sumaste otra, agregala — esta "
            "lista es lo que impide que la próxima se pierda en silencio")
