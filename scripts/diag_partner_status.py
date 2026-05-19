"""Diag — estado real de la Partner API en producción.

Responde tres preguntas, mirando solo Mongo (no toca la API ni el systemd):

1. ¿Está configurado el universo de cuentas a exportar? (`config.PARTNER_EXPORT_CUENTAS`)
2. ¿Se está corriendo el job? (último `exported_at` por cuenta en `ACAPortfolio.Cartera`)
3. ¿Hay data reciente? (fechas distintas de los últimos 10 días + total de docs)

Si la 2 dice "última corrida hace varios días" o "vacío" → el cron no se aplicó
en el Droplet (probable: falta `crontab /root/TradingAV/deploy/crontab.txt`) o
faltan las env vars `PARTNER_MONGO_URI` / `PARTNER_JWT_SECRET`.

Uso:
    python -m scripts.diag_partner_status
"""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

import config
from core.mongo import get_mongo_client_read


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - dt
    if delta.total_seconds() < 60:
        ago = f"{int(delta.total_seconds())}s"
    elif delta.total_seconds() < 3600:
        ago = f"{int(delta.total_seconds() / 60)}m"
    elif delta.total_seconds() < 86400:
        ago = f"{delta.total_seconds() / 3600:.1f}h"
    else:
        ago = f"{delta.days}d"
    return f"{dt.isoformat(timespec='seconds')}  (hace {ago})"


def main() -> int:
    print("=" * 70)
    print("DIAG · Partner API — ACAPortfolio.Cartera")
    print("=" * 70)

    # ── 1) Universo configurado ────────────────────────────────────────────
    cuentas_cfg = getattr(config, "PARTNER_EXPORT_CUENTAS", None) or []
    print(f"\n[1] PARTNER_EXPORT_CUENTAS = {cuentas_cfg}")
    if not cuentas_cfg:
        print("    ⚠ La lista está vacía → el job aborta sin exportar nada.")
        # Igual seguimos: queremos saber si HAY data vieja de antes.

    # ── 2) Última corrida por cuenta ───────────────────────────────────────
    db = get_mongo_client_read()["ACAPortfolio"]
    col = db["Cartera"]
    total = col.estimated_document_count()
    print(f"\n[2] Cartera — total docs: {total:,}")
    if total == 0:
        print("    ⚠ Colección vacía. El job nunca insertó nada (o se borró).")
        print("    Esperable si:")
        print("      - falta correr `python -m jobs.partner_export` por primera vez")
        print("      - faltan env vars PARTNER_MONGO_URI / PARTNER_JWT_SECRET")
        print("      - el cron no se aplicó (verificar `crontab -l` en Droplet)")
        return 0

    # Último exported_at GLOBAL
    last_doc = col.find_one(
        {}, {"exported_at": 1, "id_cuenta": 1}, sort=[("exported_at", -1)]
    )
    print(f"    Último exported_at global: "
          f"{_fmt_dt((last_doc or {}).get('exported_at'))}")

    # Último exported_at POR cuenta (de las configuradas + las que aparezcan)
    cuentas_view = list(cuentas_cfg)
    extra = col.distinct("id_cuenta")
    for c in extra:
        if c not in cuentas_view:
            cuentas_view.append(c)

    print("\n    Por cuenta:")
    print(f"    {'id_cuenta':<12} {'configurada':<12} {'última corrida':<40} "
          f"{'docs hoy':<10}")
    hoy_ar = (datetime.now(UTC) - timedelta(hours=3)).date()
    for cu in cuentas_view:
        cu_str = str(cu)
        last = col.find_one(
            {"id_cuenta": cu_str},
            {"exported_at": 1, "fecha": 1},
            sort=[("exported_at", -1)],
        )
        if not last:
            print(f"    {cu_str:<12} {'sí' if cu in cuentas_cfg else 'no':<12} "
                  f"{'(sin docs)':<40} {'0':<10}")
            continue
        docs_hoy = col.count_documents(
            {"id_cuenta": cu_str, "fecha": hoy_ar.isoformat()}
        )
        flag = "sí" if cu in cuentas_cfg else "no"
        print(f"    {cu_str:<12} {flag:<12} "
              f"{_fmt_dt(last.get('exported_at')):<40} {docs_hoy:<10}")

    # ── 3) Cobertura últimos 10 días ───────────────────────────────────────
    print("\n[3] Fechas distintas de los últimos 10 días hábiles:")
    desde = (hoy_ar - timedelta(days=15)).isoformat()
    fechas = col.distinct("fecha", {"fecha": {"$gte": desde}})
    if not fechas:
        print("    (ninguna — colección sin data reciente)")
    else:
        counts = Counter()
        for f in sorted(fechas, reverse=True):
            counts[f] = col.count_documents({"fecha": f})
        for f, n in counts.most_common():
            mark = "  ← HOY" if f == hoy_ar.isoformat() else ""
            print(f"    {f}   docs={n}{mark}")

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
