"""scripts/partner_user.py — gestión de usuarios de la Partner API.

Los usuarios viven en SQL `partner.api_users` y los consume `partner_api/auth.py`.
SQL-native (decomiso Mongo: ACAPortfolio.ApiUsers eliminada). Usa la conexión
propia de la Partner API (`partner_api.pg`), no el pool de la mesa.

Uso (desde la raíz del repo, en el Droplet):
    python -m scripts.partner_user crear        <username>
    python -m scripts.partner_user reset        <username>
    python -m scripts.partner_user habilitar    <username>
    python -m scripts.partner_user deshabilitar <username>
    python -m scripts.partner_user listar

`crear` y `reset` generan un password random y lo imprimen UNA sola vez.
Copialo y entregáselo al proveedor por un canal seguro — no queda guardado
en texto plano en ningún lado (en la DB va sólo el hash).
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import UTC, datetime

from partner_api import pg
from partner_api.security import hash_password


def _gen_password() -> str:
    return secrets.token_urlsafe(24)


def _print_credenciales(username: str, password: str) -> None:
    print("─" * 64)
    print("  CREDENCIALES — copialas AHORA, no se vuelven a mostrar.")
    print(f"  usuario:   {username}")
    print(f"  password:  {password}")
    print("─" * 64)
    print("  Entregáselas al proveedor por un canal seguro (no en un solo")
    print("  mensaje de WhatsApp/mail — usuario por un lado, password por otro).")


def _existe(conn, username: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM partner.api_users WHERE username = %s", (username,)
    ).fetchone() is not None


def crear(username: str) -> None:
    pg.ensure_schema()
    with pg.connect() as conn:
        if _existe(conn, username):
            print(f"✗ El usuario {username!r} ya existe. Usá 'reset' para "
                  f"cambiarle el password.")
            sys.exit(1)
        pw = _gen_password()
        conn.execute(
            "INSERT INTO partner.api_users (username, password_hash, enabled, created_at) "
            "VALUES (%s, %s, %s, %s)",
            (username, hash_password(pw), True, datetime.now(UTC)),
        )
        conn.commit()
    print(f"✓ Usuario {username!r} creado y habilitado.")
    _print_credenciales(username, pw)


def reset(username: str) -> None:
    pg.ensure_schema()
    with pg.connect() as conn:
        if not _existe(conn, username):
            print(f"✗ El usuario {username!r} no existe.")
            sys.exit(1)
        pw = _gen_password()
        conn.execute(
            "UPDATE partner.api_users SET password_hash = %s WHERE username = %s",
            (hash_password(pw), username),
        )
        conn.commit()
    print(f"✓ Password de {username!r} regenerado. El password anterior "
          f"dejó de funcionar.")
    _print_credenciales(username, pw)


def set_enabled(username: str, enabled: bool) -> None:
    pg.ensure_schema()
    with pg.connect() as conn:
        cur = conn.execute(
            "UPDATE partner.api_users SET enabled = %s WHERE username = %s",
            (enabled, username),
        )
        if cur.rowcount == 0:
            print(f"✗ El usuario {username!r} no existe.")
            sys.exit(1)
        conn.commit()
    print(f"✓ Usuario {username!r} {'habilitado' if enabled else 'deshabilitado'}.")


def listar() -> None:
    pg.ensure_schema()
    with pg.connect() as conn:
        rows = conn.execute(
            "SELECT username, enabled, created_at FROM partner.api_users ORDER BY username"
        ).fetchall()
    if not rows:
        print("(sin usuarios en partner.api_users)")
        return
    for username, enabled, created_at in rows:
        print(f"  {username!s:24} enabled={enabled}  creado={created_at}")


def main() -> None:
    p = argparse.ArgumentParser(description="Gestión de usuarios de la Partner API.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd in ("crear", "reset", "habilitar", "deshabilitar"):
        sub.add_parser(cmd).add_argument("username")
    sub.add_parser("listar")
    args = p.parse_args()

    if args.cmd == "crear":
        crear(args.username)
    elif args.cmd == "reset":
        reset(args.username)
    elif args.cmd == "habilitar":
        set_enabled(args.username, True)
    elif args.cmd == "deshabilitar":
        set_enabled(args.username, False)
    elif args.cmd == "listar":
        listar()


if __name__ == "__main__":
    main()
