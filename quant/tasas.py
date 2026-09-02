"""Conversiones entre convenciones de tasa. Funciones PURAS (sin base, sin FastAPI).

**Por qué existe.** El motor de curvas publica UNA sola tasa por bono en
`mercado.market_snapshot`: la **TEA** — la TIR efectiva anual que sale del XIRR
de los flujos (`engines/curvas.py`). La mesa, en cambio, mira **TNA** en casi
todas las pantallas, y la TNA no es un dato distinto que haya que ir a buscar:
se DERIVA de la TEA. El problema es que hasta ahora cada vista la derivaba por
su cuenta — y Mejoras Dispo (Agro) directamente no la derivaba: ponía la TEA
cruda debajo de un encabezado que decía TNA, así que la columna mentía sin que
nada fallara (ver `api/services/mejoras_dispo.py`).

**La convención de la casa es capitalización MENSUAL**:

    TEM = (1 + TEA)^(1/12) − 1
    TNA = TEM × 12

No se elige acá: se ESPEJA. Es la misma fórmula que ya derivan a mano
`renta-fija-table.tsx` y `bonos-table.tsx` en el front, o sea el número que la
mesa viene mirando hace meses en RENTA FIJA. Cualquier otra habría hecho que el
mismo bono muestre dos TNAs distintas según la pantalla, sin forma de saber
cuál es la buena.

**Lo que estas funciones NO son.** `tna_desde_tea` no es "lo que rinde el bono a
su plazo". El rendimiento real de una Lecap comprada hoy y llevada al
vencimiento es `rendimiento_al_plazo(tea, dias)` — capitalizado, y exacto por
construcción (la TEA salió justamente de ese flujo). Anualizar en forma lineal y
después prorratear por días es una CONVENCIÓN COMERCIAL y da un número distinto:
sobre 180 días y TEA 30%, la convención lineal da 13,08% y el rendimiento real
13,81%. Las dos son legítimas; lo que no es legítimo es mezclarlas sin decirlo.
Por eso viven separadas y cada consumidor elige explícito cuál usa.
"""
from __future__ import annotations

DIAS_ANIO = 365


def tem_desde_tea(tea: float | None) -> float | None:
    """TEM (tasa efectiva MENSUAL) a partir de la TEA. Ambas en fracción."""
    if tea is None or tea <= -1:
        return None
    return (1 + tea) ** (1 / 12) - 1


def tna_desde_tea(tea: float | None) -> float | None:
    """TNA (nominal anual, capitalización mensual) a partir de la TEA.

    Fracción → fracción: `tna_desde_tea(0.30) == 0.2653`, no 26,53.
    """
    tem = tem_desde_tea(tea)
    return None if tem is None else tem * 12


def tna_plazo_remanente(tea: float | None, dias: int | None) -> float | None:
    """TNA **lineal base 365 sobre el plazo remanente** — la convención de 1816.

        r_plazo = (1 + TEA)^(dias/365) − 1        ← rendimiento hasta el vto
        TNA     = r_plazo × 365/dias              ← anualizado SIN capitalizar

    **Por qué existe y por qué es la buena para las letras.** Medido contra 1816
    el 2026-09-02 sobre los 11 bonos de la pill TASA FIJA: la API declara
    `convencionTna = 'plazo-rem'` en 11 de 11, y esta fórmula reproduce su `tna`
    con **error 0,00 pp** en los 11. Las otras dos candidatas no le pegan a
    ninguno: `tna_desde_tea` (TEM×12) se aparta hasta 2,59 pp y la efectiva
    (TNA = TEA) hasta 2,86 pp.

    **La diferencia crece con el plazo, y eso es la firma de la convención**: a
    12 días son 0,14 pp y a 300 días son 2,59 pp. Un error de valuación daría
    diferencias desordenadas; una escalera ordenada por plazo es otra fórmula.

    ⚠️ `dias` es de la **liquidación** al vencimiento, no de hoy: es el plazo por
    el que va la plata. Con `dias` desde hoy el número da parecido y mal, que es
    la peor combinación.

    ⚠️ **No reemplaza a `tna_desde_tea`.** Esa sigue siendo la convención de la
    casa donde no se midió otra cosa (Agro, sintéticos). Las dos son legítimas;
    lo que no es legítimo es que una pantalla muestre una y diga la otra.
    """
    r = rendimiento_al_plazo(tea, dias)
    return None if r is None else r * DIAS_ANIO / dias


def rendimiento_al_plazo(tea: float | None, dias: int | None) -> float | None:
    """Rendimiento EFECTIVO de colocarse `dias` a esa TEA — capitalizado.

    Para una Lecap bullet es el rendimiento exacto (`flujo_vto / precio − 1`),
    porque la TEA se calculó a partir de ese mismo flujo. Distinto de la
    convención lineal `TNA × dias / 365`.
    """
    if tea is None or tea <= -1 or not dias or dias <= 0:
        return None
    return (1 + tea) ** (dias / DIAS_ANIO) - 1
