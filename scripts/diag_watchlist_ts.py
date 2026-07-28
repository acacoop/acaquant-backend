"""diag_watchlist_ts — qué timestamp guarda/sirve la watchlist HOME y cómo lo
renderiza el front (ART).

Read-only. No escribe nada. Corré:

    python -m scripts.diag_watchlist_ts

Objetivo: verificar (no asumir) si `updated_at` / `timestamp` de home.market_quotes
están en UTC y qué hora ART muestra el front para cada símbolo. Así sabemos si el
problema de "otro huso horario" es del dato guardado o del render.
"""
from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from core.postgres import get_pool

ART = ZoneInfo("America/Argentina/Buenos_Aires")


def _as_art(raw: str | None) -> str:
    """Emula fmtAct del front: si el string no trae tz, se asume UTC; después
    se muestra en ART."""
    if not raw:
        return "—"
    s = str(raw)
    tiene_tz = s.endswith("Z") or (len(s) >= 6 and s[-6] in "+-" and s[-3] == ":")
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:  # naive → el front asume UTC
            d = d.replace(tzinfo=UTC)
        return d.astimezone(ART).strftime("%H:%M:%S")
    except Exception:
        return f"(no parseable: {s!r}, tz={tiene_tz})"


def main() -> None:
    ahora_utc = datetime.now(UTC)
    print(f"AHORA  UTC = {ahora_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"AHORA  ART = {ahora_utc.astimezone(ART).strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 96)
    print(f"{'symbol':<12} {'updated_at (raw)':<32} {'timestamp (raw)':<32} {'ART(upd)':>9}")
    print("-" * 96)

    pool = get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, data->>'updated_at' AS upd, data->>'timestamp' AS ts "
            "FROM home.market_quotes ORDER BY data->>'grupo', symbol"
        )
        rows = cur.fetchall()

    if not rows:
        print("(sin filas en home.market_quotes)")
        return

    for symbol, upd, ts in rows:
        print(f"{symbol:<12} {str(upd):<32} {str(ts):<32} {_as_art(upd):>9}")

    print("-" * 96)
    print("Lectura: si 'ART(upd)' NO coincide con AHORA ART cuando el job acaba de "
          "correr, el updated_at guardado no es UTC. Si 'updated_at (raw)' no trae "
          "'+00:00'/'Z', es naive y el front lo asume UTC.")


if __name__ == "__main__":
    main()
