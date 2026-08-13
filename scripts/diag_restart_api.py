"""diag_restart_api — ¿por qué tarda `systemctl restart api.service`? READ-ONLY.

POR QUÉ EXISTE. El restart pasó de ser instantáneo a tardar ~2 minutos. La
sospecha (SIN medir) es el shutdown graceful de uvicorn: ante SIGTERM espera a
que se cierren las conexiones abiertas, y si alguna queda colgada systemd aguanta
hasta `TimeoutStopSec` (default 90s) antes de mandar SIGKILL. 90s + arranque da
justo esos 2 minutos. Pero "da justo" no es evidencia: esto lo MIDE.

Separa el tiempo en dos, que es lo único que importa para saber a dónde ir:
  · PARAR mucho  → shutdown graceful esperando conexiones (o un hilo que no muere).
  · ARRANCAR mucho → algo caro en el import o en el lifespan.

Uso (Droplet, raíz):
    python -m scripts.diag_restart_api            # forensia del ÚLTIMO restart, no toca nada
    python -m scripts.diag_restart_api --medir    # hace UN restart cronometrado

⚠️ `--medir` reinicia la API de verdad (unos segundos de corte). El modo por
defecto no toca nada.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import time


def sh(cmd: list[str], timeout: int = 240) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return f"(timeout de {timeout}s ejecutando: {' '.join(cmd)})"
    except FileNotFoundError:
        return f"(no existe el comando: {cmd[0]})"


def _linea(c: str = "─") -> None:
    print(c * 74)


def config() -> None:
    """Los timeouts efectivos del unit — el techo de lo que puede tardar."""
    print("\n▶ 1. Config efectiva del unit")
    props = sh(["systemctl", "show", "api.service", "--property=Type,TimeoutStopUSec,"
                "TimeoutStartUSec,KillMode,KillSignal,Restart,RestartUSec,RuntimeMaxUSec,"
                "MainPID,ActiveState,ExecMainStartTimestamp"])
    for ln in props.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            print(f"   {k:<26} {v}")
    if "TimeoutStopUSec=1min 30s" in props:
        print("\n   ⚠️  TimeoutStopSec está en el DEFAULT (90s): si el shutdown graceful")
        print("      se cuelga, systemd espera 90s enteros antes de matar el proceso.")


def conexiones() -> None:
    """Conexiones abiertas contra el puerto 8000 — lo que el graceful espera."""
    print("\n▶ 2. Conexiones abiertas al 8000 (esto es lo que el graceful espera)")
    out = sh(["ss", "-tnp", "state", "established", "( sport = :8000 )"])
    filas = [ln for ln in out.splitlines()[1:] if ln.strip()]
    print(f"   {len(filas)} conexiones ESTABLISHED")
    for ln in filas[:12]:
        print(f"     {ln.strip()[:110]}")
    if len(filas) > 12:
        print(f"     … y {len(filas) - 12} más")
    if not filas and "no existe" in out:
        print(f"   {out.strip()}")


def ultimo_restart() -> None:
    """Cuánto tardó el último ciclo stop→start, según el journal de systemd."""
    print("\n▶ 3. Último ciclo de restart (journal de systemd)")
    out = sh(["journalctl", "-u", "api.service", "-n", "120", "--no-pager",
              "-o", "short-iso"])
    lineas = [ln for ln in out.splitlines() if ln.strip()]
    if not lineas:
        print("   (sin líneas de journal)")
        return

    interesantes = []
    for ln in lineas:
        bajo = ln.lower()
        if any(k in bajo for k in (
            "stopping", "stopped", "starting", "started", "deactivat",
            "killing", "timed out", "waiting for", "shutdown", "application startup",
            "uvicorn running", "sigkill", "sigterm",
        )):
            interesantes.append(ln)

    for ln in interesantes[-25:]:
        marca = ""
        bajo = ln.lower()
        if "timed out" in bajo or "sigkill" in bajo or "killing" in bajo:
            marca = "  ← ⚠️  ACÁ ESTÁ EL PROBLEMA"
        elif "waiting for" in bajo:
            marca = "  ← el graceful está esperando"
        print(f"   {ln[:150]}{marca}")

    # Medir stop→start con los timestamps del propio journal.
    ts = lambda ln: ln.split()[0]  # noqa: E731 — '2026-08-13T18:34:28+0000'
    def _parse(s: str) -> float | None:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", s)
        if not m:
            return None
        h, mi, se = int(m.group(4)), int(m.group(5)), int(m.group(6))
        return h * 3600 + mi * 60 + se

    stopping = next((ln for ln in reversed(interesantes) if "Stopping" in ln), None)
    started = next((ln for ln in reversed(interesantes) if "Started" in ln), None)
    if stopping and started:
        a, b = _parse(ts(stopping)), _parse(ts(started))
        if a is not None and b is not None and b >= a:
            print(f"\n   ⏱  último Stopping → Started: {b - a:.0f}s")
            if b - a > 20:
                print("      Más de 20s = NO es normal. Mirá arriba si dice 'timed out'")
                print("      (→ el graceful se colgó) o 'Waiting for connections'.")


def procesos() -> None:
    print("\n▶ 4. Procesos uvicorn vivos (¿quedó alguno zombi de un restart anterior?)")
    out = sh(["ps", "-eo", "pid,etime,rss,cmd", "--sort=start_time"])
    uv = [ln for ln in out.splitlines() if "uvicorn" in ln and "grep" not in ln]
    if not uv:
        print("   (ninguno — la API está caída)")
    for ln in uv:
        print(f"   {ln.strip()[:130]}")
    if len(uv) > 1:
        print("   ⚠️  Hay MÁS DE UNO. Un restart anterior dejó un proceso colgado;")
        print("      el nuevo no puede tomar el puerto hasta que el viejo muera.")


def medir() -> None:
    """UN restart cronometrado, separando parar de arrancar."""
    print("\n▶ 5. Restart cronometrado (esto SÍ reinicia la API)")
    t0 = time.monotonic()
    sh(["systemctl", "stop", "api.service"], timeout=240)
    t_stop = time.monotonic() - t0
    print(f"   PARAR:    {t_stop:6.1f}s")

    t1 = time.monotonic()
    sh(["systemctl", "start", "api.service"], timeout=240)
    t_start = time.monotonic() - t1
    print(f"   ARRANCAR: {t_start:6.1f}s")

    t2 = time.monotonic()
    listo = False
    while time.monotonic() - t2 < 60:
        if '"status":"ok"' in sh(["curl", "-s", "--max-time", "3",
                                  "http://localhost:8000/api/health"], timeout=10):
            listo = True
            break
        time.sleep(0.5)
    print(f"   HASTA RESPONDER /api/health: {time.monotonic() - t2:6.1f}s"
          f"{'' if listo else '  ⚠️ NO respondió en 60s'}")

    print()
    if t_stop > 15:
        print("   → El costo está en PARAR. Es el shutdown graceful de uvicorn")
        print("     esperando conexiones abiertas. Se acota con")
        print("     `--timeout-graceful-shutdown` en el ExecStart y TimeoutStopSec")
        print("     en el unit (ver el bloque final).")
    elif t_start > 15:
        print("   → El costo está en ARRANCAR: algo caro en el import o el lifespan.")
    else:
        print("   → Los dos rápidos: el restart YA no tarda. Si antes tardó, fue")
        print("     una conexión puntual colgada, no algo estructural.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--medir", action="store_true",
                    help="hacer UN restart cronometrado (reinicia la API de verdad)")
    args = ap.parse_args()

    _linea("=")
    print(" api.service — por qué tarda el restart")
    _linea("=")
    config()
    conexiones()
    ultimo_restart()
    procesos()
    if args.medir:
        medir()
    else:
        print("\n   (para cronometrarlo de verdad: --medir; reinicia la API)")

    print()
    _linea("=")
    print(" Si confirma que PARAR es lo caro, el arreglo va al repo (unit file):")
    print("   ExecStart=... uvicorn api.main:app ... --timeout-graceful-shutdown 10")
    print("   TimeoutStopSec=20")
    print(" Eso acota la espera: uvicorn corta a los 10s y systemd a los 20s, en vez")
    print(" de los 90s del default. NO se aplica sin medir primero.")
    _linea("=")


if __name__ == "__main__":
    main()
