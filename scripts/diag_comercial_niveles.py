"""diag_comercial_niveles.py — ¿los filtros de nivel de OPERADORES filtran de verdad? (read-only)

Reproduce EXACTO lo que hace el backend de /api/operaciones/comercial/operador:
corre el scope de cuentas activas SIN filtro y CON un valor real de cada nivel
(nivel_1..nivel_5 + referido), y muestra si el conteo de cuentas efectivamente baja.

- Si BAJA → el backend filtra bien → el problema está en el front o en el deploy
  (Droplet con código viejo, o Vercel stale, o cache del browser).
- Si NO baja (mismo conteo) → el filtro NO impacta en el backend → hay que mirar ahí.

100% lectura. No escribe nada.

Uso:
    python -m scripts.diag_comercial_niveles
"""
from __future__ import annotations

from collections import Counter

from api.services import comercial_sql as cs

NIVELES = ("nivel_1", "nivel_2", "nivel_3", "nivel_4", "nivel_5", "referido")


def _ids(**kw) -> list[str]:
    """_ids_operador con todo por kwargs (sin operador = todas las activas)."""
    return cs._ids_operador(
        [],
        nivel_1=kw.get("nivel_1"), nivel_2=kw.get("nivel_2"), nivel_3=kw.get("nivel_3"),
        nivel_4=kw.get("nivel_4"), nivel_5=kw.get("nivel_5"), referido=kw.get("referido"),
    )


def main() -> None:
    combos = cs.dimensiones_comercial()["combos"]
    base = _ids()
    print(f"\n═══ Cuentas activas SIN filtro: {len(base)} ═══")
    print(f"(combos operador×niveles distintos: {len(combos)})\n")

    for nivel in NIVELES:
        # Distribución de valores del nivel (sumando cuentas por combo).
        cnt: Counter = Counter()
        for c in combos:
            v = c.get(nivel)
            if v:
                cnt[v] += c.get("n_cuentas", 0) or 0
        if not cnt:
            print(f"{nivel:9}  (sin valores cargados en comitentes)")
            continue

        val, n_esperado = cnt.most_common(1)[0]
        ids = _ids(**{nivel: [val]})
        n = len(ids)
        ok = n < len(base)
        print(f"{nivel:9}  ={val!r:>28}  → {n:>4} cuentas "
              f"(esperado ~{n_esperado})  "
              f"{'✅ FILTRA' if ok else '❌ NO CAMBIA (= al total)'}")
        # Distintos valores del nivel (para ver que hay más de uno).
        print(f"            valores distintos: {len(cnt)}  "
              f"({', '.join(list(cnt)[:6])}{'…' if len(cnt) > 6 else ''})")


if __name__ == "__main__":
    main()
