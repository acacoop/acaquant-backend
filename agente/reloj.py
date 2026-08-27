"""`agente/reloj.py` — el tiempo. **La única definición de «ahora».**

En el agente viejo había dos relojes juzgando la misma foto: un detector leía la
hora del árbol que estaba evaluando y otra función llamaba al reloj del proceso.
Entre las 10:00 y las 10:30 ART eso hacía fallar tres tests todos los días, en
CI, como un rojo intermitente sin causa aparente.

**Cuando algo se evalúa contra una foto, el tiempo tiene que salir de la foto.**
Por eso todo acá recibe `ahora` y solo lo inventa si nadie se lo dio.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from core.tz import AR_TZ

# ═══ EL HORARIO DE MERCADO, EN UN SOLO LUGAR ═══════════════════════════════
#
# User (2026-08-24): *«es fundamental que todo tenga claro el horario de mercado
# para saber cuándo frenar»*.
#
# Y es la mitad de lo que hace que el agente signifique algo: un precio sin
# actualizarse hace 282 minutos es un problema a las 11 y es lo NORMAL a las 18.
# Un detector que no sabe la hora canta 47 falsos positivos por noche.
#
# Los motores corren 13-20 UTC (10-17 ART) por cron: fuera de esa ventana el
# snapshot está viejo POR DISEÑO.
RUEDA_UTC = (13, 20)

# EL CIERRE. Media hora después de que la rueda para: lo que se le pide al
# mercado ya no se pide más, y lo que quedó sin resolver se completa una vez,
# con el día cerrado. En UTC porque todo el agente razona en UTC y convierte al
# mostrar — 20:30 UTC = 17:30 ART.
CIERRE_UTC = (20, 30)
# Cuánto dura la ventana del cierre. No es «a las 20:30 en punto»: el motor
# pasa cada 30 s en rueda y cada 5 min fuera, así que una ventana de una hora es
# lo que garantiza que le toque aunque una pasada se demore.
CIERRE_DURA_MIN = 60


def ahora_utc(ahora=None) -> datetime:
    return ahora or datetime.now(UTC)


def dia_habil(ahora=None) -> bool:
    """¿HOY (en ART) es hábil de mercado? L-V y no feriado argentino.

    Define el UNIVERSO del día: en un día no hábil hay cosas que NO PUEDEN
    pasar, y si pasan el hallazgo es la actividad misma.
    """
    from core import calendario
    return calendario.es_habil(ahora_utc(ahora).astimezone(AR_TZ).date())


def en_rueda(ahora=None) -> bool:
    """¿El mercado está abierto?

    De esto depende que la mitad de lo que mira el agente signifique algo: un
    precio sin actualizar hace 282 minutos es un problema a las 11 y es lo normal
    a las 18. **Feriados incluidos** — mirar solo `weekday < 5` hacía que un
    feriado entre semana contara como rueda y produjera el churn del sábado con
    disfraz de miércoles.
    """
    a = ahora_utc(ahora)
    return dia_habil(a) and RUEDA_UTC[0] <= a.hour < RUEDA_UTC[1]


def en_cierre(ahora=None) -> bool:
    """¿Estamos en la ventana del CIERRE del día hábil?

    Es la ventana de lo que **no se le puede seguir pidiendo al mercado**: la
    rueda paró, los precios del día ya son los definitivos, y lo que quedó sin
    resolver se completa UNA vez y no se toca más hasta mañana.

    Existe como ventana propia y no como «un cron a las 20:30» porque el
    horario del agente vive en UN lugar. Un cron aparte sería un quinto reloj —
    y salir de los cuatro relojes fue todo el punto del rediseño.
    """
    a = ahora_utc(ahora)
    if not dia_habil(a):
        return False
    desde = a.replace(hour=CIERRE_UTC[0], minute=CIERRE_UTC[1],
                      second=0, microsecond=0)
    return desde <= a < desde + timedelta(minutes=CIERRE_DURA_MIN)


def arranco_el_dia(ahora=None) -> datetime:
    """Medianoche de HOY en hora argentina, en UTC.

    ⚠️ En ART y no en UTC: el día UTC arranca a las 21:00 de acá, así que con el
    corte en UTC lo de anoche a las 21:30 saldría como «de hoy» y lo de esta
    mañana también — dos días mezclados bajo el mismo rótulo.
    """
    d = ahora_utc(ahora).astimezone(AR_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return d.astimezone(UTC)


def hhmm(ahora=None) -> str:
    """`dd/mm HH:MM` en hora argentina, para pegar al final de un problema.

    Con FECHA y no solo la hora: el sello se PERSISTE con el hallazgo, así que un
    «· 23:31» pelado se lee como de hoy cuando la fila tiene días.
    """
    return ahora_utc(ahora).astimezone(AR_TZ).strftime("%d/%m %H:%M")

