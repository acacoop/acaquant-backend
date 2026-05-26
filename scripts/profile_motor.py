"""profile_motor.py — Perfilar un motor always-on en vivo con py-spy.

Herramienta: perf · Perfila un motor always-on en vivo con py-spy (top/record/dump), sin reiniciarlo.

py-spy se "cuelga" del proceso de un motor por su PID y muestra en qué gasta
CPU SIN reiniciarlo ni instrumentar el código. Ideal para `engines/` (procesos
largos). Este wrapper resuelve el PID del servicio systemd y dispara py-spy.

Correr EN EL DROPLET (donde corren los motores), desde /root/TradingAV:

    python -m scripts.profile_motor --list                 # motores corriendo + PID
    python -m scripts.profile_motor curvas                 # 'top' en vivo (htop de funciones)
    python -m scripts.profile_motor curvas --record 30     # flamegraph SVG de 30s → logs/
    python -m scripts.profile_motor curvas --dump          # stack actual de cada thread (1 shot)

El nombre es la parte corta: `curvas` → `motor_curvas.service`. py-spy necesita
permisos de ptrace → en el Droplet corrés como root, alcanza.

Si py-spy no está instalado: `venv/bin/pip install py-spy` (una sola vez).

Cómo leer cada modo:
  • top     → ranking en vivo por %CPU (OwnTime = tiempo en la función misma,
              TotalTime = incluye lo que llama). Si arriba está `recv`/`poll`/
              `read` de pymongo o del socket WS → el motor está esperando I/O
              (esperado). Si está una función de quant/ o un loop tuyo → CPU.
  • record  → flamegraph (abrir el .svg en el browser): ancho = % de tiempo.
              Las "mesetas" anchas son los hotspots. Las llamadas a I/O
              aparecen como bloques de espera, fáciles de distinguir del cómputo.
  • dump    → foto instantánea del stack de cada thread; útil para ver si un
              motor está "colgado" en una llamada puntual.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _norm_service(name: str) -> str:
    """`curvas` → `motor_curvas.service`. Acepta también el nombre completo."""
    if name.endswith(".service"):
        return name
    if not name.startswith("motor_"):
        name = f"motor_{name}"
    return f"{name}.service"


def _systemctl(*args: str) -> str:
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True
    ).stdout.strip()


def _main_pid(service: str) -> int:
    pid = _systemctl("show", "-p", "MainPID", "--value", service)
    return int(pid) if pid.isdigit() else 0


def _listar() -> None:
    out = _systemctl(
        "list-units", "--all", "--type=service", "--no-legend", "motor_*.service"
    )
    if not out:
        print("No hay servicios motor_*.service (¿estás en el Droplet?).")
        return
    print(f"{'servicio':<32}{'estado':<12}{'PID':>8}")
    print("-" * 52)
    for line in out.splitlines():
        svc = line.split()[0]
        active = _systemctl("show", "-p", "SubState", "--value", svc)
        pid = _main_pid(svc)
        print(f"{svc:<32}{active:<12}{(pid or '-'):>8}")


def _run_pyspy(mode_args: list[str], pid: int) -> int:
    cmd = ["py-spy", *mode_args, "--pid", str(pid)]
    print(f"→ {' '.join(cmd)}\n")
    return subprocess.call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description="Perfilar un motor con py-spy")
    ap.add_argument("motor", nargs="?", help="nombre corto del motor (ej. curvas)")
    ap.add_argument("--list", action="store_true", help="listar motores + PID y salir")
    ap.add_argument("--record", type=int, metavar="SEG", help="grabar flamegraph SVG N segundos")
    ap.add_argument("--dump", action="store_true", help="foto del stack actual (1 shot)")
    ap.add_argument("--rate", type=int, default=100, help="muestras/seg (default 100)")
    args = ap.parse_args()

    if args.list:
        _listar()
        return 0

    if not args.motor:
        ap.error("falta el nombre del motor (o usá --list)")

    if shutil.which("py-spy") is None:
        print("py-spy no está instalado. Instalalo una vez:\n  venv/bin/pip install py-spy")
        return 1

    service = _norm_service(args.motor)
    pid = _main_pid(service)
    if pid == 0:
        print(f"{service} no está corriendo (MainPID=0). Probá: python -m scripts.profile_motor --list")
        return 1

    print(f"motor={service}  PID={pid}")

    if args.dump:
        return _run_pyspy(["dump"], pid)

    if args.record:
        LOGS_DIR.mkdir(exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        out = LOGS_DIR / f"flame_{args.motor}_{ts}.svg"
        rc = _run_pyspy(
            ["record", "--rate", str(args.rate), "--duration", str(args.record), "-o", str(out)],
            pid,
        )
        if rc == 0:
            print(f"\nflamegraph → {out}  (abrilo en el browser)")
        return rc

    # default: top en vivo (Ctrl-C para salir)
    return _run_pyspy(["top", "--rate", str(args.rate)], pid)


if __name__ == "__main__":
    sys.exit(main())
