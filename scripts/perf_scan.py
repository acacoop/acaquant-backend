"""perf_scan.py — Static analysis para anti-patterns de queries Mongo.

Scanea dashboard/, engines/, jobs/ y reporta:
  PERF001  list(col.find(...)) sin projection      → carga docs completos
  PERF002  find/find_one/aggregate dentro de un for → posible N+1
  PERF003  count_documents({}) con filtro vacío     → escaneo total
  PERF004  misma query repetida en una función      → candidato a cachear

Uso:
    python -m scripts.perf_scan            # informativo (exit 0)
    python -m scripts.perf_scan --strict   # exit 1 si hay findings

Escape: agregar `# noqa: PERF00X` al final de la línea suprime ese finding.
"""

import ast
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("dashboard", "engines", "jobs")
SKIP_PARTS = {"__pycache__", ".venv", "venv"}
MONGO_METHODS = {"find", "find_one", "aggregate", "count_documents"}


def _parent_map(tree):
    m = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            m[id(c)] = p
    return m


def _mongo_method(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in MONGO_METHODS:
            return node.func.attr
    return None


def _parent(node, pmap):
    return pmap.get(id(node))


def _is_list_wrap(call, pmap):
    p = _parent(call, pmap)
    return (
        isinstance(p, ast.Call)
        and isinstance(p.func, ast.Name)
        and p.func.id == "list"
    )


def _has_projection(call):
    if any(k.arg == "projection" for k in call.keywords):
        return True
    if call.func.attr in ("find", "find_one") and len(call.args) >= 2:
        return True
    return False


def _is_empty_filter(call):
    if not call.args:
        return True
    first = call.args[0]
    return isinstance(first, ast.Dict) and not first.keys


def _inside_for(node, pmap):
    """True si node se ejecuta en cada iteración (body).
    False si está en el `iter` (se ejecuta 1 vez como cursor)."""
    cur = node
    parent = _parent(cur, pmap)
    while parent is not None:
        if isinstance(parent, (ast.For, ast.AsyncFor)):
            if cur in parent.body:
                return True
            # cur está en parent.iter o parent.target → sigo subiendo por si
            # hay un for externo que sí contiene todo esto en su body.
        cur = parent
        parent = _parent(cur, pmap)
    return False


def _enclosing_func(node, pmap):
    cur = _parent(node, pmap)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = _parent(cur, pmap)
    return None


def _noqa(lines, lineno, rule):
    if lineno <= 0 or lineno > len(lines):
        return False
    line = lines[lineno - 1]
    if "# noqa" not in line:
        return False
    tail = line.split("# noqa", 1)[1].strip(": \t")
    return tail == "" or rule in tail


def scan_file(path):
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError):
        return []
    lines = source.splitlines()
    pmap = _parent_map(tree)
    rel = path.relative_to(ROOT).as_posix()
    findings = []
    func_queries = defaultdict(list)

    for node in ast.walk(tree):
        method = _mongo_method(node)
        if not method:
            continue

        if method in ("find", "find_one") and not _has_projection(node) and _is_list_wrap(node, pmap):
            if not _noqa(lines, node.lineno, "PERF001"):
                findings.append((rel, node.lineno, "PERF001",
                    f"list({method}(...)) sin projection — carga docs completos"))

        if _inside_for(node, pmap):
            if not _noqa(lines, node.lineno, "PERF002"):
                findings.append((rel, node.lineno, "PERF002",
                    f"{method}() dentro de un for — posible N+1"))

        if method == "count_documents" and _is_empty_filter(node):
            if not _noqa(lines, node.lineno, "PERF003"):
                findings.append((rel, node.lineno, "PERF003",
                    "count_documents({}) escanea toda la colección — usar estimated_document_count()"))

        fname = _enclosing_func(node, pmap)
        if fname:
            sig = ast.dump(node, annotate_fields=False)
            func_queries[(fname, sig)].append(node.lineno)

    for (fname, _sig), linenos in func_queries.items():
        if len(linenos) > 1:
            head = min(linenos)
            if not _noqa(lines, head, "PERF004"):
                findings.append((rel, head, "PERF004",
                    f"query Mongo repetida en {fname}() líneas {sorted(linenos)} — considerar cachear"))

    return findings


def main():
    strict = "--strict" in sys.argv
    findings = []
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(part in SKIP_PARTS for part in p.parts):
                continue
            findings.extend(scan_file(p))

    findings.sort(key=lambda f: (f[0], f[1], f[2]))
    for path, line, rule, msg in findings:
        print(f"{path}:{line}  [{rule}] {msg}")

    if findings:
        counts = Counter(f[2] for f in findings)
        print()
        print(f"Total: {len(findings)} findings")
        for rule, n in sorted(counts.items()):
            print(f"  {rule}: {n}")
    else:
        print("perf_scan: sin findings.")

    return 1 if (strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
