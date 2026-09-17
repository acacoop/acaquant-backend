"""scripts/diag_asistente_control.py — mide, sobre los runs GUARDADOS del asistente,
cuánto pesa cada problema del control y de la evidencia antes de tocar nada.

Lo que se vio en dos preguntas del LAB tiene cuatro caras (A: números «no
dados» que eran las cuentas habilitadas · B: citas `[E:ref:campo]` en el texto
· C: prosa que repite la tabla · D: sujeto de evidencia que es un diccionario).
Este diag dice cuántas veces pasa cada una en lo que ya corrió, así el
arreglo se ordena por peso y no por anécdota. Doc: docs/AvAgentAI.md.

Fuentes (todas de solo lectura):
  · ia.ejecuciones        — resultado de cada run: respuesta, control, agentes, tokens
  · ia.eventos_ejecucion  — ruteo (quién decidió) y resultados de herramientas (`_tabla`)
  · ia.evidencias         — sujeto, herramienta y campos de cada evidencia
  · ia.conversaciones     — los turnos anteriores al sistema de runs (respuesta + tablas)

No imprime ninguna respuesta ni nombre de titular: solo conteos, números
sueltos, títulos de tabla y tokens.

Uso (Droplet, raíz):
    python -m scripts.diag_asistente_control              # últimos 30 días
    python -m scripts.diag_asistente_control --dias 7
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter

from psycopg.rows import dict_row

from asistente import control as CTL
from asistente import permitido
from core.postgres import connect

_RE_CITA = CTL._RE_EVIDENCIA
_SALUDOS = {"hola", "buenas", "buen dia", "buen día", "buenos dias", "buenos días", "gracias",
            "ok", "dale", "chau", "hola!", "hola.", "que tal", "qué tal"}


def _titulo(texto: str) -> None:
    print(f"\n{'─' * 72}\n {texto}\n{'─' * 72}")


def _pct(parte: int, total: int) -> str:
    return f"{parte}/{total} ({100 * parte / total:.0f}%)" if total else f"{parte}/0"


def _numeros_de(texto: str) -> set[float]:
    out: set[float] = set()
    for _tok, vals in CTL.numeros(texto or ""):
        out |= vals
    return out


def _cargar(cur, dias: int) -> tuple[list[dict], dict[str, list[dict]], dict[str, list[dict]]]:
    cur.execute(
        "SELECT run_id, pregunta, estado, resultado, creada_at FROM ia.ejecuciones"
        " WHERE creada_at >= now() - make_interval(days => %s) ORDER BY creada_at",
        (dias,))
    runs = [dict(r) for r in cur.fetchall()]
    ids = [r["run_id"] for r in runs]
    eventos: dict[str, list[dict]] = {i: [] for i in ids}
    evidencias: dict[str, list[dict]] = {i: [] for i in ids}
    if ids:
        cur.execute(
            "SELECT run_id, tipo, payload FROM ia.eventos_ejecucion"
            " WHERE run_id = ANY(%s) AND tipo IN ('ruteo', 'resultado') ORDER BY id", (ids,))
        for r in cur.fetchall():
            eventos[r["run_id"]].append(dict(r["payload"] or {}))
        cur.execute(
            "SELECT run_id, ref, herramienta, acceso, sujeto, ruta, campos FROM ia.evidencias"
            " WHERE run_id = ANY(%s)", (ids,))
        for r in cur.fetchall():
            evidencias[r["run_id"]].append(dict(r))
    return runs, eventos, evidencias


def seccion_runs(runs: list[dict]) -> list[dict]:
    _titulo("1. RUNS y hallazgos del control")
    por_estado = Counter(r["estado"] for r in runs)
    print(f"  runs: {len(runs)}  " + "  ".join(f"{k}={v}" for k, v in sorted(por_estado.items())))
    con_respuesta = [r for r in runs if (r.get("resultado") or {}).get("respuesta")]
    print(f"  con respuesta: {len(con_respuesta)}")
    con_hallazgo = [r for r in con_respuesta
                    if not ((r["resultado"].get("control") or {}).get("ok", True))]
    print(f"  con al menos un hallazgo del control: {_pct(len(con_hallazgo), len(con_respuesta))}")
    tipos: Counter = Counter()
    for r in con_hallazgo:
        for h in (r["resultado"].get("control") or {}).get("hallazgos") or []:
            tipos[(h.get("control"), h.get("que_paso"))] += 1
    for (ctl, que), n in tipos.most_common():
        print(f"    {n:>4}  {ctl} · {que}")
    return con_respuesta


def seccion_numeros(runs: list[dict], eventos: dict[str, list[dict]]) -> None:
    _titulo("2. A — los números «no dados»: de dónde venían en realidad")
    cuentas = set(permitido.cuentas())
    cuentas_num = _numeros_de(" ".join(cuentas))
    clasif: Counter = Counter()
    ejemplos_resto: list[str] = []
    for r in runs:
        for h in (r["resultado"].get("control") or {}).get("hallazgos") or []:
            if h.get("control") != "numeros_fundados":
                continue
            en_tools = _numeros_de(json.dumps(
                [e.get("resultado") for e in eventos.get(r["run_id"], []) if e.get("tipo") == "resultado"],
                ensure_ascii=False, default=str))
            en_pregunta = _numeros_de(r.get("pregunta") or "")
            for tok in h.get("detalle") or []:
                vals = CTL._lecturas(tok)
                if vals & cuentas_num:
                    clasif["es una cuenta habilitada (estaba en el SYSTEM, no en el contexto)"] += 1
                elif vals & en_tools:
                    clasif["estaba en un resultado de herramienta (el control no lo vio)"] += 1
                elif vals & en_pregunta:
                    clasif["estaba en la pregunta"] += 1
                else:
                    clasif["no aparece en nada guardado (calculado o inventado)"] += 1
                    if len(ejemplos_resto) < 12:
                        ejemplos_resto.append(tok)
    total = sum(clasif.values())
    print(f"  cuentas habilitadas en .env: {len(cuentas)}   números marcados: {total}")
    for k, n in clasif.most_common():
        print(f"    {_pct(n, total):>14}  {k}")
    if ejemplos_resto:
        print(f"  ejemplos de los que no aparecen en nada: {', '.join(ejemplos_resto)}")


def seccion_evidencia(runs: list[dict], evidencias: dict[str, list[dict]]) -> None:
    _titulo("3. D — el sujeto de las evidencias")
    todas = [e for lista in evidencias.values() for e in lista]
    por_tool: Counter = Counter(e["herramienta"] for e in todas)
    dict_tool: Counter = Counter(e["herramienta"] for e in todas
                                 if str(e.get("sujeto") or "").startswith("{"))
    con_nombre = sum(1 for e in todas if str(e.get("sujeto") or "").startswith("{")
                     and "'nombre'" in str(e.get("sujeto")))
    print(f"  evidencias guardadas: {len(todas)}   con sujeto-diccionario: {sum(dict_tool.values())}"
          f"   de esas con el NOMBRE del titular adentro: {con_nombre}")
    for tool, n in por_tool.most_common():
        print(f"    {n:>5}  {tool:<28} sujeto-diccionario: {dict_tool.get(tool, 0)}")
    por_ref = {(e["run_id"], e["ref"]): e for e in todas}
    causa: Counter = Counter()
    for r in runs:
        for h in (r["resultado"].get("control") or {}).get("hallazgos") or []:
            if h.get("control") != "evidencia_fundada":
                continue
            if "sujeto" not in str(h.get("que_paso")):
                causa[f"otro hallazgo de evidencia: {h.get('que_paso')}"] += h.get("cuantos") or 0
                continue
            for det in h.get("detalle") or []:
                m = _RE_CITA.search(str(det))
                ev = por_ref.get((r["run_id"], m.group(1))) if m else None
                if ev is None:
                    causa["cita a una evidencia que no está guardada"] += 1
                elif str(ev.get("sujeto") or "").startswith("{"):
                    causa["sujeto-diccionario: IMPOSIBLE de cumplir (bug D)"] += 1
                else:
                    causa["sujeto escalar que no aparece en el texto (desajuste real)"] += 1
    total = sum(causa.values())
    print(f"  citas marcadas por el control de evidencia: {total}")
    for k, n in causa.most_common():
        print(f"    {_pct(n, total):>14}  {k}")


def _tablas_de_eventos(evs: list[dict]) -> list[tuple[str, str | None]]:
    """(título, total) de cada tabla que dibujó una herramienta en el run."""
    out = []
    for e in evs:
        if e.get("tipo") != "resultado":
            continue
        res = e.get("resultado")
        if not isinstance(res, dict):
            continue
        decl = res.get("_tabla")
        if not isinstance(decl, dict) or not decl.get("titulo"):
            continue
        total = res.get(decl["total"]) if decl.get("total") else None
        out.append((str(decl["titulo"]), None if total is None else str(total)))
    return out


def _medir_texto(pares: list[tuple[str, list[tuple[str, str | None]]]], origen: str) -> None:
    """pares = [(respuesta, [(titulo, total), ...])]."""
    if not pares:
        print(f"  {origen}: sin respuestas")
        return
    con_cita = [r for r, _ in pares if _RE_CITA.search(r)]
    citas = sum(len(_RE_CITA.findall(r)) for r, _ in pares)
    chars_citas = sum(sum(len(m.group(0)) for m in _RE_CITA.finditer(r)) for r, _ in pares)
    con_tabla = [(r, t) for r, t in pares if t]
    repite_titulo = [r for r, t in con_tabla if any(ti.casefold() in r.casefold() for ti, _ in t)]
    repite_total = [r for r, t in con_tabla
                    if any(tot and (_numeros_de(tot) & _numeros_de(r)) for _, tot in t)]
    largo_con = [len(r) for r, _ in con_tabla]
    largo_sin = [len(r) for r, t in pares if not t]
    print(f"  {origen}: {len(pares)} respuestas")
    print(f"    B  con citas [E:…] en el texto: {_pct(len(con_cita), len(pares))}"
          f"   citas: {citas}   caracteres de cita: {chars_citas}")
    print(f"    C  con tabla dibujada: {len(con_tabla)}"
          f"   repiten el título de la tabla: {_pct(len(repite_titulo), len(con_tabla))}"
          f"   repiten el total: {_pct(len(repite_total), len(con_tabla))}")
    if largo_con and largo_sin:
        print(f"    largo mediano de la prosa: con tabla {statistics.median(largo_con):.0f} chars"
              f" · sin tabla {statistics.median(largo_sin):.0f} chars")


def seccion_texto(runs: list[dict], eventos: dict[str, list[dict]], cur) -> None:
    _titulo("4. B y C — citas en el texto y prosa que repite la tabla")
    pares = [(r["resultado"]["respuesta"], _tablas_de_eventos(eventos.get(r["run_id"], [])))
             for r in runs]
    _medir_texto(pares, "runs")
    cur.execute("SELECT turnos FROM ia.conversaciones")
    pares_conv = []
    for fila in cur.fetchall():
        for t in fila["turnos"] or []:
            if t.get("respuesta"):
                tablas = [(str(x.get("titulo") or ""), None if x.get("total") is None else str(x["total"]))
                          for x in t.get("tablas") or []]
                pares_conv.append((t["respuesta"], tablas))
    _medir_texto(pares_conv, "conversaciones (todo el historial)")


def seccion_ruteo(runs: list[dict], eventos: dict[str, list[dict]]) -> None:
    _titulo("5. Ruteo — quién decidió y qué costaron los saludos")
    motivos: Counter = Counter()
    saludos = []
    for r in runs:
        ru = next((e for e in eventos.get(r["run_id"], []) if e.get("tipo") == "ruteo"), None)
        motivo = str((ru or {}).get("motivo") or "sin evento de ruteo")
        clave = ("regla" if "regla" in motivo or "señal" in motivo else
                 "eligió el modelo" if "eligió el modelo" in motivo else
                 "van todos" if "van todos" in motivo else motivo[:40])
        motivos[clave] += 1
        preg = " ".join((r.get("pregunta") or "").lower().split())
        if (preg in _SALUDOS or len(preg.split()) <= 1) and (ru or {}).get("elegidos"):
            res = r["resultado"]
            saludos.append((preg, ru["elegidos"], res.get("tokens_in", 0), res.get("tokens_out", 0)))
    for k, n in motivos.most_common():
        print(f"    {n:>4}  {k}")
    print(f"  saludos / una palabra que llegaron a un agente: {len(saludos)}")
    for preg, ag, ti, to in saludos[:10]:
        print(f"    «{preg}» → {', '.join(ag)}  {ti} in / {to} out")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=30)
    a = ap.parse_args()
    with connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        runs, eventos, evidencias = _cargar(cur, a.dias)
        print(f"ventana: últimos {a.dias} días")
        con_respuesta = seccion_runs(runs)
        seccion_numeros(con_respuesta, eventos)
        seccion_evidencia(con_respuesta, evidencias)
        seccion_texto(con_respuesta, eventos, cur)
        seccion_ruteo(con_respuesta, eventos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
