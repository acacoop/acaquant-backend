"""El control de patas en dólares NO puede contar lo que el botón no arregla.

⚠️ **El incidente (2026-08-22).** `patas_dolar_sin_pedir` decía **133** y al
aplicar la acción se arreglaron **16**: los otros 117 contestaron *«ya la
escuchamos y tiene precio»*. El botón estaba bien. Lo que estaba mal es que la
misma pregunta —¿alguien pide la pata en dólares de este bono?— vivía escrita
dos veces:

  · el CONTROL elegía la pata por `ORDER BY e.simbolo` (alfabético → el CABLE,
    `BPA7C` < `BPA7D`) y miraba solo `adhoc_subscriptions`;
  · la ACCIÓN elige con `core.especies.mejor` (MEP sobre cable, 24hs sobre CI) y
    cuenta como «escuchando» también estar en `market_snapshot`.

Miraban dos símbolos distintos del mismo bono y contestaban distinto. Es la
REGLA #9 y ya se había arreglado DOS veces (la puerta y el diag) — el control
era el tercer lugar, y nadie lo tocó porque nada fallaba.
"""
from __future__ import annotations

import inspect

from jobs import controles_datos as cd


class TestElControlDerivaDeLaAccion:
    def test_el_veredicto_lo_da_explicar_y_no_el_SQL(self):
        """Si el control vuelve a decidir por su cuenta, vuelve a contar 133
        donde hay 16."""
        src = inspect.getsource(cd._chk_patas_dolar_sin_pedir)
        assert "av_agent_pata" in src, (
            "el control tiene que preguntarle a la acción, no reimplementarla")
        assert "explicar(" in src
        assert '"hay_que_pedirla"' in src, (
            "solo ese veredicto tiene trabajo; los demás son estados legítimos")

    def test_no_elige_la_pata_por_orden_alfabetico(self):
        """`ORDER BY e.simbolo` devuelve el CABLE, que es la pata equivocada.
        El SQL puede ordenar para agrupar, pero la ELECCIÓN no puede salir de
        ahí — tiene que venir de `especies.mejor` vía `explicar`."""
        src = inspect.getsource(cd._chk_patas_dolar_sin_pedir)
        i_orden = src.find("ORDER BY")
        i_explicar = src.find("explicar(")
        assert i_explicar > 0, "sin explicar() no hay criterio compartido"
        assert i_orden < i_explicar, (
            "el orden del SQL no puede ser lo último que decide cuál pata es")

    def test_el_simbolo_que_reporta_es_el_que_el_boton_va_a_pedir(self):
        """Si la fila nombra un símbolo y el botón pide otro, el que lee la
        pantalla no puede verificar nada de lo que hizo."""
        src = inspect.getsource(cd._chk_patas_dolar_sin_pedir)
        assert 'd.get("pedible")' in src


class TestLaEleccionDeLaPataViveUnaSolaVez:
    def test_la_puerta_delega_en_especies_mejor(self):
        """El criterio (MEP sobre cable, 24hs sobre CI) es del dominio. Ya hubo
        dos copias y las dos elegían cable."""
        from api.services import av_agent_pata
        src = inspect.getsource(av_agent_pata._elegir)
        assert "especies.mejor" in src


class TestUnVerdeTieneQuePoderAuditarse:
    """⚠️ El control pasó de 133 a **0** en una corrida. *«0 porque no hay nada
    que hacer»* y *«0 porque me quedé ciego»* se ven idénticos en la pantalla:
    los dos son un tilde verde.

    Misma regla que la prueba de permisos (§0.s): **si el chequeo no pudo mirar,
    lo tiene que decir — el silencio se lee como un verde.**"""

    def test_deja_el_desglose_por_veredicto_en_el_log(self):
        src = inspect.getsource(cd._chk_patas_dolar_sin_pedir)
        assert "conteo" in src, (
            "sin desglose, un 0 no se puede distinguir de una ceguera")
        assert "logger.info" in src

    def test_avisa_fuerte_si_no_hubo_NI_UN_candidato(self):
        """Cero candidatos = el `WHERE` dejó de matchear (columna renombrada,
        join roto). Produce cero hallazgos con el mismo verde que «está todo
        bien», y es el único caso en que este control no dice nada."""
        src = inspect.getsource(cd._chk_patas_dolar_sin_pedir)
        assert "if not vistos:" in src
        assert "logger.warning" in src.split("if not vistos:")[1]
