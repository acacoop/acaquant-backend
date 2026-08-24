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

# La rueda en UTC. Los motores corren 13-20 UTC (10-17 ART) por cron: fuera de
# esa ventana el snapshot está viejo POR DISEÑO y no por un problema.
RUEDA_UTC = (13, 20)


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


def segundos_de_rueda(desde: datetime, hasta: datetime | None = None) -> float:
    """Segundos de MERCADO ABIERTO entre dos instantes.

    Una tabla de rueda a las 20:30 ART lleva 3½ h sin escribir y **eso no es un
    atraso**: el mercado cerró. Medir el atraso en tiempo de reloj es lo que
    hacía que el agente cantara 47 hallazgos falsos por noche.
    """
    hasta = ahora_utc(hasta)
    if hasta <= desde:
        return 0.0
    total, cur = 0.0, desde
    while cur < hasta:
        fin_dia = (cur + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        tope = min(fin_dia, hasta)
        if dia_habil(cur):
            abre = cur.replace(hour=RUEDA_UTC[0], minute=0, second=0, microsecond=0)
            cierra = cur.replace(hour=RUEDA_UTC[1], minute=0, second=0, microsecond=0)
            total += max(0.0, (min(tope, cierra) - max(cur, abre)).total_seconds())
        cur = tope
    return total
