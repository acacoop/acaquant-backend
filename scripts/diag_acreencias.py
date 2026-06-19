"""diag_acreencias.py — validar la vista COBROS FUTUROS (read-only).

Compara el total de CashFlow.Acreencias SIN filtro (lo que muestra OPERADORES →
Cobros Futuros, comercial.py) vs CON filtro `fecha_pago >= hoy` (lo correcto, lo que
usan las funciones canónicas acreencias.por_dia/del_cliente). La diferencia = pagos
YA VENCIDOS que la vista de operadores está sumando de más.

También chequea frescura (último generado_at + snapshot de tenencia usado) y lista
las cuentas más afectadas.

    python -m scripts.diag_acreencias
"""
from __future__ import annotations

from datetime import date

from core.mongo import get_mongo_client_read

HOY = date.today().isoformat()


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def main() -> int:
    col = get_mongo_client_read()["CashFlow"]["Acreencias"]

    n = col.estimated_document_count()
    print(f"═══ CashFlow.Acreencias — validación COBROS FUTUROS (hoy = {HOY}) ═══\n")
    print(f"documentos: {n:,}")
    if not n:
        print("⚠ COLECCIÓN VACÍA → el cron jobs.acreencias no escribió (o falló). Revisar.")
        return 0

    # Frescura: cuándo se generó y con qué snapshot de tenencia.
    ult = next(iter(col.find({}, {"_id": 0, "generado_at": 1, "snapshot": 1})
                    .sort("generado_at", -1).limit(1)), {})
    print(f"último generado_at: {ult.get('generado_at')}")
    print(f"snapshot tenencia usado: {ult.get('snapshot')}")

    # Rango de fechas de pago.
    fmin = next(iter(col.find({}, {"fecha_pago": 1, "_id": 0}).sort("fecha_pago", 1).limit(1)), {})
    fmax = next(iter(col.find({}, {"fecha_pago": 1, "_id": 0}).sort("fecha_pago", -1).limit(1)), {})
    print(f"rango fecha_pago: {fmin.get('fecha_pago')} → {fmax.get('fecha_pago')}")

    # ── El núcleo: total SIN filtro (vista operadores) vs CON filtro >= hoy ──
    def _totales(match: dict) -> dict[str, float]:
        out: dict[str, float] = {}
        for d in col.aggregate([
            {"$match": match},
            {"$group": {"_id": "$moneda", "monto": {"$sum": "$monto"}}},
        ]):
            out[d["_id"] or "?"] = float(d.get("monto") or 0.0)
        return out

    todo = _totales({})
    futuro = _totales({"fecha_pago": {"$gte": HOY}})
    vencido = _totales({"fecha_pago": {"$lt": HOY}})
    n_vencido = col.count_documents({"fecha_pago": {"$lt": HOY}})

    print("\n── TOTAL por moneda ──")
    print(f"  {'moneda':<6}{'OPERADORES (sin filtro)':>26}{'CORRECTO (>= hoy)':>22}{'inflado por vencidos':>22}")
    for m in sorted(set(todo) | set(futuro)):
        t, fu = todo.get(m, 0.0), futuro.get(m, 0.0)
        print(f"  {m:<6}{_fmt(t):>26}{_fmt(fu):>22}{_fmt(t - fu):>22}")

    print(f"\ndocs con fecha_pago < hoy (YA VENCIDOS, no deberían contar): {n_vencido:,}")
    if vencido:
        print(f"  monto vencido sumado de más: {dict((m, round(v, 2)) for m, v in vencido.items())}")

    if n_vencido:
        print("\n🔴 La tab OPERADORES → Cobros Futuros está sumando pagos YA VENCIDOS.")
        print("   Fix: filtrar fecha_pago >= hoy en comercial.cobros_futuros[_cliente]")
        print("   (igual que acreencias.por_dia/del_cliente).")
    else:
        print("\n✅ Sin vencidos en la colección → el desfase no viene de la fecha.")
        print("   Si igual ves números raros, el problema está en el MONTO (cantidad×flujo,")
        print("   ajuste CER, resolver unidad→ticker) o en la tenencia. Decime qué cliente/")
        print("   número no cierra y lo recomputo contra la tenencia.")

    # Top cuentas por total (sin filtro) para cruzar a mano con la pantalla.
    print("\n── Top 10 cuentas por total (sin filtro vs >= hoy) ──")
    rows = list(col.aggregate([
        {"$group": {"_id": "$id_cuenta", "cliente": {"$first": "$cliente"},
                    "todo": {"$sum": "$monto"},
                    "fut": {"$sum": {"$cond": [{"$gte": ["$fecha_pago", HOY]}, "$monto", 0]}}}},
        {"$sort": {"todo": -1}},
        {"$limit": 10},
    ]))
    print(f"  {'id_cuenta':<12}{'cliente':<24}{'sin filtro':>16}{'>= hoy':>16}")
    for r in rows:
        cli = (r.get("cliente") or "?")[:22]
        print(f"  {str(r['_id']):<12}{cli:<24}{_fmt(r['todo']):>16}{_fmt(r['fut']):>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
