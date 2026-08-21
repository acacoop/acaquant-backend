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
    def test_si_salud_no_se_puede_evaluar_no_caduca_nada(self, monkeypatch):
        """⚠️ La guarda que importa: `{}` significa «no pude mirar», y un id
        ausente NO es «está resuelto». Confundirlos vaciaría ENCONTRÓ justo el
        día que SALUD está caído — la mentira más cara que puede decir una
        herramienta de integridad."""
        monkeypatch.setattr(v, "_salud_por_id", dict)
        src = inspect.getsource(v._hallazgos_ultima_corrida)
        # El predicado compara contra "ok" EXPLÍCITO, no contra la ausencia.
        assert 'estado == "ok"' in src, (
            "tiene que exigir el verde, no la ausencia del id")

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
