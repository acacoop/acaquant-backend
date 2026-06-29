"""scripts/diag_mcp_sql.py — verifica que el OAuth del MCP escribe SQL (MCP_SQL=1).

Read-only. Cuenta filas en mcp.oauth_{clients,codes,tokens}. Tras reconectar el
connector con MCP_SQL=1, debería haber ≥1 client y ≥1 token vivo → confirma que el
flujo OAuth pasó a Postgres y que se puede dropear MCP.* (Mongo).

    python -m scripts.diag_mcp_sql
"""
from __future__ import annotations

from core.postgres import get_pool


def main() -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        for t in ("oauth_clients", "oauth_codes", "oauth_tokens"):
            cur.execute(f"SELECT count(*) FROM mcp.{t}")
            total = cur.fetchone()[0]
            if t == "oauth_tokens":
                cur.execute("SELECT count(*) FROM mcp.oauth_tokens WHERE expires_at > now()")
                vivos = cur.fetchone()[0]
                print(f"  mcp.{t:<14} {total:>4}  (vivos: {vivos})")
            else:
                print(f"  mcp.{t:<14} {total:>4}")
    print("\nOK si clients>=1 y tokens vivos>=1 → el connector escribió SQL → MCP.* dropeable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
