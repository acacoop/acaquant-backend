"""core/crontab.py — el parser del crontab, UNA sola vez y sin dependencias.

Vivía adentro de `api.services.jobs_catalogo` y lo necesitaba también
`core.dependencias` (para traducir el label de un cron a sus proveedores).
Un import core → api rompe el contrato de capas (`.importlinter`: core/ no
importa nada del proyecto), y copiar el parser habría dejado dos regexes que
se desincronizan solas (REGLA #9). La pieza es parsing PURO de un archivo que
viaja con el deploy — no toca base ni red — así que su lugar es core/;
`jobs_catalogo` delega acá y todos sus llamadores siguen igual.
"""
from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

CRONTAB = Path(__file__).resolve().parents[1] / "deploy" / "crontab.txt"

_RE_RUNJOB = re.compile(r"run_job\.sh\s+(\S+)\s+(\S+)\s+'(.+)'\s*$")
_RE_MODULES = re.compile(r"-m\s+((?:jobs|engines|scripts)\.[\w.]+)")


def parse_crontab() -> list[dict]:
    """Líneas run_job.sh del crontab → [{label, schedule, timeout, modules}]."""
    out: list[dict] = []
    for raw in CRONTAB.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        schedule, cmd = " ".join(parts[:5]), parts[5]
        m = _RE_RUNJOB.search(cmd)
        if not m:
            continue  # systemctl start/stop de motores → viven en DIAGNÓSTICO
        label, timeout, inner = m.groups()
        modules = _RE_MODULES.findall(inner)
        # `comando` es lo que run_job.sh ejecuta, tal cual: es lo que un
        # relanzamiento tiene que repetir (agente/rehacer.py), sin re-armarlo.
        out.append({"label": label, "schedule": schedule, "timeout": timeout,
                    "modules": modules, "comando": inner})
    return out


# ═══ CADA CUÁNTO CORRE, EN SEGUNDOS ════════════════════════════════════════
#
# ⚠️⚠️ **EL RITMO DE UN JOB ESTÁ DECLARADO ACÁ Y EL AGENTE LO ESTABA ADIVINANDO.**
#
# `agente/tablas.py` mide la mediana del tiempo entre filas para clasificar una
# tabla. Funciona para un motor, y falla feo para un job que corre UNA VEZ AL DÍA
# y appendea un lote: adentro del lote las filas están separadas por
# milisegundos, así que la mediana dice «tiempo real» y el detector le empieza a
# exigir el ritmo de un feed live.
#
# Medido el 2026-08-28: **7 de los 10 hallazgos abiertos de `tabla_quieta`** eran
# eso. `research.mkt_1816_series` (un append diario de 22:00 UTC) figuraba como
# «tiempo real, cada 2 s», y `mercado.canje_cierre` —post-cierre— como «cada 0 s».
#
# La guarda que ya existía (`tablas._dias_con_escritura`) pregunta *«¿escribió en
# muchos días distintos?»*, y un job diario contesta **que sí**: escribe todos los
# días… una vez. Distingue «escribe seguido» de «escribió mucho una vez», pero no
# **«escribe todo el día»** de **«escribe todos los días»**.
#
# ⚠️⚠️ **UN JOB EN VARIAS LÍNEAS DEL CRONTAB ES UN SOLO RELOJ, NO VARIOS.**
#
# La versión anterior resolvía cada línea por separado y, cuando el mismo módulo
# aparecía en más de una, se quedaba con el hueco MÁS CHICO de las líneas
# sueltas — como si fueran horarios alternativos. No lo son: se COMPONEN. Medido
# sobre `deploy/crontab.txt`: **7 de los 58 jobs tienen más de una línea**, y en
# los 7 el número que salía así era falso. Caso real: `jobs.mayor_sync` corre
# `*/15 13-15 * * 1-5` + `0,30 16-17 * * 1-5` + `0 18-21 * * 1-5` — o sea
# 13:00→21:00 UTC sin parar, 16 h de hueco real — y con las líneas sueltas
# contestaba **21 h**.
#
# Por eso `hueco_maximo_union` mira TODAS las líneas de un job a la vez, sobre
# una grilla SEMANAL (`_momentos_semana`, minuto-de-semana 0..10079, 0 = lunes
# 00:00) y no diaria: el hueco viernes→lunes no existe en una grilla de un día,
# solo aparece si el reloj da toda la vuelta a la semana.
#
# El fin de semana se DESCUENTA acá (cuando `solo_habiles`) y se vuelve a sumar
# en `agente/tablas.py::_segundos_de_finde` — las dos mitades tienen que usar la
# MISMA definición de `solo_habiles`, si no se descuenta el finde dos veces o
# ninguna (REGLA #9: el mismo dato en dos lugares necesita árbitro).
_SEMANA_MIN = 7 * 1440


def _valores(campo: str, tope: int) -> list[int] | None:
    """Los valores concretos que matchea un campo cron. `None` = no lo entiendo.

    ⚠️ `None` ante la duda es a propósito: **quien no sabe se abstiene** y el que
    pregunta se queda con lo que medía antes. Adivinar mal un ritmo declarado es
    peor que no declararlo, porque tapa la señal con una cifra que parece dura.
    """
    if campo == "*":
        return list(range(tope))
    out: set[int] = set()
    for parte in campo.split(","):
        cuerpo, paso = (parte.split("/", 1) + ["1"])[:2] if "/" in parte else (parte, "1")
        try:
            n = int(paso)
        except ValueError:
            return None
        if n <= 0:
            return None
        if cuerpo == "*":
            a, b = 0, tope - 1
        elif "-" in cuerpo:
            try:
                a, b = (int(x) for x in cuerpo.split("-", 1))
            except ValueError:
                return None
        elif cuerpo.isdigit():
            a = b = int(cuerpo)
        else:
            return None
        if not (0 <= a <= b < tope):
            return None
        out.update(range(a, b + 1, n))
    return sorted(out) or None


def _momentos_semana(schedule: str) -> set[int] | None:
    """Los minutos-de-semana (0..10079, 0 = lunes 00:00) en los que dispara.

    Es la versión SEMANAL de `_valores`: misma doctrina de abstenerse
    (`None` = no lo puedo modelar). Se abstiene si el cron no es semanal —
    día-del-mes o mes distintos de `*`— o si minuto/hora/día-de-semana no se
    pueden resolver con `_valores`.
    """
    campos = (schedule or "").split()
    if len(campos) < 5:
        return None
    # Día-del-mes y MES tienen que ser `*`: un cron que corre sólo los 1° o sólo
    # en junio no es un ritmo semanal, y meterlo en esta grilla le inventaría una
    # tolerancia dura y falsa. Hoy no hay ninguno en el crontab (medido: los 93
    # tienen `*` en el mes); la guarda está para el día que aparezca, porque el
    # modo de falla sería el de siempre — no falla nada, contesta otro número.
    if campos[2] != "*" or campos[3] != "*":
        return None
    minutos = _valores(campos[0], 60)
    horas = _valores(campos[1], 24)
    if not minutos or not horas:
        return None
    dias = _valores(campos[4], 8)
    if not dias:
        return None
    # Cron usa 0..7 con 0 Y 7 = domingo; Python usa weekday() con lunes=0.
    weekdays = {6 if d in (0, 7) else d - 1 for d in dias}
    return {dow * 1440 + h * 60 + m for dow in weekdays for h in horas for m in minutos}


def hueco_maximo_union(schedules) -> tuple[int, bool] | None:
    """El hueco MÁS LARGO (seg) de la UNIÓN de varias líneas del MISMO job.

    Varias líneas de un job no son horarios alternativos: se COMPONEN, son un
    solo reloj. `jobs.mayor_sync` corre `*/15 13-15 * * 1-5` +
    `0,30 16-17 * * 1-5` + `0 18-21 * * 1-5` — 13:00→21:00 UTC sin parar, 16 h
    de hueco real — y resolver cada línea por separado y quedarse con la más
    chica contestaba 21 h.

    Se resuelve sobre la grilla SEMANAL de `_momentos_semana` (no diaria):
    el hueco viernes→lunes no existe en una grilla de un día. Los pares se
    recorren de forma CIRCULAR sobre la semana (del último al primero, sumando
    una semana en minutos).

    `solo_habiles` sale de los CAMPOS, no de la grilla resultante: es `True`
    solo si TODAS las líneas tienen día-de-semana != `*` y día-del-mes == `*`.
    Tiene que ser exactamente esa definición porque `agente/tablas.py::frescura`
    le suma el fin de semana con `_segundos_de_finde` usando el mismo criterio
    — si las dos mitades no coinciden, el finde se descuenta dos veces o
    ninguna (REGLA #9).

    Cuando `solo_habiles`, a cada hueco se le resta 1440 min por cada sábado o
    domingo que abarca, contado igual que `_segundos_de_finde`: los días `d`
    desde `a // 1440` hasta `(a + hueco) // 1440`, sin incluir el último.
    """
    momentos: set[int] = set()
    solo_habiles = True
    tuvo_alguna = False
    for sch in schedules:
        tuvo_alguna = True
        m = _momentos_semana(sch)
        if m is None:
            return None
        momentos |= m
        campos = sch.split()
        if campos[4] == "*":
            solo_habiles = False
    if not tuvo_alguna or not momentos:
        return None
    ordenados = sorted(momentos)
    extendidos = ordenados + [ordenados[0] + _SEMANA_MIN]
    huecos = []
    for a, b in pairwise(extendidos):
        hueco = b - a
        if solo_habiles:
            d, fin = a // 1440, (a + hueco) // 1440
            dias_finde = sum(1 for x in range(d, fin) if x % 7 >= 5)
            hueco -= dias_finde * 1440
        huecos.append(hueco)
    maximo = max(huecos)
    if maximo <= 0:
        return None
    return maximo * 60, solo_habiles


def hueco_maximo(schedule: str) -> int | None:
    """El hueco MÁS LARGO, en segundos, entre dos corridas de UNA sola línea.

    No es «cada cuánto corre en promedio», que para un job con ventana horaria da
    un número que no existe: `*/30 12-23` no corre cada hora, corre cada media
    hora **de 12 a 23** y después no corre en doce horas y media. Lo que hace
    falta para no gritar es el hueco más largo que el cron ADMITE.

        `0 22 * * 1-5`                 → una por día        → 24 h
        `*/30 12-23 * * *`             → 12:00…23:30        → 12,5 h (el salto nocturno)
        `0 12,14,16,18,20,22 * * 1-5`  → 12,14,…,22         → 14 h

    Wrapper de `hueco_maximo_union([schedule])`: cuando el job tiene UNA sola
    línea, la grilla semanal y la circular dan el mismo número que antes. Si el
    job tiene varias líneas hay que sumarlas — ver `hueco_maximo_union`.
    """
    r = hueco_maximo_union([schedule])
    return r[0] if r else None


def ritmo_declarado() -> dict[str, dict]:
    """`módulo del job` → `{hueco_s, solo_habiles}`, para todo el crontab.

    La clave es el MÓDULO (`jobs.mercado_1816_series`) y no el label, porque es
    lo que devuelve `core.escribe.que_relanzar()` cuando se le pregunta quién
    escribe una tabla. Así las dos mitades —**quién** escribe y **cada cuánto**—
    se juntan sin que nadie tenga que mantener un tercer mapa que se desincronice.

    Agrupa primero TODAS las líneas de cada módulo y recién después resuelve el
    hueco una sola vez con `hueco_maximo_union`: un job en varias líneas del
    crontab es un solo reloj, no varios (ver el comentario de arriba).
    """
    try:
        filas = parse_crontab()
    except Exception:
        return {}
    grupos: dict[str, list[str]] = {}
    for f in filas:
        sch = f.get("schedule", "")
        for m in f.get("modules") or ():
            # Una cadena (`a && b`) corre sus dos jobs con el MISMO reloj: el
            # ritmo es el del cron, no el de cada uno por separado.
            grupos.setdefault(m, []).append(sch)
    out: dict[str, dict] = {}
    for modulo, schedules in grupos.items():
        r = hueco_maximo_union(schedules)
        if not r:
            continue
        hueco, solo_habiles = r
        out[modulo] = {"hueco_s": hueco, "solo_habiles": solo_habiles}
    return out
