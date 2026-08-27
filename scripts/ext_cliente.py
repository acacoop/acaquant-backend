"""scripts/ext_cliente.py — ABM de los clientes de la API EXTERNA (`/ext`).

**Es el ÚNICO lugar donde se escribe un `id_cuenta` de un accionista.** En el
código del repo no hay ni va a haber uno: el permiso es un DATO
(`ext.cuentas_autorizadas`), y por eso dar de alta al segundo accionista no es
un deploy.

Uso (desde la raíz, en el Droplet):

    # Alta completa: crea el cliente, sus cuentas y su primera API key
    python -m scripts.ext_cliente --alta "PEPITO SRL" --cuentas 10452,10453

    # Con aranceles y allowlist de IPs
    python -m scripts.ext_cliente --alta "PEPITO SRL" --cuentas 10452 \
        --aranceles --ips 200.45.12.8,190.2.0.0/24

    # Ver el estado de todo
    python -m scripts.ext_cliente --listar

    # ROTACIÓN sin corte: se emite la nueva, conviven, después se revoca la vieja
    python -m scripts.ext_cliente --nueva-key cli_pepito
    python -m scripts.ext_cliente --revocar avk_live_7f3a

    # Cuentas (el permiso). Efecto INMEDIATO, sin reiniciar nada.
    python -m scripts.ext_cliente --agregar-cuentas cli_pepito --cuentas 10999
    python -m scripts.ext_cliente --quitar-cuentas  cli_pepito --cuentas 10453

    # Cortar el acceso entero de un cliente (reversible con --activar)
    python -m scripts.ext_cliente --desactivar cli_pepito

    # Deshacer un alta equivocada — baja DEFINITIVA, pide confirmación
    python -m scripts.ext_cliente --borrar cli_pepito

    # Poda de la auditoría (default 90 días)
    python -m scripts.ext_cliente --podar --dias 90

⚠️ La API key se imprime UNA SOLA VEZ y no se puede recuperar: en la base queda
sólo su hash. Si se pierde, se emite otra y se revoca la anterior.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata

sys.path.insert(0, ".")

from psycopg.rows import dict_row

from api.ext import db
from config import EXT_JWT_SECRET
from core.postgres import get_pool


def _actor() -> str:
    return os.getenv("SUDO_USER") or os.getenv("USER") or "cli"


def _slug(nombre: str) -> str:
    """'PEPITO SRL' → 'cli_pepito_srl'. Estable, legible en logs y en el token."""
    base = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower()
    return f"cli_{base or 'sin_nombre'}"[:60]


def _cuentas(arg: str | None) -> list[str]:
    return [c.strip() for c in (arg or "").split(",") if c.strip()]


def _emitir_key(cur, cliente_id: str, notas: str | None) -> str:
    key, prefijo = db.generar_key()
    cur.execute(
        "INSERT INTO ext.api_keys (prefijo, key_hash, cliente_id, creada_por, notas) "
        "VALUES (%s,%s,%s,%s,%s)",
        (prefijo, db.hash_key(key), cliente_id, _actor(), notas),
    )
    return key


def _mostrar_key(nombre: str, key: str) -> None:
    print()
    print("═" * 72)
    print(f"  API KEY de {nombre} — SE MUESTRA UNA SOLA VEZ")
    print("═" * 72)
    print(f"  {key}")
    print("═" * 72)
    print("  Mandala por un canal seguro. En la base queda sólo el hash:")
    print("  ni vos ni nadie la puede volver a leer desde acá.")
    print()


# ── operaciones ──────────────────────────────────────────────────────────────
def alta(a: argparse.Namespace) -> int:
    cuentas = _cuentas(a.cuentas)
    if not cuentas:
        print("ERROR: --alta necesita --cuentas (el permiso es la lista de cuentas).")
        return 2
    cliente_id = a.id or _slug(a.alta)
    ips = _cuentas(a.ips)

    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM ext.clientes WHERE id = %s", (cliente_id,))
        if cur.fetchone():
            print(f"ERROR: el cliente '{cliente_id}' ya existe. "
                  f"Usá --agregar-cuentas o --nueva-key.")
            return 2
        cur.execute(
            "INSERT INTO ext.clientes (id, nombre, ip_allowlist, ver_aranceles, creado_por, notas) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (cliente_id, a.alta, ips, bool(a.aranceles), _actor(), a.notas),
        )
        cur.executemany(
            "INSERT INTO ext.cuentas_autorizadas (cliente_id, id_cuenta, agregada_por) "
            "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            [(cliente_id, c, _actor()) for c in cuentas],
        )
        key = _emitir_key(cur, cliente_id, a.notas)

    print(f"✓ cliente   {cliente_id}  ({a.alta})")
    print(f"✓ cuentas   {', '.join(cuentas)}")
    print(f"✓ aranceles {'SÍ' if a.aranceles else 'no'}")
    print(f"✓ IPs       {', '.join(ips) if ips else '(sin restricción en la app)'}")
    _mostrar_key(a.alta, key)
    print("Falta, del lado de Cloudflare: crear el service token del cliente y")
    print("apuntarlo en la policy (Service Auth) de la app que cubre /ext.")
    print()
    print("⚠️  NO agregar ese token a CF_TRUSTED_SERVICE_TOKENS: esa lista da ADMIN")
    print("    sobre /api (config.py). El token del accionista no va ahí NUNCA —")
    print("    /ext no la usa: su credencial es la API key de arriba.")
    return 0


def nueva_key(a: argparse.Namespace) -> int:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT nombre FROM ext.clientes WHERE id = %s", (a.nueva_key,))
        cli = cur.fetchone()
        if cli is None:
            print(f"ERROR: no existe el cliente '{a.nueva_key}'.")
            return 2
        key = _emitir_key(cur, a.nueva_key, a.notas)
    _mostrar_key(str(cli["nombre"]), key)
    print("La key ANTERIOR sigue funcionando: las dos conviven hasta que revoques")
    print("la vieja con --revocar <prefijo>. Eso es lo que evita el corte.")
    return 0


def revocar(a: argparse.Namespace) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE ext.api_keys SET revocada_at = now() "
            "WHERE prefijo = %s AND revocada_at IS NULL",
            (a.revocar,),
        )
        n = cur.rowcount
    if not n:
        print(f"No se revocó nada: '{a.revocar}' no existe o ya estaba revocada.")
        return 1
    print(f"✓ key {a.revocar} revocada. Sus tokens vigentes murieron en el acto")
    print("  (cada request re-valida la key que lo emitió).")
    return 0


def cuentas_abm(cliente_id: str, cuentas: list[str], quitar: bool) -> int:
    if not cuentas:
        print("ERROR: falta --cuentas.")
        return 2
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM ext.clientes WHERE id = %s", (cliente_id,))
        if not cur.fetchone():
            print(f"ERROR: no existe el cliente '{cliente_id}'.")
            return 2
        if quitar:
            cur.execute(
                "DELETE FROM ext.cuentas_autorizadas WHERE cliente_id = %s AND id_cuenta = ANY(%s)",
                (cliente_id, cuentas),
            )
        else:
            cur.executemany(
                "INSERT INTO ext.cuentas_autorizadas (cliente_id, id_cuenta, agregada_por) "
                "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                [(cliente_id, c, _actor()) for c in cuentas],
            )
        n = cur.rowcount
    print(f"✓ {'quitadas' if quitar else 'agregadas'} {n} cuenta(s) — efecto inmediato, sin reiniciar.")
    return 0


def borrar(cliente_id: str) -> int:
    """Baja DEFINITIVA de un cliente externo.

    El CASCADE se lleva sus keys y sus cuentas autorizadas; la auditoría se borra
    aparte (no tiene FK a propósito: un log que desaparece con lo que audita no
    sirve para nada, así que sale por decisión explícita y no de arrastre).

    Para cortarle el acceso conservando el historial está `--desactivar`, que es
    reversible. Esto es para deshacer un alta equivocada.
    """
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT c.nombre,
                   (SELECT count(*) FROM ext.api_keys k WHERE k.cliente_id = c.id) AS keys,
                   (SELECT count(*) FROM ext.cuentas_autorizadas x WHERE x.cliente_id = c.id) AS cuentas,
                   (SELECT count(*) FROM ext.requests_log l WHERE l.cliente_id = c.id) AS requests
              FROM ext.clientes c WHERE c.id = %s
        """, (cliente_id,))
        info = cur.fetchone()
        if info is None:
            print(f"ERROR: no existe el cliente '{cliente_id}'.")
            return 2
        print(f"\nSe va a BORRAR '{cliente_id}' ({info['nombre']}):")
        print(f"  · {info['keys']} key(s)   · {info['cuentas']} cuenta(s)   "
              f"· {info['requests']} fila(s) de auditoría")
        if input("\nEscribí el id del cliente para confirmar: ").strip() != cliente_id:
            print("Cancelado — no se borró nada.")
            return 1
        cur.execute("DELETE FROM ext.requests_log WHERE cliente_id = %s", (cliente_id,))
        cur.execute("DELETE FROM ext.clientes WHERE id = %s", (cliente_id,))
    print(f"✓ cliente {cliente_id} borrado (keys y cuentas incluidas).")
    return 0


def activar(cliente_id: str, valor: bool) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE ext.clientes SET activo = %s WHERE id = %s", (valor, cliente_id))
        n = cur.rowcount
    if not n:
        print(f"ERROR: no existe el cliente '{cliente_id}'.")
        return 2
    print(f"✓ cliente {cliente_id} → {'ACTIVO' if valor else 'DESACTIVADO'}")
    return 0


def listar() -> int:
    with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("""
            SELECT c.id, c.nombre, c.activo, c.ver_aranceles, c.ip_allowlist,
                   (SELECT count(*) FROM ext.cuentas_autorizadas x WHERE x.cliente_id = c.id) AS n_cuentas,
                   (SELECT string_agg(x.id_cuenta, ', ' ORDER BY x.id_cuenta)
                      FROM ext.cuentas_autorizadas x WHERE x.cliente_id = c.id) AS cuentas
              FROM ext.clientes c ORDER BY c.creado_at
        """)
        clientes = cur.fetchall()
        cur.execute("""
            SELECT prefijo, cliente_id, creada_at, expira_at, revocada_at, ultimo_uso
              FROM ext.api_keys ORDER BY cliente_id, creada_at
        """)
        keys = cur.fetchall()

    if not clientes:
        print("No hay clientes externos dados de alta.")
        return 0
    for c in clientes:
        estado = "ACTIVO" if c["activo"] else "DESACTIVADO"
        print(f"\n{c['id']}  —  {c['nombre']}  [{estado}]")
        print(f"  cuentas ({c['n_cuentas']}): {c['cuentas'] or '(NINGUNA → todo request da 403)'}")
        print(f"  aranceles: {'SÍ' if c['ver_aranceles'] else 'no'}"
              f"   IPs: {', '.join(c['ip_allowlist']) if c['ip_allowlist'] else '(sin restricción)'}")
        for k in [x for x in keys if x["cliente_id"] == c["id"]]:
            if k["revocada_at"]:
                est = f"revocada {k['revocada_at']:%Y-%m-%d}"
            elif k["expira_at"]:
                est = f"vence {k['expira_at']:%Y-%m-%d}"
            else:
                est = "vigente"
            uso = f"{k['ultimo_uso']:%Y-%m-%d %H:%M}" if k["ultimo_uso"] else "nunca usada"
            print(f"  key {k['prefijo']}  [{est}]  último uso: {uso}")
    return 0


def podar(dias: int) -> int:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ext.requests_log WHERE ts < now() - make_interval(days => %s)",
                    (dias,))
        n = cur.rowcount
    print(f"✓ {n} fila(s) de auditoría borradas (> {dias} días).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ABM de clientes de la API externa (/ext).")
    ap.add_argument("--alta", metavar="NOMBRE", help='Crear cliente, ej: --alta "PEPITO SRL"')
    ap.add_argument("--id", help="ID del cliente (default: derivado del nombre)")
    ap.add_argument("--cuentas", help="id_cuenta separadas por coma")
    ap.add_argument("--ips", help="IPs/CIDR permitidas, separadas por coma")
    ap.add_argument("--aranceles", action="store_true",
                    help="El cliente ve el arancel que le cobramos")
    ap.add_argument("--notas", help="Nota libre (a quién se le entregó, por qué)")
    ap.add_argument("--nueva-key", metavar="CLIENTE_ID", help="Emitir otra key (rotación)")
    ap.add_argument("--revocar", metavar="PREFIJO", help="Revocar una key")
    ap.add_argument("--agregar-cuentas", metavar="CLIENTE_ID")
    ap.add_argument("--quitar-cuentas", metavar="CLIENTE_ID")
    ap.add_argument("--desactivar", metavar="CLIENTE_ID",
                    help="Corta el acceso, conserva el historial (reversible)")
    ap.add_argument("--activar", metavar="CLIENTE_ID")
    ap.add_argument("--borrar", metavar="CLIENTE_ID",
                    help="Baja DEFINITIVA (pide confirmación). Para deshacer un alta equivocada")
    ap.add_argument("--listar", action="store_true")
    ap.add_argument("--podar", action="store_true")
    ap.add_argument("--dias", type=int, default=90)
    a = ap.parse_args()

    if not EXT_JWT_SECRET:
        print("ERROR: falta EXT_JWT_SECRET en el .env.")
        print("Es el pepper con el que se hashean las keys: sin él, una key creada")
        print("ahora no validaría después. Configurala ANTES de dar de alta a nadie.")
        return 2

    if a.alta:
        return alta(a)
    if a.nueva_key:
        return nueva_key(a)
    if a.revocar:
        return revocar(a)
    if a.agregar_cuentas:
        return cuentas_abm(a.agregar_cuentas, _cuentas(a.cuentas), quitar=False)
    if a.quitar_cuentas:
        return cuentas_abm(a.quitar_cuentas, _cuentas(a.cuentas), quitar=True)
    if a.borrar:
        return borrar(a.borrar)
    if a.desactivar:
        return activar(a.desactivar, False)
    if a.activar:
        return activar(a.activar, True)
    if a.podar:
        return podar(a.dias)
    if a.listar:
        return listar()

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
