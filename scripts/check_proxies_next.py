"""scripts/check_proxies_next.py — ¿todo router del backend tiene su proxy en Next?

El browser NUNCA pega a api.acaquant.com directo: pide a `/api/x` del mismo
origen y un route handler de Next (`src/app/api/x/**/route.ts`) reenvía. Si el
handler no existe, Next devuelve 404 y el panel queda vacío EN SILENCIO — así
vivió una semana rota la tab ESTRATEGIA (`/api/estrategia/*` sin proxy) y el
calendario de HOME (`/api/calendario`).

Este check cruza los prefijos `APIRouter(prefix="/api/...")` de api/routers/
contra los directorios de src/app/api/ del checkout del frontend.

Los prefijos que el browser no consume (server-to-server) se listan en IGNORAR.
Los que el frontend expone con OTRO nombre (ej. /api/derivados → derivados-agro)
se declaran en ALIAS — si no, el check daría falsos positivos.

Uso:
    python -m scripts.check_proxies_next                    # autodetecta el frontend
    python -m scripts.check_proxies_next --frontend <path>
    python -m scripts.check_proxies_next --strict           # exit 1 si falta alguno
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Prefijos que NO necesitan proxy: los consume otro servicio, o el frontend solo
# desde el SERVIDOR (SSR con apiFetch, que pega al backend directo).
IGNORAR = {
    "/api/ingest",   # feeds externos (script de la PC de oficina) con API key
    "/api/titulos",  # solo SSR (safeFetch en app/renta-fija/page.tsx)
    "/api/cuentas",  # sin consumidor en el frontend (entrada vestigial en proxy.ts)
}

# Prefijo del backend → directorios de src/app/api/ que lo cubren (el frontend
# los renombra para no colisionar entre sub-dominios del mismo router).
ALIAS: dict[str, set[str]] = {
    "/api/derivados": {"derivados-agro", "derivados-sinteticos"},
    "/api/market": {"market", "eikon-news"},
}

# Candidatos donde suele estar el checkout del frontend, relativos a la raíz.
CANDIDATOS = ["../acaquant-web", "../acaquant-frontend",
              "../../ACAQuant - Frontend/acaquant-frontend"]


def _frontend(arg: str | None) -> Path | None:
    for c in ([arg] if arg else CANDIDATOS):
        p = (RAIZ / c).resolve() if not Path(c).is_absolute() else Path(c)
        if (p / "src" / "app" / "api").is_dir():
            return p
    return None


def _prefijos_backend() -> dict[str, str]:
    """{prefijo: archivo} de cada APIRouter del backend."""
    pat = re.compile(r'APIRouter\(\s*prefix="(/api/[a-z0-9-]+)"')
    out: dict[str, str] = {}
    for f in sorted((RAIZ / "api" / "routers").rglob("*.py")):
        for m in pat.finditer(f.read_text(encoding="utf-8")):
            out.setdefault(m.group(1), str(f.relative_to(RAIZ)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frontend", help="Path al checkout del frontend")
    ap.add_argument("--strict", action="store_true", help="Exit 1 si falta un proxy")
    args = ap.parse_args()

    front = _frontend(args.frontend)
    if front is None:
        print("no encontré el checkout del frontend — pasá --frontend <path>")
        return 0 if not args.strict else 1
    print(f"frontend: {front}\n")

    dirs = {d.name for d in (front / "src" / "app" / "api").iterdir() if d.is_dir()}
    faltan: list[tuple[str, str]] = []
    for prefijo, archivo in sorted(_prefijos_backend().items()):
        if prefijo in IGNORAR:
            continue
        esperados = ALIAS.get(prefijo, {prefijo.removeprefix("/api/")})
        if not (esperados & dirs):
            faltan.append((prefijo, archivo))

    if not faltan:
        print("OK — todos los routers del backend tienen proxy en Next.")
        return 0
    print(f"!! {len(faltan)} router(s) SIN proxy en Next (el browser recibiría 404):")
    for prefijo, archivo in faltan:
        print(f"   {prefijo:<24} ← {archivo}")
    print("\nFix: crear src/app/api/<x>/[...path]/route.ts (copiar el de /api/trading),")
    print("o agregar el prefijo a IGNORAR/ALIAS si no lo consume el browser.")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
