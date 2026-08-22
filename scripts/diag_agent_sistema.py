"""scripts/diag_agent_sistema.py — QUÉ ESTÁ CANTANDO EL MONITOR DEL SISTEMA.

**Read-only. No escribe una fila.**

La primera corrida dejó **58 hallazgos** de `alcance='sistema'` y ese número, solo,
no dice nada: puede ser que la base esté rota o que el detector esté midiendo mal.
La diferencia entre las dos cosas se ve agrupando — 50 tablas atrasadas de tiempo
real, todas con el mismo atraso, no son 50 problemas: es UN problema del detector.

Es el mismo paso de calibración que se hizo con la relevada de bonos: **el número
que importa no es cuántos encontró, es cuántos de esos son de verdad.**

    python -m scripts.diag_agent_sistema
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict

from core.postgres import get_pool


def _humano(seg) -> str:
    try:
        s = float(seg)
    except (TypeError, ValueError):
        return "—"
    if s < 3600:
        return f"{int(s // 60)} min"
    if s < 86400:
        return f"{s / 3600:.1f} h".replace(".", ",")
    return f"{s / 86400:.1f} d".replace(".", ",")


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tipo, ticker, regla, severidad, motivo, evidencia "
            "FROM agente.av_agent_hallazgos WHERE alcance = 'sistema' "
            "ORDER BY tipo, ticker")
        hall = [dict(zip(["tipo", "ticker", "regla", "severidad", "motivo",
                          "evidencia"], r, strict=True)) for r in cur.fetchall()]
        cur.execute("SELECT cadencia, count(*) FROM manager.tabla_perfil "
                    "GROUP BY cadencia ORDER BY 2 DESC")
        cadencias = cur.fetchall()

    print(f"\n{len(hall)} hallazgos con alcance 'sistema'\n" + "=" * 62)
    for tipo, n in Counter(h["tipo"] for h in hall).most_common():
        print(f"  {tipo:18} {n:>4}")

    total = sum(n for _, n in cadencias)
    print(f"\nLAS {total} TABLAS, POR CADENCIA MEDIDA\n" + "=" * 62)
    for cad, n in cadencias:
        print(f"  {cad!s:18} {n:>4}")

    # ── El corte que decide si esto es señal o ruido ──────────────────────────
    # Si las atrasadas son casi todas de la misma cadencia y con atrasos
    # parecidos, no son N problemas: es el detector juzgándolas fuera del horario
    # en el que esas tablas escriben. Es el mismo criterio de VENTANA que ya usa
    # el detector de motores («fuera de rueda no está caído, está apagado»).
    quietas = [h for h in hall if h["tipo"] == "tabla_quieta"]
    if quietas:
        print(f"\nLAS {len(quietas)} TABLAS QUIETAS, POR CADENCIA\n" + "=" * 62)
        por_cad = defaultdict(list)
        for h in quietas:
            por_cad[(h["evidencia"] or {}).get("cadencia") or "?"].append(h)
        for cad, hs in sorted(por_cad.items(), key=lambda x: -len(x[1])):
            atrasos = [(h["evidencia"] or {}).get("atraso_s") or 0 for h in hs]
            tope = (hs[0]["evidencia"] or {}).get("tope_s")
            print(f"\n  {cad}  ·  {len(hs)} tablas  ·  tolerancia "
                  f"{_humano(tope)}  ·  atraso {_humano(min(atrasos))} → "
                  f"{_humano(max(atrasos))}")
            for h in sorted(hs, key=lambda x: -((x["evidencia"] or {}).get("atraso_s") or 0)):
                e = h["evidencia"] or {}
                print(f"      {h['ticker']:44} hace {_humano(e.get('atraso_s')):>9}"
                      f"  ({e.get('col_fecha')})")

    otros = [h for h in hall if h["tipo"] != "tabla_quieta"]
    if otros:
        print("\nEL RESTO\n" + "=" * 62)
        for h in otros:
            print(f"  [{h['severidad']}] {h['tipo']}/{h['regla']} · "
                  f"{h['ticker']}\n      {h['motivo']}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
