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
        out.append({"label": label, "schedule": schedule, "timeout": timeout,
                    "modules": modules})
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
# No hace falta evaluar la expresión cron completa —eso sería un motor de
# calendario adentro de `core/`— y tampoco hace falta: para saber qué esperar
# alcanza con **cuántas veces por día dispara**.
_DIA_S = 86400


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


def hueco_maximo(schedule: str) -> int | None:
    """El hueco MÁS LARGO, en segundos, entre dos corridas de días consecutivos.

    No es «cada cuánto corre en promedio», que para un job con ventana horaria da
    un número que no existe: `*/30 12-23` no corre cada hora, corre cada media
    hora **de 12 a 23** y después no corre en doce horas y media. Lo que hace
    falta para no gritar es el hueco más largo que el cron ADMITE.

        `0 22 * * 1-5`                 → una por día        → 24 h
        `*/30 12-23 * * *`             → 12:00…23:30        → 12,5 h (el salto nocturno)
        `0 12,14,16,18,20,22 * * 1-5`  → 12,14,…,22         → 14 h

    **El fin de semana no se mira acá**: entre el viernes y el lunes hay tres
    días y meterlo en este número haría que un job diario tolere el triple TODOS
    los días. Eso lo resuelve quien pregunta, con el calendario hábil.
    """
    campos = (schedule or "").split()
    if len(campos) < 5:
        return None
    minutos = _valores(campos[0], 60)
    horas = _valores(campos[1], 24)
    if not minutos or not horas:
        return None
    momentos = sorted(h * 3600 + m * 60 for h in horas for m in minutos)
    if len(momentos) == 1:
        return _DIA_S
    saltos = [b - a for a, b in pairwise(momentos)]
    # El que cruza la medianoche: de la última de hoy a la primera de mañana.
    saltos.append(_DIA_S - momentos[-1] + momentos[0])
    return max(saltos) or None


def ritmo_declarado() -> dict[str, dict]:
    """`módulo del job` → `{hueco_s, solo_habiles}`, para todo el crontab.

    La clave es el MÓDULO (`jobs.mercado_1816_series`) y no el label, porque es
    lo que devuelve `core.escribe.que_relanzar()` cuando se le pregunta quién
    escribe una tabla. Así las dos mitades —**quién** escribe y **cada cuánto**—
    se juntan sin que nadie tenga que mantener un tercer mapa que se desincronice.
    """
    out: dict[str, dict] = {}
    try:
        filas = parse_crontab()
    except Exception:
        return out
    for f in filas:
        sch = f.get("schedule", "")
        hueco = hueco_maximo(sch)
        if not hueco:
            continue
        campos = sch.split()
        # ¿Corre solo días hábiles? Si el campo de día-de-semana no es `*`, el
        # fin de semana no cuenta como atraso — lo descuenta quien pregunta.
        solo_habiles = len(campos) >= 5 and campos[4] != "*" and campos[2] == "*"
        for m in f.get("modules") or ():
            # Una cadena (`a && b`) corre sus dos jobs con el MISMO reloj: el
            # ritmo es el del cron, no el de cada uno por separado.
            if m not in out or hueco < out[m]["hueco_s"]:
                out[m] = {"hueco_s": hueco, "solo_habiles": solo_habiles}
    return out
