"""Setup de la vista TRADING — UN solo comando, idempotente.

Deja todo listo para que /trading funcione en prod. Correr UNA vez tras el
deploy del backend (git pull + restart):

    python -m scripts.setup_trading

Hace dos cosas, las dos seguras de re-correr:

  1. Crea las tablas `manager.trading_watchlist` (watchlist por usuario) y
     `mercado.cedears_volume_history` (perfil de volumen para el RVOL).
     schema.sql no siempre está aplicado en la DB real → las creamos explícito.

  2. Le da el módulo `trading` al rol `admin` en `manager.role_matrix`, PERO
     solo si el admin ya tiene una fila propia ahí. Si no la tiene, el sistema
     cae al default del código (que YA incluye `trading`) → no hay nada que
     tocar, y escribir una fila parcial ROMPERÍA al admin. Por eso el chequeo.

Una vez confirmado que /trading anda, este script se puede borrar (REGLA #5).
"""
from __future__ import annotations

from core.postgres import get_pool

DDL = """
CREATE TABLE IF NOT EXISTS manager.trading_watchlist (
    email      text PRIMARY KEY,
    tickers    text[] NOT NULL DEFAULT '{}',
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mercado.cedears_volume_history (
    ticker_corto text NOT NULL,
    fecha        date NOT NULL,
    minuto       text NOT NULL,
    volume       numeric NOT NULL DEFAULT 0,
    PRIMARY KEY (ticker_corto, fecha, minuto)
);
CREATE INDEX IF NOT EXISTS ix_cedears_volhist_tk_fecha
    ON mercado.cedears_volume_history (ticker_corto, fecha);
"""


def _crear_tablas() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        cur.execute(
            "SELECT to_regclass('manager.trading_watchlist') IS NOT NULL, "
            "       to_regclass('mercado.cedears_volume_history') IS NOT NULL"
        )
        wl, vh = cur.fetchone()
    print(f"[1/2] tabla manager.trading_watchlist: {'OK' if wl else 'NO se creó'}")
    print(f"      tabla mercado.cedears_volume_history: {'OK' if vh else 'NO se creó'}")


def _grant_admin() -> None:
    from core import roles_sql
    from core.roles import invalidate_cache

    mods = roles_sql.get_role_modules_sql("admin")
    if not mods:
        # admin no tiene fila propia → el sistema usa el DEFAULT_MATRIX (que ya
        # incluye `trading`). NO escribimos una fila parcial: la pisaría con
        # solo lo que pongamos y dejaría al admin sin el resto de los módulos.
        print("[2/2] grant admin: el admin usa el default del código "
              "(ya incluye `trading`) → nada que hacer")
        return
    if "trading" in mods:
        print("[2/2] grant admin: `trading` ya estaba asignado → nada que hacer")
        return
    roles_sql.set_role_modules_sql("admin", [*mods, "trading"])
    invalidate_cache()  # toma efecto sin reiniciar la API
    print(f"[2/2] grant admin: `trading` agregado ({len(mods)} → {len(mods) + 1} módulos)")


def main() -> None:
    _crear_tablas()
    _grant_admin()
    print("Listo. Entrá a /trading (refrescá; el cache de roles ya se invalidó).")


if __name__ == "__main__":
    main()
