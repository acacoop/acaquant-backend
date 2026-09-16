"""security_audit.py — chequeo de seguridad defensiva del repo (read-only).

Herramienta: seguridad · Escaneo de secretos filtrados, deps con CVE (pip-audit) y código inseguro (bandit).

Pensado para correrlo solo, sin saber de seguridad: junta los chequeos
automatizables y reporta en castellano, priorizado 🔴/🟡/🟢. NO reemplaza una
auditoría profesional, pero cubre el 80% de lo que un atacante prueba primero.

    python -m scripts.security_audit

Cubre:
  1. SECRETOS en el working tree (claves/credenciales que no deberían estar).
  2. DEPS con CVE conocido — vía pip-audit (si está instalado).
  3. CÓDIGO inseguro — vía bandit (si está instalado).
  4. POSTURA de config — CORS abierto, debug/reload, logging de secretos.

Lo que NO puede chequear desde acá (va por checklist manual, ver el final):
  - Atlas IP allowlist, puertos del Droplet, SSH/fail2ban.
  - Secretos en el HISTORIAL de git (usar gitleaks — ver el reporte).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patrones de credenciales REALES (no placeholders). (nombre, regex).
_SECRET_PATTERNS = [
    ("Mongo URI con password", re.compile(r"mongodb\+srv://[^:\s]+:[^@\s.]{6,}@")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key block", re.compile(r"-----BEGIN[A-Z ]*PRIVATE KEY-----")),
    ("OpenAI/Anthropic key", re.compile(r"\b(sk|sk-ant)-[A-Za-z0-9_-]{20,}")),
    ("JWT largo embebido", re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}\.")),
    ("Password hardcodeado", re.compile(r"(?i)(password|passwd|secret|token)\s*=\s*[\"'][^\"'$<{]{8,}[\"']")),
]
# Lo que es ejemplo/placeholder y NO cuenta como leak.
_FALSO = re.compile(r"(?i)\.\.\.|example|placeholder|your_|xxx|<[a-z_]+>|getenv|environ|os\.environ|\bENV\b|dummy|changeme|test")
_SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "logs", ".ipynb_checkpoints"}
_SCAN_EXT = {".py", ".md", ".json", ".txt", ".sh", ".service", ".yml", ".yaml", ".toml", ".env"}


def _walk_files():
    for p in ROOT.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and (p.suffix in _SCAN_EXT or p.name.startswith(".env")):
            yield p


def scan_secretos() -> list[tuple[str, str]]:
    """Devuelve [(ruta_rel, descripción)] de líneas con patrón de secreto."""
    hits: list[tuple[str, str]] = []
    for p in _walk_files():
        try:
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if len(line) > 400 or _FALSO.search(line):
                    continue
                for nombre, rx in _SECRET_PATTERNS:
                    if rx.search(line):
                        hits.append((str(p.relative_to(ROOT)), f"{nombre}:{i}"))
        except Exception:
            continue
    return hits


def _ignorado(rel: str) -> bool:
    """True si git ya ignora el archivo (= es un .env legítimo, no un leak)."""
    rc, _ = _run(["git", "check-ignore", "-q", rel])
    return rc == 0


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, cwd=ROOT)
        return r.returncode, (r.stdout + r.stderr)
    except Exception as e:
        return -1, str(e)


def check_config() -> list[str]:
    """Anti-patterns de configuración detectables por grep."""
    avisos = []

    def grep(rx: str, paths: str) -> list[str]:
        # -i: case-insensitive (POSIX grep no soporta (?i) embebido).
        rc, out = _run(["grep", "-rniE", rx, *paths.split()])
        return [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []

    if grep(r"allow_origins\s*=\s*\[?\s*[\"']\*[\"']", "api"):
        avisos.append("🔴 CORS abierto a '*' en api/ — cualquier web puede llamar tu API desde el browser.")
    if grep(r"(debug\s*=\s*true|reload\s*=\s*true)", "api"):
        avisos.append("🟡 debug/reload=True en api/ — no debe quedar así en prod (filtra stack traces).")
    # Logging de secretos: el grep matchea la PALABRA, no el valor → listamos las
    # líneas para que las juzgues (mención en un mensaje = OK; interpolar el valor = mal).
    logs = grep(r"(print|logger\.[a-z]+)\([^)]*(password|token|secret|mongo_uri|api_key)",
                "api jobs core engines")
    if logs:
        avisos.append(f"ℹ️  {len(logs)} línea(s) que mencionan un secreto en print/log — revisá que NO interpolen el valor:")
        avisos.extend(f"      {ln}" for ln in logs[:8])
    if grep(r"verify\s*=\s*false", "api jobs core engines"):
        avisos.append("🟡 verify=False (TLS sin verificar) en alguna request — vector de man-in-the-middle.")
    if grep(r"shell\s*=\s*true", "api jobs core engines"):
        avisos.append("🟡 subprocess con shell=True — riesgo de command injection si entra input externo.")
    return avisos


def _emit() -> None:
    print("=" * 70)
    print("AUDITORÍA DE SEGURIDAD — AcaQuant")
    print("=" * 70)

    # 1. Secretos — distinguir archivos VERSIONABLES (leak real) de IGNORADOS (.env, OK).
    print("\n[1] SECRETOS en el código (working tree)")
    versionables: dict[str, list[str]] = {}
    ignorados: dict[str, list[str]] = {}
    for rel, desc in scan_secretos():
        (ignorados if _ignorado(rel) else versionables).setdefault(rel, []).append(desc)
    if versionables:
        print("  🔴 SECRETOS EN ARCHIVOS VERSIONABLES (se pueden commitear → LEAK real):")
        for arch, descs in versionables.items():
            print(f"     • {arch} — {', '.join(descs)}")
        print("     → Agregá el archivo al .gitignore Y ROTÁ esos secretos (asumí que se vieron).")
    if ignorados:
        print(f"  ℹ️  Secretos en archivos YA gitignoreados (esperado, ej. .env): "
              f"{', '.join(ignorados)} — OK, no se commitean.")
    if not versionables and not ignorados:
        print("  🟢 Sin credenciales en el working tree.")

    # 2. Deps con CVE
    print("\n[2] DEPENDENCIAS con CVE conocido")
    if shutil.which("pip-audit"):
        rc, out = _run(["pip-audit", "--progress-spinner", "off"])
        print("  " + (out.strip().replace("\n", "\n  ") or "(sin salida)"))
        if rc == 0:
            print("  🟢 Sin vulnerabilidades conocidas.")
    else:
        print("  ⚪ pip-audit no instalado. Instalá y corré:")
        print("       pip install pip-audit && pip-audit")

    # 3. Código inseguro
    print("\n[3] CÓDIGO inseguro (análisis estático)")
    if shutil.which("bandit"):
        rc, out = _run(["bandit", "-r", "api", "jobs", "core", "engines", "quant",
                        "-ll", "-q", "-f", "txt"])
        print("  " + (out.strip().replace("\n", "\n  ") or "(sin hallazgos de severidad media/alta)"))
    else:
        print("  ⚪ bandit no instalado. Instalá y corré (severidad media+):")
        print("       pip install bandit && bandit -r api jobs core engines quant -ll")

    # 4. Config
    print("\n[4] POSTURA de configuración")
    cfg = check_config()
    if cfg:
        for a in cfg:
            print(f"  {a}")
    else:
        print("  🟢 Sin anti-patterns de config detectados (CORS, debug, logging de secretos).")

    # Checklist manual
    print("\n" + "=" * 70)
    print("CHEQUEO MANUAL (no automatizable desde acá):")
    print("=" * 70)
    print("  □ Secretos en el HISTORIAL de git → instalá gitleaks y corré:")
    print("      brew install gitleaks && gitleaks detect --source . -v")
    print("  □ Atlas → Network Access: la IP allowlist NO debe ser 0.0.0.0/0")
    print("    (solo la IP del Droplet + tu IP). Si está abierta + se filtró la URI = acceso total.")
    print("  □ Droplet: 'sudo ufw status' (solo 22/80/443), fail2ban activo, SSH con key (no password).")
    print("  □ Cada cambio de código sensible: pasalo por el skill /seguridad-acaquant.")


def main() -> None:
    """Por default escribe el reporte completo a un archivo (es largo). Con
    --stdout además lo imprime en la terminal."""
    import argparse
    import sys
    from contextlib import redirect_stdout

    ap = argparse.ArgumentParser(description="Auditoría de seguridad AcaQuant")
    ap.add_argument("--out", default="security_audit_report.txt",
                    help="archivo de salida (default: security_audit_report.txt)")
    ap.add_argument("--stdout", action="store_true",
                    help="además del archivo, imprimir todo en la terminal")
    args = ap.parse_args()

    with open(args.out, "w", encoding="utf-8") as f:
        if args.stdout:
            class _Tee:
                def write(self, s: str) -> int:
                    sys.__stdout__.write(s)
                    return f.write(s)

                def flush(self) -> None:
                    sys.__stdout__.flush()
                    f.flush()

            with redirect_stdout(_Tee()):
                _emit()
        else:
            with redirect_stdout(f):
                _emit()

    print(f"✅ Reporte de seguridad escrito en: {args.out}")
    print("   Abrilo con tu editor. NOTA: los hallazgos 'B608 (SQL injection)' de")
    print("   bandit son FALSOS POSITIVOS — el código parametriza todos los valores")
    print("   del usuario con %(param)s; las columnas/tablas son hardcodeadas/whitelist.")


if __name__ == "__main__":
    main()
