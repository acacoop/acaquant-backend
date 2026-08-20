"""api/services/logs_sistema.py — LEER LOS LOGS DE SYSTEMD, UNA SOLA VEZ.

Doc madre: **`docs/AV_AGENT.md`** §0.ac.

Esta lectura vivía ADENTRO de `api/routers/manager/logs.py`, o sea que la única
forma de mirar los logs era que una persona abriera la pantalla. El agente no
podía usarla: un service no importa un router (regla de capas), así que la única
salida habría sido copiar el `subprocess` — y ahí quedan dos formas de leer lo
mismo que se separan sole el día que una cambia (REGLA #9).

Acá está la lectura y el AGRUPADOR; el router y el agente son dos clientes.

QUÉ AGREGA EL AGRUPADOR, Y POR QUÉ HACE FALTA
=============================================

Los últimos N renglones **no sirven para vigilar**: un motor que se reconecta mil
veces produce mil líneas casi iguales y lo único que se lee es la última. La
pregunta útil no es «¿qué dijo recién?» sino **«¿qué viene diciendo, y cuántas
veces?»**.

Por eso se normaliza cada mensaje —se le sacan números, horas, símbolos y
direcciones— y las mil líneas colapsan en UN patrón con su cuenta. Eso convierte
un muro de texto en una lista de tres o cuatro cosas.

⚠️ **NO DECIDE QUÉ ES UN PROBLEMA.** Cuenta y agrupa, nada más. Poner umbrales
acá sin haber mirado nunca los logs de producción sería adivinar (REGLA #2), y un
detector mal calibrado grita todos los días hasta que se lo ignora — que es peor
que no tenerlo. Primero se mide (`scripts/diag_logs_motores`), después se calibra.

⚠️ **Y SI NO SE PUEDE LEER, SE DICE.** `disponible: False` con el motivo. En un
contenedor sin systemd, o si el proceso no tiene permiso sobre el journal, un
`except` que devuelve lista vacía se lee EXACTAMENTE igual que «no hay errores».
Esa confusión es la que ya costó un incidente (§0.s).
"""
from __future__ import annotations

import json
import re
import subprocess
from collections import Counter

# Prioridades de syslog. Se piden por número y se muestran por nombre.
NIVEL: dict[int, str] = {
    0: "emerg", 1: "alert", 2: "crit", 3: "error",
    4: "warn",  5: "notice", 6: "info", 7: "debug",
}
# De acá para arriba (número más chico) es "algo para mirar".
ATENCION = 4  # warn

TIMEOUT_S = 15
# Techo de líneas por lectura. Sin techo, un motor en loop de error puede
# devolver cientos de miles de renglones y el parseo se come el proceso.
MAX_LINEAS = 5000


def _cmd(unidades: list[str], desde: str | None,
         prioridad: int, lineas: int) -> list[str]:
    cmd = ["journalctl"]
    for u in unidades:
        cmd += ["-u", f"{u}.service"]
    if desde:
        cmd += ["--since", desde]
    return cmd + ["-p", str(prioridad), "-n", str(lineas),
                  "--output=json", "--no-pager"]


def leer(unidades: list[str], *, desde: str | None = "-24h",
         prioridad: int = ATENCION, lineas: int = MAX_LINEAS) -> dict:
    """Las líneas de `unidades` desde `desde`, de prioridad `prioridad` o peor.

    `desde=None` no acota por tiempo (las últimas `lineas` y listo) — así lo pide
    la pantalla de LOGS, que muestra el final del archivo.

    Devuelve `{disponible, motivo, lineas: [...]}`. **Nunca levanta**: quien lo
    llama es un job de vigilancia y una excepción acá apagaría todo lo demás que
    ese job mira.
    """
    if not unidades:
        return {"disponible": True, "motivo": None, "lineas": []}
    try:
        proc = subprocess.run(_cmd(unidades, desde, prioridad, lineas),
                              capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return {"disponible": False, "lineas": [],
                "motivo": "no hay journalctl en esta máquina"}
    except subprocess.TimeoutExpired:
        return {"disponible": False, "lineas": [],
                "motivo": f"journalctl no contestó en {TIMEOUT_S}s"}
    except Exception as e:
        return {"disponible": False, "lineas": [], "motivo": str(e)[:200]}
    if proc.returncode != 0:
        return {"disponible": False, "lineas": [],
                "motivo": (proc.stderr or "").strip()[:200] or "journalctl falló"}

    out: list[dict] = []
    for raw in proc.stdout.splitlines():
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        try:
            us = int(d.get("__REALTIME_TIMESTAMP", "0"))
        except (TypeError, ValueError):
            us = 0
        try:
            prio = int(d.get("PRIORITY", 6))
        except (TypeError, ValueError):
            prio = 6
        out.append({
            "ts": us / 1_000_000,
            "nivel": NIVEL.get(prio, "info"),
            "prioridad": prio,
            "unidad": str(d.get("_SYSTEMD_UNIT", "")).removesuffix(".service"),
            "mensaje": str(d.get("MESSAGE", "")),
        })
    return {"disponible": True, "motivo": None, "lineas": out}


# ── LA NORMALIZACIÓN ────────────────────────────────────────────────────────
#
# Lo que se borra es lo que CAMBIA en cada repetición y no aporta a identificar
# el problema: la hora, el número de intento, el símbolo, el id. Lo que queda es
# la forma de la frase, que es la identidad real del error.
#
# El orden importa: primero lo largo y específico (fechas, símbolos entre
# comillas), al final los números sueltos. Al revés, un `2026-08-20` ya
# convertido en `N-N-N` no matchea el patrón de fecha.
_LIMPIEZAS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?"), "<fecha>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<fecha>"),
    (re.compile(r"\d{2}:\d{2}:\d{2}"), "<hora>"),
    (re.compile(r"MERV\s*-\s*XMEV\s*-\s*[\w.]+\s*-\s*\w+"), "<simbolo>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                r"[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "<ip>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<hex>"),
    (re.compile(r"'[^']{1,60}'"), "'<x>'"),
    (re.compile(r'"[^"]{1,60}"'), '"<x>"'),
    (re.compile(r"-?\d[\d.,]*"), "<n>"),
    (re.compile(r"\s+"), " "),
)
# Un mensaje entero de 4 KB no se lee ni agrupa mejor que su primer renglón.
LARGO_PATRON = 160


def patron(mensaje: str) -> str:
    """La FORMA del mensaje, sin lo que cambia en cada repetición."""
    # Un traceback son 20 renglones para UN error: se agrupa por el último, que
    # es el que nombra la excepción. Sin esto, cada traceback es su propio grupo.
    lineas = [x for x in (mensaje or "").splitlines() if x.strip()]
    texto = lineas[-1] if len(lineas) > 1 else (lineas[0] if lineas else "")
    for rx, con in _LIMPIEZAS:
        texto = rx.sub(con, texto)
    return texto.strip()[:LARGO_PATRON]


def agrupar(lineas: list[dict]) -> list[dict]:
    """Las líneas colapsadas por (unidad, patrón), de más repetido a menos.

    Cada grupo trae CUÁNTAS veces, DESDE cuándo y HASTA cuándo, y una muestra
    textual. La ventana importa tanto como la cuenta: 300 repeticiones en dos
    minutos es un motor peleando contra algo, y las mismas 300 repartidas en un
    día es ruido de fondo — y no se responden igual.
    """
    grupos: dict[tuple[str, str], dict] = {}
    for x in lineas:
        k = (x["unidad"], patron(x["mensaje"]))
        g = grupos.get(k)
        if g is None:
            grupos[k] = {"unidad": k[0], "patron": k[1], "veces": 1,
                         "primera": x["ts"], "ultima": x["ts"],
                         "peor": x["prioridad"], "nivel": x["nivel"],
                         "muestra": x["mensaje"][:400]}
            continue
        g["veces"] += 1
        g["primera"] = min(g["primera"], x["ts"])
        g["ultima"] = max(g["ultima"], x["ts"])
        if x["prioridad"] < g["peor"]:      # número más chico = más grave
            g["peor"], g["nivel"] = x["prioridad"], x["nivel"]
            g["muestra"] = x["mensaje"][:400]
    # Lo más grave primero y, dentro de eso, lo que más se repite.
    return sorted(grupos.values(), key=lambda g: (g["peor"], -g["veces"]))


def por_unidad(lineas: list[dict]) -> dict[str, Counter]:
    """Cuántas líneas de cada nivel dejó cada unidad. Para la foto de conjunto."""
    out: dict[str, Counter] = {}
    for x in lineas:
        out.setdefault(x["unidad"] or "?", Counter())[x["nivel"]] += 1
    return out
