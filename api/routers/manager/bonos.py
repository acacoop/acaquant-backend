"""Manager sub-router — el ÚNICO endpoint que sobrevivió al borrado del tab BONOS.

El sub-tab `/manager → TÍTULOS → BONOS` (CARGAR / BONOS / REVISAR) y el editor de
ONs que vivía adentro se ELIMINARON: lo que hacían lo hace hoy EL AV AGENT
(`bono_sin_flujo`, `bono_sin_tasa`, `on_faltante` con sus arreglos `alta_bono`,
`alta_flujos` y `alta_on`). Con ellos se fueron `GET/POST/DELETE /bonos`,
`/bonos/sin-tasa`, `/bonos/parse-flujos` y TODO `/bonos` de escritura, más el
router `ons.py` entero.

⚠️ **Las PUERTAS de escritura NO se fueron.** `api/services/bonos_admin.upsert_bono`
y `api/services/ons.upsert_on` siguen enteras porque son por donde escribe
`agente/alta.py`: se fue la pantalla que las llamaba a mano, no la función.

Queda este endpoint solo, y no por inercia: lo consume el check «Títulos sin flujo»
de `/manager → VALIDACIONES`, que no estaba en el pedido de borrado.

  GET /api/manager/bonos/sin-flujo  → conciliador unificado (gate `manager_titulos`)
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/bonos/sin-flujo")
def bonos_sin_flujo() -> dict:
    """Conciliador unificado: bonos cartera ARS/DL/HD faltantes o incompletos en
    Trading.Curvas (no-ON) o BondsMaster (ONs), con la acción para resolver cada uno.
    Gate `manager_titulos`.

    ⚠️ La lista de ignorados (`mercado.ons_ignoradas`) la sigue descontando
    `titulos_sin_flujo`, pero YA NO SE EDITA POR HTTP: la escribe el agente desde
    «no me interesan» (`agente/vista.no_interesan_ons` → `ons.ignorar_concil`).
    Restaurar una descartada hoy no tiene pantalla — es un DELETE a mano sobre
    `mercado.ons_ignoradas`."""
    from api.services.acreencias import titulos_sin_flujo
    falta = titulos_sin_flujo()
    return {
        "total": len(falta),
        "en_cartera": sum(1 for t in falta if t["en_cartera"]),
        "por_fuente": {
            "curvas": sum(1 for t in falta if t["fuente"] == "curvas"),
            "on": sum(1 for t in falta if t["fuente"] == "on"),
            "ninguna": sum(1 for t in falta if t["fuente"] == "ninguna"),
        },
        "ok": not falta,
        "titulos": falta,
    }
