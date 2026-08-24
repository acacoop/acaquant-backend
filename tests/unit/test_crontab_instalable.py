"""`deploy/crontab.txt` tiene que poder INSTALARSE. Nada más, y alcanza.

⚠️ **EL BUG QUE LO ORIGINA (2026-08-24).** Un commit le reintrodujo un **BOM**
(los tres bytes `EF BB BF` que Windows le pone a un UTF-8) a la primera línea.
`crontab` lo lee como el primer campo del primer renglón y responde:

    "deploy/crontab.txt":0: bad minute
    errors in crontab file, can't install.

Y **rechaza el archivo ENTERO**. O sea: desde ese commit, `crontab
deploy/crontab.txt` no instalaba nada, la máquina se quedó con el crontab que
tenía, y el archivo que todo el repo trata como fuente de verdad dejó de serlo
**en silencio** — no falla ningún job, no hay log, y el archivo se ve perfecto
en el editor.

Es exactamente la clase de bug que el agente detecta con `cron_desalineado` (el
repo dice una cosa y la máquina hace otra), y es la razón por la que ese
detector existe. Pero el detector avisa DESPUÉS; esto lo frena antes de mergear.
"""
from __future__ import annotations

import re
from pathlib import Path

CRONTAB = Path(__file__).resolve().parents[2] / "deploy" / "crontab.txt"


def test_sin_BOM():
    """Un byte invisible que hace que `crontab` descarte el archivo entero."""
    assert not CRONTAB.read_bytes().startswith(b"\xef\xbb\xbf"), (
        "deploy/crontab.txt arranca con un BOM: `crontab` lo lee como el primer "
        "campo y RECHAZA EL ARCHIVO ENTERO ('0: bad minute'). Se saca con "
        "`sed -i '1s/^\\xEF\\xBB\\xBF//' deploy/crontab.txt`.")


def test_toda_linea_de_cron_es_valida():
    """Cinco campos de tiempo y un comando. Un renglón mal formado también hace
    que `crontab` rechace el archivo completo, no solo esa línea."""
    malas = []
    for n, linea in enumerate(CRONTAB.read_text().splitlines(), 1):
        s = linea.strip()
        if not s or s.startswith("#") or re.match(r"^[A-Z_]+=", s):
            continue                      # vacía, comentario o variable
        campos = s.split(None, 5)
        if len(campos) < 6:
            malas.append(f"línea {n}: menos de 6 campos → {s[:60]}")
            continue
        for i, c in enumerate(campos[:5], 1):
            if not re.fullmatch(r"[\d*/,\-]+", c):
                malas.append(f"línea {n}: campo {i} inválido ({c!r}) → {s[:60]}")
                break
    assert not malas, "\n".join(malas)


def test_los_jobs_del_crontab_existen():
    """Un cron que apunta a un módulo borrado corre y falla todos los días.

    Se mira el `-m jobs.x` porque es lo que `run_job.sh` termina ejecutando; el
    label del wrapper es solo para el log y el lock.
    """
    raiz = CRONTAB.resolve().parents[1]
    faltan = []
    for n, linea in enumerate(CRONTAB.read_text().splitlines(), 1):
        if linea.strip().startswith("#"):
            continue
        for m in re.finditer(r"-m\s+(jobs\.[\w.]+)", linea):
            mod = m.group(1)
            if not (raiz / (mod.replace(".", "/") + ".py")).exists():
                faltan.append(f"línea {n}: «{mod}» no existe")
    assert not faltan, "\n".join(faltan)
