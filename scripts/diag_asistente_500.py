"""scripts/diag_asistente_500.py — reproduce en el Droplet el request que el chat
del LAB manda al apretar ENTER y muestra el error PUNTUAL, sin adivinar.

El front hace `POST /api/agente/lab/runs` y, si eso anda, abre el SSE de
`/lab/runs/{id}/events`. Un «Error: HTTP 500» sin detalle es una excepción
no manejada del backend: el traceback está en el journal de `api.service`,
no en la pantalla. Este diag lo saca de ahí y, antes, chequea las tres
causas típicas en orden (import roto, tabla que falta, request que revienta).

Pasos (cada uno imprime OK / FALLA y sigue):
  1. IMPORT   — importa `api.routers.agente` y `asistente.ejecuciones` en este
                mismo venv. Si falla, la API tira 500 en el primer request.
  2. TABLAS   — `ia.ejecuciones`, `ia.eventos_ejecucion`, `ia.evidencias`
                existen en la base (las crea `apply_schema`).
  3. REQUEST  — pega a la API LOCAL (127.0.0.1:8000) igual que el front:
                Bearer API_KEY + `x-acaquant-user-email`. Imprime status y
                cuerpo. Si crea un run, lo cancela y lo borra enseguida
                (no llega al modelo: cero costo).
  4. JOURNAL  — el último Traceback de `api.service` (los últimos 15 min).
  5. WORKER   — `asistente-worker.service` activo y sus últimas líneas.

READ-ONLY sobre el negocio: lo único que escribe es el run de prueba del
paso 3, que borra en el mismo paso.

Uso (Droplet, raíz):
    python -m scripts.diag_asistente_500 --email vos@acavalores.com.ar
    python -m scripts.diag_asistente_500 --email ... --sin-request   # solo 1, 2, 4, 5
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
import traceback
import urllib.error
import urllib.request

from config import API_KEY  # config.py hace load_dotenv() al importarse

API_LOCAL = "http://127.0.0.1:8000"
TABLAS = ("ejecuciones", "eventos_ejecucion", "evidencias")


def _titulo(n: int, texto: str) -> None:
    print(f"\n{'─' * 70}\n {n}. {texto}\n{'─' * 70}")


def paso_import() -> bool:
    ok = True
    for modulo in ("api.routers.agente", "asistente.ejecuciones", "asistente.worker"):
        try:
            importlib.import_module(modulo)
            print(f"  OK     import {modulo}")
        except Exception:
            ok = False
            print(f"  FALLA  import {modulo}:")
            print("         " + traceback.format_exc().replace("\n", "\n         "))
    return ok


def paso_tablas() -> bool:
    from core.postgres import connect

    ok = True
    try:
        with connect() as conn, conn.cursor() as cur:
            for tabla in TABLAS:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema = 'ia' AND table_name = %s ORDER BY ordinal_position",
                    (tabla,),
                )
                columnas = [r[0] for r in cur.fetchall()]
                if columnas:
                    print(f"  OK     ia.{tabla}  ({len(columnas)} columnas)")
                else:
                    ok = False
                    print(f"  FALLA  ia.{tabla} NO EXISTE → correr python -m scripts.apply_schema")
    except Exception as e:
        print(f"  FALLA  no pude consultar la base: {type(e).__name__}: {e}")
        return False
    return ok


def _http(metodo: str, ruta: str, email: str, body: dict | None = None) -> tuple[int, str]:
    datos = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API_LOCAL + ruta, data=datos, method=metodo, headers={
        "Authorization": f"Bearer {API_KEY}",
        "x-acaquant-user-email": email,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def paso_request(email: str) -> bool:
    try:
        status, cuerpo = _http("POST", "/api/agente/lab/runs", email,
                               {"pregunta": "diag_asistente_500: ignorar", "sesion": ""})
    except Exception as e:
        print(f"  FALLA  no pude hablar con {API_LOCAL}: {type(e).__name__}: {e}")
        print("         ¿api.service está arriba?  systemctl status api.service")
        return False
    print(f"  POST /api/agente/lab/runs → HTTP {status}")
    print("         " + cuerpo[:800].replace("\n", "\n         "))
    if status != 200:
        return False
    run_id = ""
    try:
        run_id = str(json.loads(cuerpo).get("run_id") or "")
    except ValueError:
        pass
    if run_id:
        st, _ = _http("POST", f"/api/agente/lab/runs/{run_id}/cancelar", email)
        print(f"  cancelar → HTTP {st}")
        st, cuerpo = _http("GET", f"/api/agente/lab/runs/{run_id}", email)
        print(f"  GET run → HTTP {st}  estado={_estado(cuerpo)}")
        from core.postgres import connect
        with connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ia.ejecuciones WHERE run_id = %s", (run_id,))
            print(f"  borrado el run de prueba ({cur.rowcount} fila)")
    return True


def _estado(cuerpo: str) -> str:
    try:
        return str(json.loads(cuerpo).get("estado"))
    except ValueError:
        return "?"


def _journal(unidad: str, desde: str, lineas: int | None = None) -> str:
    cmd = ["journalctl", "-u", unidad, f"--since={desde}", "--no-pager", "--output=cat"]
    if lineas:
        cmd += ["-n", str(lineas)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return "(journalctl no está disponible: ¿no es el Droplet?)"
    except subprocess.TimeoutExpired:
        return "(journalctl tardó demasiado)"
    return p.stdout if p.returncode == 0 else f"(journalctl falló: {p.stderr.strip()[:200]})"


def paso_journal() -> None:
    salida = _journal("api.service", "15 min ago")
    lineas = salida.splitlines()
    inicios = [i for i, l in enumerate(lineas) if l.startswith("Traceback")]
    if not inicios:
        print("  sin Traceback en api.service en los últimos 15 min")
        print("  (si el 500 lo diste hace más, volvé a apretar ENTER en el chat y corré esto de nuevo)")
        sospechosas = [l for l in lineas if "lab/runs" in l or " 500 " in l or "ERROR" in l][-15:]
        if sospechosas:
            print("  líneas con lab/runs / 500 / ERROR:")
            print("  " + "\n  ".join(sospechosas))
        return
    ultimo = inicios[-1]
    bloque = lineas[max(0, ultimo - 3):ultimo + 60]
    print("  ÚLTIMO TRACEBACK de api.service:")
    print("  " + "\n  ".join(bloque))


def paso_worker() -> None:
    try:
        p = subprocess.run(["systemctl", "is-active", "asistente-worker.service"],
                           capture_output=True, text=True, timeout=10)
        estado = p.stdout.strip() or p.stderr.strip()
    except FileNotFoundError:
        estado = "(systemctl no disponible)"
    print(f"  asistente-worker.service: {estado}")
    print("  " + "\n  ".join(_journal("asistente-worker.service", "1 hour ago", 15).splitlines()[-15:]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True, help="tu email de la mesa (el que usa el front)")
    ap.add_argument("--sin-request", action="store_true", help="no crear el run de prueba")
    a = ap.parse_args()

    _titulo(1, "IMPORT del router y del módulo de ejecuciones")
    imp = paso_import()
    _titulo(2, "TABLAS ia.* en la base")
    tab = paso_tablas()
    req = None
    if not a.sin_request:
        _titulo(3, "REQUEST a la API local, igual que el front")
        req = paso_request(a.email)
    _titulo(4, "JOURNAL de api.service — el error puntual")
    paso_journal()
    _titulo(5, "WORKER del asistente")
    paso_worker()

    print("\n" + "═" * 70)
    print(f" import={'OK' if imp else 'FALLA'}  tablas={'OK' if tab else 'FALLA'}"
          f"  request={'—' if req is None else ('OK' if req else 'FALLA')}")
    return 0 if imp and tab and req is not False else 1


if __name__ == "__main__":
    sys.exit(main())
