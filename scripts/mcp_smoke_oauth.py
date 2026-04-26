"""mcp_smoke_oauth.py — verifica que /mcp/ acepta JWTs OAuth-issued.

Auto-emite un JWT como si lo hubiéramos generado vía /oauth/token y
después hace una request real al MCP local con ese bearer.

- Status 200 → el middleware funciona. El problema está del lado del
  cliente (Claude Desktop, claude.ai backend, etc.).
- Status 401 → hay bug en verify_access_token: el JWT que emitimos no
  pasa nuestra propia validación. Pegame el output entero.
- Status 500 → bug en algún tool / lifespan / mongo.

Uso:
    /root/TradingAV/venv/bin/python -m scripts.mcp_smoke_oauth
"""
from __future__ import annotations

import sys

import requests

from api.mcp.oauth import issue_access_token

URL = "http://localhost:8000/mcp/"


def main() -> int:
    print("=" * 60)
    print("MCP smoke test — JWT OAuth-issued contra /mcp/ local")
    print("=" * 60)

    token, ttl = issue_access_token(
        subject="smoke@local", client_id="smoke", scope="mcp:read",
    )
    print(f"\nJWT emitido (ttl={ttl}s):")
    print(f"  {token[:60]}...")

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        },
    }

    print(f"\nPOST {URL}")
    r = requests.post(
        URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json=body,
        timeout=10,
    )

    print(f"\nStatus: {r.status_code}")
    print("\nHeaders (relevantes):")
    for h in ("content-type", "mcp-session-id", "www-authenticate"):
        v = r.headers.get(h)
        if v:
            print(f"  {h}: {v}")

    print("\nBody:")
    print(r.text[:1000])
    if len(r.text) > 1000:
        print(f"... ({len(r.text)} bytes total)")

    print("\n" + "=" * 60)
    if r.status_code == 200:
        print("OK — middleware acepta JWTs OAuth-issued.")
        print("Si Claude Desktop sigue fallando, el problema NO está acá.")
        return 0
    print(f"FAIL — status {r.status_code}. Bug del backend.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
