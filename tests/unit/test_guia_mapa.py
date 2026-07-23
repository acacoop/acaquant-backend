"""Candado del mapa de la guía (2 incidentes de mapa stale: 'garantía' y
'sensibilidad'). No reemplaza el chequeo cruzado contra el frontend (deuda
abierta), pero congela lo mínimo: rutas bien formadas + el caso que ya falló.
"""
from __future__ import annotations

from api.services.copiloto import ayuda


def test_todas_las_rutas_del_mapa_son_rutas_reales():
    """Cada entrada apunta a una ruta app (empieza con '/'). Una ruta mal
    escrita manda a la guía a un lado que no existe."""
    for e in ayuda._MAPA:
        assert e["ruta"].startswith("/"), f"ruta rara: {e}"
        assert e.get("seccion") and e.get("menu")


def test_sensibilidad_rutea_a_estrategia_no_a_renta_fija():
    """El bug de v1.95: la guía mandaba el 'cuadrito de sensibilidad' a Renta
    Fija; vive en Estrategia (/retorno). Congelado por el corte exacto."""
    fila = next((e for e in ayuda._MAPA
                 if "sensibilidad" in e["seccion"].lower()), None)
    assert fila is not None, "falta la equivalencia de sensibilidad en el mapa"
    assert fila["ruta"] == "/retorno"
    assert "Estrategia" in fila["menu"] and "Renta Fija" not in fila["menu"]
