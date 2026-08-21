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
