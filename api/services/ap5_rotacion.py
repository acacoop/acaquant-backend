"""api/services/ap5_rotacion.py — EL ACUMULADO ROTA: T-1 + DIARIA = ACUMULADO.

La regla, en una línea::

    acumulado_t1  +  diaria  =  acumulado        ← y mañana el acumulado es el t1

⚠️⚠️ **UN ACUMULADOR QUE ROTA NO ES IDEMPOTENTE POR NATURALEZA.** El job puede
correr dos veces el mismo día — el 2026-08-25 corrió CUATRO veces. Si cada
corrida rotara, el acumulado se duplicaría y **nada fallaría**: el número sigue
siendo plausible y la pantalla se ve igual.

La guarda es `fecha`, y es la razón de ser de este módulo::

    fecha guardada  <  fecha nueva   →  ROTA: el acumulado de ayer pasa a t1
    fecha guardada  == fecha nueva   →  NO rota: recalcula t1 + diaria
    fecha guardada  >  fecha nueva   →  NO toca nada (llegó un día viejo)

El tercer caso importa tanto como los otros dos: `jobs.ap5_portfolio --fecha` de
un día pasado es una operación normal (re-pedirle a la cámara un día que se
borró), y si rotara hacia atrás rompería el acumulado del día corriente.

**Esta función es PURA**: recibe el estado y devuelve el estado nuevo. No toca
la base. Así el invariante se puede congelar con tests en vez de con cuidado.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Los tres tramos, por moneda. `diaria` NO es acumulativa: es el settlement del
# día que dice `fecha`, tal cual lo mandó la cámara.
MONEDAS = ("pesos", "mtr")


@dataclass(frozen=True)
class Estado:
    """Lo que hay guardado hoy para una cuenta (o lo que va a quedar)."""
    fecha: date | None
    t1_pesos: float = 0.0
    t1_mtr: float = 0.0
    diaria_pesos: float = 0.0
    diaria_mtr: float = 0.0

    @property
    def acum_pesos(self) -> float:
        return round(self.t1_pesos + self.diaria_pesos, 2)

    @property
    def acum_mtr(self) -> float:
        return round(self.t1_mtr + self.diaria_mtr, 2)


def rotar(previo: Estado, dia: date, diaria_pesos: float,
          diaria_mtr: float) -> tuple[Estado, str]:
    """El estado nuevo y QUÉ pasó ('rota' | 'recalcula' | 'ignora').

    El motivo se devuelve —en vez de deducirse después— porque es lo que va al
    libro: «rota» y «recalcula» dejan el mismo número cuando la diaria no
    cambió, y sin el motivo no habría forma de distinguir una corrida nueva de
    una repetida.
    """
    if previo.fecha is not None and dia < previo.fecha:
        # Un día viejo (re-pedido a la cámara). No puede mover el acumulado del
        # día corriente: rotar hacia atrás lo dejaría contando otra cosa.
        return previo, "ignora"

    if previo.fecha is not None and dia == previo.fecha:
        # Misma fecha: el t1 NO se toca. Sólo se refresca la diaria, por si la
        # cámara corrigió el día. Re-correr el job devuelve el mismo número.
        return Estado(fecha=dia, t1_pesos=previo.t1_pesos, t1_mtr=previo.t1_mtr,
                      diaria_pesos=round(diaria_pesos, 2),
                      diaria_mtr=round(diaria_mtr, 2)), "recalcula"

    # Día nuevo: lo que hasta ayer era el acumulado, hoy es el punto de partida.
    return Estado(fecha=dia,
                  t1_pesos=previo.acum_pesos, t1_mtr=previo.acum_mtr,
                  diaria_pesos=round(diaria_pesos, 2),
                  diaria_mtr=round(diaria_mtr, 2)), "rota"
