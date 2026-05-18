"""scripts/partner_user.py — gestión de usuarios de la Partner API.

Los usuarios viven en `ACAPortfolio.ApiUsers` y los consume `partner_api/auth.py`.
Este script corre con el Mongo rw de la mesa (`core.mongo`) — el servicio
`partner_api` es read-only y NO puede crear ni modificar usuarios.

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

from core.mongo import get_mongo_client
from partner_api.security import hash_password


def _col():
    return get_mongo_client()["ACAPortfolio"]["ApiUsers"]


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


def crear(username: str) -> None:
    col = _col()
    col.create_index("username", unique=True)
    if col.find_one({"username": username}):
        print(f"✗ El usuario {username!r} ya existe. Usá 'reset' para "
              f"cambiarle el password.")
        sys.exit(1)
    pw = _gen_password()
    col.insert_one({
        "username":      username,
        "password_hash": hash_password(pw),
        "enabled":       True,
        "created_at":    datetime.now(UTC),
    })
    print(f"✓ Usuario {username!r} creado y habilitado.")
    _print_credenciales(username, pw)


def reset(username: str) -> None:
    col = _col()
    if not col.find_one({"username": username}):
        print(f"✗ El usuario {username!r} no existe.")
        sys.exit(1)
    pw = _gen_password()
    col.update_one(
        {"username": username},
        {"$set": {"password_hash": hash_password(pw)}},
    )
    print(f"✓ Password de {username!r} regenerado. El password anterior "
          f"dejó de funcionar.")
    _print_credenciales(username, pw)


def set_enabled(username: str, enabled: bool) -> None:
    r = _col().update_one({"username": username}, {"$set": {"enabled": enabled}})
    if r.matched_count == 0:
        print(f"✗ El usuario {username!r} no existe.")
        sys.exit(1)
    print(f"✓ Usuario {username!r} {'habilitado' if enabled else 'deshabilitado'}.")


def listar() -> None:
    rows = list(_col().find({}, {"_id": 0, "password_hash": 0}))
    if not rows:
        print("(sin usuarios en ACAPortfolio.ApiUsers)")
        return
    for r in rows:
        print(f"  {r.get('username')!s:24} enabled={r.get('enabled')}  "
              f"creado={r.get('created_at')}")


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
