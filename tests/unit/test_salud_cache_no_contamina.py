"""El filtro `solo_problemas` NO puede contaminar el cache de `panel()`.

Contexto (2026-08-11): medir la app desde el browser mostró que el layout monta
DOS componentes que piden salud al abrir CUALQUIER pantalla — `SaludBoton` pide
`/salud` y `SaludAlertasModal` pide `/salud?solo_problemas=true`. Los dos
terminaban en el mismo `panel()`, que no estaba cacheado: **2,9 segundos por
carga de página calculando dos veces exactamente lo mismo**.

Al cachear `panel()` aparece un riesgo que antes no existía. El router hacía:

    r = svc.panel(email=email)
    if solo_problemas:
        r["chequeos"] = [...]      # ← MUTA el objeto cacheado
    return r

Con cache, ese `r` es la MISMA instancia guardada. Filtrarlo in-place dejaría la
entrada del cache con la lista recortada, y el siguiente pedido SIN
`solo_problemas` —la pantalla de SALUD del Manager— recibiría solo lo roto: cero
chequeos en verde, con pinta de bug de datos y no de cache.

Este test fija ese contrato: pedir filtrado NO puede cambiar lo que ve el que
pide completo, aunque los dos compartan el objeto.
"""
from __future__ import annotations

from api.routers.manager import salud as router_salud
from api.services import salud as svc


def _panel_falso() -> dict:
    """Un panel con los tres estados, para distinguir filtrado de completo."""
    return {
        "veredicto": svc.ERROR,
        "chequeos": [
            {"id": "a", "estado": svc.OK},
            {"id": "b", "estado": svc.WARN},
            {"id": "c", "estado": svc.ERROR},
        ],
        "pendientes": [],
    }


def test_pedir_solo_problemas_no_recorta_el_pedido_completo(monkeypatch):
    # UNA sola instancia compartida: es exactamente lo que devuelve el cache
    # cuando dos requests seguidos caen en la misma entrada.
    compartido = _panel_falso()
    monkeypatch.setattr(svc, "panel", lambda email="": compartido)

    filtrado = router_salud.get_salud(solo_problemas=True, email="x@y.com")
    assert [c["id"] for c in filtrado["chequeos"]] == ["b", "c"], \
        "el filtro tiene que sacar los chequeos en verde"

    completo = router_salud.get_salud(solo_problemas=False, email="x@y.com")
    assert [c["id"] for c in completo["chequeos"]] == ["a", "b", "c"], \
        "el pedido completo NO puede haber perdido el chequeo en verde"

    # Y el objeto de origen quedó intacto: nadie escribió sobre el cache.
    assert [c["id"] for c in compartido["chequeos"]] == ["a", "b", "c"]


def test_el_filtrado_no_pierde_el_resto_del_payload(monkeypatch):
    """Recortar `chequeos` no puede llevarse puestas las otras claves — el modal
    usa `pendientes` y el veredicto para decidir si abre."""
    monkeypatch.setattr(svc, "panel", lambda email="": _panel_falso())
    r = router_salud.get_salud(solo_problemas=True, email="x@y.com")
    assert r["veredicto"] == svc.ERROR
    assert "pendientes" in r
