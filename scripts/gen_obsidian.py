"""Genera el vault de Obsidian (docs/vault/) — el cerebro vivo de TODO el sistema.

Mapea, en un único vault navegable, ambos repos + base + deploy:
  - Backend (TradingAV): core / engines / jobs / quant / api(services·routers·mcp) / partner_api
  - Frontend (acaquant-web): vistas (app), componentes, lib, rutas API (proxy)
  - Base: colecciones Mongo (quién escribe / quién lee)
  - Deploy: servicios systemd + crons (qué módulo corre cada uno)

Edges (las conexiones del "cerebro"):
  - import → [[módulo]]         (AST en backend, regex en front)
  - módulo ↔ colección Mongo    (acceso por nombre de colección)
  - servicio/cron → módulo      (ExecStart / `python -m`)
  - ruta-front → endpoint-back   (URL `/api/...` que proxquea el front)

DETERMINISTA y regenerable — no se mantiene a mano (igual que gen_sistema):
  python -m scripts.gen_obsidian          # (re)genera docs/vault/
  python -m scripts.gen_obsidian --check  # falla si el vault quedó desincronizado

La prosa "qué hace" de cada nota la rellena la pasada de enriquecimiento con IA;
este generador deja el esqueleto + todos los edges mecánicos (que no pueden mentir).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT.parent / "acaquant-web"
VAULT = ROOT / "docs" / "vault"
PROSE = VAULT / "_prose"   # prosa de IA por nodo (sobrevive a la regeneración)

# Paquetes top-level del backend que cuentan como "nodos" del grafo de imports.
# El cerebro = la ARQUITECTURA VIVA del sistema, NO los one-shots ni los tests:
# scripts/ (migraciones/diag de una sola vez) y tests/ se dejan fuera a propósito
# para que el grafo muestre cómo conecta lo que corre en prod, sin ruido.
BACKEND_PKGS = {"core", "engines", "jobs", "quant", "api", "partner_api", "config"}

# Colecciones Mongo conocidas por DB (seed; el scanner igual auto-descubre más).
KNOWN_COLLECTIONS: dict[str, list[str]] = {
    "Trading": ["Curvas", "MarketSnapshot", "SnapshotsCierre", "CanjeCierre",
                "OrderBookL2", "TimeSales", "DOLAR", "UVA", "PreciosAcciones"],
    "Valuaciones": ["Assets", "AuM", "DolarOficialLive", "PnLTotalesCache",
                    "ConsolidadoCuentas", "Dolar"],
    "CashFlow": ["Contrapartes", "Productores", "NegocioMovimientos", "Operaciones"],
    "Manager": ["Users", "RoleMatrix", "RoleAudit"],
    "Clientes": ["Comitentes", "ComercialCache"],
    "CuentasAPI": ["AccionistasAPI", "ContrapartesAPI"],
    "MCP": ["OAuthCodes", "OAuthTokens"],
    "ACAPortfolio": ["Cartera"],
}
DB_NAMES = list(KNOWN_COLLECTIONS.keys())


class Node:
    """Una nota del vault."""

    __slots__ = (
        "id",
        "layer",
        "links",
        "meta",
        "ntype",
        "path",
        "repo",
        "summary",
        "title",
    )

    def __init__(self, nid: str, title: str, ntype: str, layer: str, repo: str,
                 path: str = "", summary: str = ""):
        self.id = nid
        self.title = title
        self.ntype = ntype          # module | collection | service | cron | view | component | lib | route
        self.layer = layer          # core, engines, ... db, deploy, web-view, ...
        self.repo = repo            # backend | frontend | infra
        self.path = path
        self.summary = summary
        self.links: set[str] = set()   # ids de nodos a los que apunta
        self.meta: dict[str, str] = {}


# ───────────────────────── helpers ─────────────────────────

def _slug_backend(rel: Path) -> str:
    """core/mongo.py -> core.mongo ; api/routers/manager/x.py -> api.routers.manager.x"""
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _first_docline(src: str) -> str:
    try:
        mod = ast.parse(src)
        doc = ast.get_docstring(mod)
    except SyntaxError:
        return ""
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


# ───────────────────────── backend ─────────────────────────

def scan_backend(nodes: dict[str, Node]) -> None:
    py_files: list[tuple[Path, str]] = []
    for pkg in BACKEND_PKGS:
        base = ROOT / pkg
        if base.is_file():  # config.py
            py_files.append((base, pkg))
            continue
        if not base.is_dir():
            continue
        for f in base.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            py_files.append((f, pkg))
    # config.py vive en la raíz
    cfg = ROOT / "config.py"
    if cfg.exists():
        py_files.append((cfg, "config"))

    known_slugs: set[str] = set()
    parsed: dict[str, tuple[str, Path, str]] = {}  # slug -> (src, path, pkg)
    for f, pkg in py_files:
        rel = f.relative_to(ROOT)
        slug = _slug_backend(rel)
        known_slugs.add(slug)
        try:
            src = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            src = ""
        parsed[slug] = (src, f, pkg)

    for slug, (src, f, pkg) in parsed.items():
        rel = f.relative_to(ROOT)
        n = Node(slug, "/".join(rel.with_suffix("").parts), "module", pkg,
                 "backend", str(rel), _first_docline(src))
        nodes[slug] = n
        # imports
        for tgt in _backend_imports(src, known_slugs):
            if tgt != slug:
                n.links.add(tgt)
        # colecciones
        for coll_id in _collections_in(src):
            n.links.add(coll_id)


def _backend_imports(src: str, known: set[str]) -> set[str]:
    out: set[str] = set()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                _match_module(a.name, known, out)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # imports relativos: poco frecuentes acá, los saltamos
                continue
            mod = node.module or ""
            if not mod:
                continue
            _match_module(mod, known, out)
            for a in node.names:  # from pkg import submod
                _match_module(f"{mod}.{a.name}", known, out)
    return out


def _match_module(dotted: str, known: set[str], out: set[str]) -> None:
    if not dotted:
        return
    top = dotted.split(".", 1)[0]
    if top not in BACKEND_PKGS:
        return
    if dotted in known:
        out.add(dotted)
        return
    # subir hasta encontrar un módulo conocido (paquete)
    parts = dotted.split(".")
    while len(parts) > 1:
        parts = parts[:-1]
        cand = ".".join(parts)
        if cand in known:
            out.add(cand)
            return


_COLL_BRACKET = re.compile(
    r"\[\s*[\"'](" + "|".join(DB_NAMES) + r")[\"']\s*\]\s*\[\s*[\"']([A-Za-z0-9_]+)[\"']\s*\]"
)


def _collections_in(src: str) -> set[str]:
    out: set[str] = set()
    # patrón fuerte: client["DB"]["Coll"]
    for db, coll in _COLL_BRACKET.findall(src):
        out.add(f"db.{db}.{coll}")
    # patrón débil: nombre de colección conocida como string literal
    for db, colls in KNOWN_COLLECTIONS.items():
        for coll in colls:
            if re.search(r"[\"']" + re.escape(coll) + r"[\"']", src):
                out.add(f"db.{db}.{coll}")
    return out


def seed_collections(nodes: dict[str, Node]) -> None:
    for db, colls in KNOWN_COLLECTIONS.items():
        for coll in colls:
            cid = f"db.{db}.{coll}"
            if cid not in nodes:
                nodes[cid] = Node(cid, f"{db}.{coll}", "collection", "db",
                                  "infra", summary=f"Colección Mongo en DB {db}.")


# ───────────────────────── deploy ─────────────────────────

_EXEC_M = re.compile(r"-m\s+([A-Za-z0-9_.]+)")


def scan_deploy(nodes: dict[str, Node]) -> None:
    sysd = ROOT / "deploy" / "systemd"
    if sysd.is_dir():
        for unit in sorted(sysd.glob("*.service")):
            name = unit.stem
            sid = f"svc.{name}"
            txt = unit.read_text(encoding="utf-8", errors="ignore")
            n = Node(sid, f"systemd: {name}", "service", "deploy", "infra",
                     str(unit.relative_to(ROOT)), "Servicio systemd.")
            for m in _EXEC_M.findall(txt):
                tgt = _module_to_slug(m, nodes)
                if tgt:
                    n.links.add(tgt)
            nodes[sid] = n

    cron = ROOT / "deploy" / "crontab.txt"
    if cron.exists():
        for ln in cron.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if ln.startswith("#") or "python -m" not in ln:
                continue
            m = _EXEC_M.search(ln)
            if not m:
                continue
            mod = m.group(1)
            tgt = _module_to_slug(mod, nodes)
            cid = f"cron.{mod}"
            n = nodes.get(cid)
            if not n:
                n = Node(cid, f"cron: {mod}", "cron", "deploy", "infra",
                         "deploy/crontab.txt", "Tarea programada (cron).")
                nodes[cid] = n
            if tgt:
                n.links.add(tgt)


def _module_to_slug(mod: str, nodes: dict[str, Node]) -> str | None:
    if mod in nodes:
        return mod
    parts = mod.split(".")
    while len(parts) > 1:
        parts = parts[:-1]
        cand = ".".join(parts)
        if cand in nodes:
            return cand
    return None


# ───────────────────────── frontend ─────────────────────────

_IMPORT_RE = re.compile(r"""import\s+(?:.+?\s+from\s+)?['"]([^'"]+)['"]""", re.S)
_FETCH_RE = re.compile(r"""['"`](/api/[A-Za-z0-9_\-/\[\]]+)""")


def scan_frontend(nodes: dict[str, Node]) -> None:
    if not WEB.is_dir():
        return
    app = WEB / "src" / "app"
    comps = WEB / "src" / "components"
    lib = WEB / "src" / "lib"

    # nodos componentes / lib (para resolver imports)
    comp_ids: dict[str, str] = {}   # nombre-archivo -> id
    if comps.is_dir():
        for f in comps.rglob("*.tsx"):
            stem = f.stem
            cid = f"web.cmp.{stem}"
            comp_ids[stem] = cid
            nodes[cid] = Node(cid, f"web/components/{stem}", "component",
                              "web-component", "frontend",
                              str(f.relative_to(WEB)))
    lib_ids: dict[str, str] = {}
    if lib.is_dir():
        for f in lib.rglob("*.ts"):
            stem = f.stem
            lid = f"web.lib.{stem}"
            lib_ids[stem] = lid
            nodes[lid] = Node(lid, f"web/lib/{stem}", "lib", "web-lib",
                              "frontend", str(f.relative_to(WEB)))

    # vistas (page/layout/loading/error/...), rutas API (route.ts) y colocados
    if app.is_dir():
        for f in app.rglob("*"):
            if f.suffix not in (".ts", ".tsx"):
                continue
            rel = f.relative_to(app).parent
            route = "/".join(rel.parts) if rel.parts else "(home)"
            is_api = "api" in rel.parts
            if f.name == "route.ts":
                nid = f"web.api.{route.replace('/', '.')}"
                n = nodes.get(nid) or Node(nid, f"web /{route}  (proxy)", "route",
                                           "web-api", "frontend", str(f.relative_to(WEB)))
            else:
                # page→view, layout→layout (IDs estables); el resto, por su nombre.
                tag = {"page": "view", "layout": "layout"}.get(f.stem, f.stem)
                nid = f"web.view.{route.replace('/', '.')}.{tag}"
                n = nodes.get(nid) or Node(nid, f"web /{route}  ({tag})", "view",
                                           "web-view", "frontend", str(f.relative_to(WEB)))
            nodes[nid] = n
            _wire_frontend(f, n, comp_ids, lib_ids, nodes, is_api)

    # wirear los PROPIOS componentes y libs (sus imports componente→componente,
    # componente→lib, componente→/api). Sin esto un componente que no es usado
    # directo por una vista pero sí por otro componente quedaba orphan. Los
    # comp_ids/lib_ids ya están todos poblados → los imports resuelven.
    if comps.is_dir():
        for f in comps.rglob("*.tsx"):
            cid = comp_ids.get(f.stem)
            if cid:
                _wire_frontend(f, nodes[cid], comp_ids, lib_ids, nodes, is_api=False)
    if lib.is_dir():
        for f in lib.rglob("*.ts"):
            lid = lib_ids.get(f.stem)
            if lid:
                _wire_frontend(f, nodes[lid], comp_ids, lib_ids, nodes, is_api=False)

    # archivos sueltos en src/ (ej. proxy.ts) — nada queda afuera
    srcroot = WEB / "src"
    if srcroot.is_dir():
        for f in sorted(srcroot.iterdir()):
            if not (f.is_file() and f.suffix in (".ts", ".tsx")):
                continue
            lid = f"web.lib.{f.stem}"
            if lid not in nodes:
                lib_ids[f.stem] = lid
                nodes[lid] = Node(lid, f"web/{f.stem}", "lib", "web-lib",
                                  "frontend", str(f.relative_to(WEB)))
                _wire_frontend(f, nodes[lid], comp_ids, lib_ids, nodes, False)


def _wire_frontend(f: Path, n: Node, comp_ids, lib_ids, nodes, is_api: bool) -> None:
    try:
        src = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for imp in _IMPORT_RE.findall(src):
        stem = imp.rstrip("/").split("/")[-1]
        if imp.startswith("@/components") or "/components/" in imp:
            if stem in comp_ids:
                n.links.add(comp_ids[stem])
        elif imp.startswith("@/lib") or "/lib/" in imp:
            if stem in lib_ids:
                n.links.add(lib_ids[stem])
    # edge back↔front: URL /api/... que el front consume → router backend
    for url in _FETCH_RE.findall(src):
        tgt = _endpoint_to_router(url, nodes)
        if tgt:
            n.links.add(tgt)


def _endpoint_to_router(url: str, nodes: dict[str, Node]) -> str | None:
    segs = [s for s in url.strip("/").split("/") if s and not s.startswith("[")]
    if segs and segs[0] == "api":
        segs = segs[1:]
    # probar api.routers.<seg> y api.routers.manager.<seg>
    for i in range(len(segs), 0, -1):
        cand = "api.routers." + ".".join(segs[:i])
        if cand in nodes:
            return cand
        candm = "api.routers.manager." + ".".join(segs[:i])
        if candm in nodes:
            return candm
    return None


# ───────────────────────── emisión ─────────────────────────

LAYER_ORDER = [
    ("core", "🧱 core — infraestructura"),
    ("quant", "📐 quant — cálculo puro"),
    ("engines", "⚙️ engines — motores WS→Mongo"),
    ("jobs", "⏱️ jobs — batch / cron"),
    ("api", "🌐 api — services · routers · mcp"),
    ("partner_api", "🤝 partner_api"),
    ("config", "⚙️ config"),
    ("db", "🗄️ base — colecciones Mongo"),
    ("deploy", "🚀 deploy — servicios + crons"),
    ("web-view", "🖥️ web — vistas"),
    ("web-component", "🧩 web — componentes"),
    ("web-api", "🔌 web — rutas API (proxy)"),
    ("web-lib", "📚 web — lib"),
]


def _backlinks(nodes: dict[str, Node]) -> dict[str, set[str]]:
    back: dict[str, set[str]] = defaultdict(set)
    for n in nodes.values():
        for tgt in n.links:
            back[tgt].add(n.id)
    return back


def emit(nodes: dict[str, Node]) -> dict[Path, str]:
    files: dict[Path, str] = {}
    back = _backlinks(nodes)

    for n in sorted(nodes.values(), key=lambda x: x.id):
        files[VAULT / "nodes" / f"{n.id}.md"] = _render_node(n, nodes, back)

    # índices por capa (MOC)
    by_layer: dict[str, list[Node]] = defaultdict(list)
    for n in nodes.values():
        by_layer[n.layer].append(n)
    for layer, title in LAYER_ORDER:
        ns = sorted(by_layer.get(layer, []), key=lambda x: x.id)
        if not ns:
            continue
        files[VAULT / f"_{layer}.md"] = _render_index(layer, title, ns)

    files[VAULT / "Home.md"] = _render_home(nodes, by_layer)
    files[VAULT / "README.md"] = _render_readme(nodes)
    files[VAULT / "_manifest.json"] = _manifest(nodes)
    return files


def _manifest(nodes: dict[str, Node]) -> str:
    """Índice máquina-legible del vault (sirve para re-enriquecer la prosa)."""
    data = [
        {"id": n.id, "type": n.ntype, "layer": n.layer, "repo": n.repo,
         "path": n.path, "summary": n.summary, "links": sorted(n.links)}
        for n in sorted(nodes.values(), key=lambda x: x.id)
    ]
    return json.dumps(data, ensure_ascii=False, indent=0)


def _render_node(n: Node, nodes: dict[str, Node], back: dict[str, set[str]]) -> str:
    out = ["---",
           f"id: {n.id}",
           f"type: {n.ntype}",
           f"layer: {n.layer}",
           f"repo: {n.repo}",
           f"tags: [{n.ntype}, {n.layer}, {n.repo}]"]
    if n.path:
        out.append(f"path: {n.path}")
    out += ["---", "", f"# {n.title}", ""]
    if n.summary:
        out.append(f"> {n.summary}")
        out.append("")
    if n.path:
        out.append(f"**Archivo:** `{n.path}`")
        out.append("")

    out.append("## Qué hace")
    sidecar = PROSE / f"{n.id}.md"
    if sidecar.exists():
        out.append(sidecar.read_text(encoding="utf-8").strip())
    else:
        out.append("_(pendiente de enriquecimiento)_")
    out.append("")

    fwd = sorted(t for t in n.links if t in nodes)
    if fwd:
        out.append("## Usa / conecta con →")
        out += [f"- [[{t}]]  ·  _{nodes[t].ntype}_" for t in fwd]
        out.append("")
    rev = sorted(b for b in back.get(n.id, ()) if b in nodes)
    if rev:
        out.append("## Lo usan (backlinks) ←")
        out += [f"- [[{b}]]  ·  _{nodes[b].ntype}_" for b in rev]
        out.append("")
    if not fwd and not rev:
        out.append("_Sin conexiones detectadas mecánicamente._\n")
    return "\n".join(out)


def _render_index(layer: str, title: str, ns: list[Node]) -> str:
    out = [f"# {title}", "", f"{len(ns)} notas.", ""]
    for n in ns:
        s = f" — {n.summary}" if n.summary else ""
        out.append(f"- [[{n.id}]]{s}")
    return "\n".join(out) + "\n"


def _render_home(nodes: dict[str, Node], by_layer: dict[str, list[Node]]) -> str:
    out = ["# 🧠 acaquant — el cerebro del sistema", "",
           "Mapa vivo y navegable de TODO: backend + frontend + base + deploy.",
           "Generado por `scripts/gen_obsidian.py` (no editar a mano).", "",
           "Abrí el **graph view** (Ctrl/Cmd+G) para ver cómo conecta todo con todo.",
           "", f"**{len(nodes)} nodos** en total.", "", "## Mapas por capa", ""]
    for layer, title in LAYER_ORDER:
        ns = by_layer.get(layer, [])
        if ns:
            out.append(f"- [[_{layer}|{title}]]  ({len(ns)})")
    out += ["", "## Flujo de datos (alto nivel)", "",
            "```",
            "pyRofex WS → engines/ → Trading.MarketSnapshot → api/services → api/routers",
            "                                                       ↓",
            "        acaquant-web (vistas) ← rutas API (proxy) ← FastAPI",
            "```", ""]
    return "\n".join(out)


def _render_readme(nodes: dict[str, Node]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for n in nodes.values():
        counts[n.ntype] += 1
    out = ["# Vault acaquant — cómo usarlo", "",
           "1. Instalá [Obsidian](https://obsidian.md).",
           "2. *Open folder as vault* → elegí `docs/vault/`.",
           "3. Abrí `Home.md` y el **graph view**.", "",
           "## Qué hay adentro", ""]
    for t, c in sorted(counts.items(), key=lambda x: -x[1]):
        out.append(f"- **{c}** {t}")
    out += ["", "## Regenerar", "",
            "```bash",
            "python -m scripts.gen_obsidian          # regenera",
            "python -m scripts.gen_obsidian --check  # CI: falla si quedó stale",
            "```", "",
            "La sección _Qué hace_ de cada nota la completa la pasada de",
            "enriquecimiento con IA; el resto (archivos, links, backlinks) es",
            "determinista y se regenera del código.", ""]
    return "\n".join(out)


# ───────────────────────── main ─────────────────────────

def build() -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    scan_backend(nodes)
    seed_collections(nodes)
    scan_deploy(nodes)
    scan_frontend(nodes)
    _prune_orphan_front(nodes)
    return nodes


# Sólo se podan estos tipos del front (component/lib). El backend, db, deploy,
# view y route forman el esqueleto del cerebro aunque queden sueltos → no se tocan.
_PRUNABLE_TYPES = {"component", "lib"}


def _prune_orphan_front(nodes: dict[str, Node]) -> None:
    """Elimina componentes/libs del front sin NINGUNA conexión (minimalismo).

    Un component/lib que no apunta a nada existente ni es apuntado por nadie es
    ruido flotante. Se quitan tras armar todo el grafo. No hace falta limpiar los
    links salientes de otros nodos: el render filtra con `if t in nodes`.
    """
    back = _backlinks(nodes)
    to_drop = []
    for n in nodes.values():
        if n.ntype not in _PRUNABLE_TYPES:
            continue
        fwd = [t for t in n.links if t in nodes]
        rev = [b for b in back.get(n.id, ()) if b in nodes]
        if not fwd and not rev:
            to_drop.append(n.id)
    for nid in to_drop:
        del nodes[nid]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="No escribe; falla (exit 1) si el vault quedó desincronizado.")
    args = ap.parse_args()

    web_present = WEB.is_dir()
    nodes = build()
    files = emit(nodes)

    if args.check and web_present:
        # Check COMPLETO (ambos repos): igualdad byte a byte → el vault commiteado
        # refleja exactamente el código.
        stale = []
        for path, content in files.items():
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(path.relative_to(ROOT))
        existing = {p for p in VAULT.rglob("*.md") if PROSE not in p.parents}
        extra = existing - set(files)
        if stale or extra:
            print("Vault DESINCRONIZADO. Corré: python -m scripts.gen_obsidian")
            for p in sorted(stale)[:20]:
                print(f"  stale: {p}")
            for p in sorted(extra)[:20]:
                print(f"  extra: {p.relative_to(ROOT)}")
            return 1
        print(f"Vault OK ({len(files)} archivos · check completo).")
        return 0

    if args.check:
        # Check de COBERTURA (CI sin el repo del front): el contenido de las notas
        # de backend depende de backlinks del front → no se puede comparar byte a
        # byte. Verificamos lo que importa para "nada afuera": que cada nodo
        # backend/infra tenga su nota, y que no sobren notas backend (archivo borrado).
        ndir = VAULT / "nodes"
        missing = [n.id for n in nodes.values()
                   if not (ndir / f"{n.id}.md").exists()]
        extra = [p.stem for p in ndir.glob("*.md")
                 if not p.stem.startswith("web.") and p.stem not in nodes]
        if missing or extra:
            print("Vault SIN COBERTURA. Corré: python -m scripts.gen_obsidian")
            for i in sorted(missing)[:20]:
                print(f"  falta nota: {i}")
            for i in sorted(extra)[:20]:
                print(f"  nota huérfana: {i}")
            return 1
        print(f"Vault OK (cobertura backend: {len(nodes)} nodos presentes).")
        return 0

    # Regenera estructura SIN tocar _prose/ (la prosa de IA persiste).
    for old in VAULT.rglob("*.md"):
        if PROSE not in old.parents:
            old.unlink()
    # Limpia sidecars huérfanos (nodos que dejaron de existir, ej. scripts borrados).
    if web_present and PROSE.is_dir():
        valid = set(nodes)
        for sc in PROSE.glob("*.md"):
            if sc.stem not in valid:
                sc.unlink()
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    n_nodes = len(nodes)
    n_edges = sum(len(n.links) for n in nodes.values())
    print(f"Vault generado en {VAULT.relative_to(ROOT)}/")
    print(f"  {n_nodes} nodos · {n_edges} edges · {len(files)} archivos .md")
    by_type: dict[str, int] = defaultdict(int)
    for n in nodes.values():
        by_type[n.ntype] += 1
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {c:>4}  {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
