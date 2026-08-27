"""scripts/ext_ver.py — QUÉ VE un cliente de la API externa.

Muestra, exactamente, lo que le devuelve `/ext` a un accionista: sus cuentas, su
cobertura de datos y sus últimas operaciones. Sin curl, sin credenciales, sin
pelear con la terminal.

    python -m scripts.ext_ver cli_pepito_srl

Sirve para tres cosas:
  · comprobar un alta ANTES de mandarle las credenciales a nadie;
  · contestar «¿por qué no ve tal boleto?» mirando lo mismo que mira él;
  · verificar que un cambio nuestro no le rompió la vista.

Corre in-process contra la base REAL: verifica el código y los datos, no la capa
de Cloudflare (eso se prueba con un curl desde afuera, ver docs/API_EXTERNA.md).

Por default emite el token directamente para el cliente, sin pedir la API key
—que no se puede leer, en la base sólo está su hash—. Con `--api-key` recorre
además el camino real de la credencial, que es la forma de comprobar que la key
que le mandaste al accionista efectivamente sirve.

Opciones:
    --limit N     cuántas operaciones mostrar (default 5)
    --api-key K   validar además la API key de verdad
    --json        imprimir el JSON crudo de la primera operación
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from api.ext import auth as ext_auth
from api.ext import db
from config import EXT_JWT_SECRET


def _titulo(t: str) -> None:
    print(f"\n{'─' * 66}\n {t}\n{'─' * 66}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Qué ve un cliente de la API externa.")
    ap.add_argument("cliente_id", help="ej. cli_pepito_srl (lo lista --listar)")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--api-key", help="validar también la API key real")
    ap.add_argument("--json", action="store_true", help="JSON crudo de la primera operación")
    a = ap.parse_args()

    if not EXT_JWT_SECRET:
        print("ERROR: falta EXT_JWT_SECRET en el .env → /ext no está montado.")
        return 2

    cli_row = db.cliente_por_id(a.cliente_id)
    if cli_row is None:
        print(f"ERROR: no existe el cliente '{a.cliente_id}'.")
        print("Listado: python -m scripts.ext_cliente --listar")
        return 2

    cuentas = db.cuentas_autorizadas(a.cliente_id)
    print(f"\nCLIENTE   {cli_row['nombre']}  ({a.cliente_id})")
    print(f"ESTADO    {'ACTIVO' if cli_row['activo'] else 'DESACTIVADO'}"
          f"   ·   aranceles: {'SÍ los ve' if cli_row['ver_aranceles'] else 'no los ve'}")
    if not cuentas:
        print("\n⚠️  SIN CUENTAS AUTORIZADAS → todo request suyo da 403 (fail-closed).")
        print("    Arreglo: python -m scripts.ext_cliente --agregar-cuentas "
              f"{a.cliente_id} --cuentas <ID>")
        return 1

    from api.ext.app import ext_app
    http = TestClient(ext_app, raise_server_exceptions=False)

    # Token. Con --api-key recorre el camino real (valida el hash); sin ella, se
    # emite directo para el cliente: la key no se puede leer desde la base.
    if a.api_key:
        r = http.post("/v1/auth/token", json={"apiKey": a.api_key})
        if r.status_code != 200:
            print(f"\n❌ LA API KEY NO SIRVE ({r.status_code}): {r.text[:200]}")
            print("   Emitir otra: python -m scripts.ext_cliente --nueva-key "
                  f"{a.cliente_id}")
            return 1
        token = r.json()["token"]
        print("API KEY   ✓ válida")
    else:
        prefijo = _un_prefijo_vigente(a.cliente_id)
        if prefijo is None:
            print("\n⚠️  El cliente NO tiene ninguna API key vigente → no puede entrar.")
            print(f"    Arreglo: python -m scripts.ext_cliente --nueva-key {a.cliente_id}")
            return 1
        token, _, _ = _token_directo(a.cliente_id, prefijo)
        print(f"API KEY   {prefijo} (vigente; no se valida sin --api-key)")

    h = {"Authorization": f"Bearer {token}"}

    _titulo("CUENTAS QUE VE")
    r = http.get("/v1/cuentas", headers=h)
    for c in r.json().get("cuentas", []):
        print(f"  · {c['cuenta']}")

    _titulo("COBERTURA DE SUS DATOS")
    m = http.get("/v1/meta", headers=h).json()
    print(f"  primera operación : {m.get('primera_operacion')}")
    print(f"  última operación  : {m.get('ultima_operacion')}")
    print(f"  total             : {m.get('total_operaciones')} operaciones")
    print(f"  última ingesta    : {m.get('ultima_ingesta')}")
    if not m.get("total_operaciones"):
        print("\n  ⚠️  CERO OPERACIONES. Casi seguro el id_cuenta no es el correcto:")
        print("      comparalo con el de la vista NEGOCIO → OPERACIONES.")

    _titulo(f"ÚLTIMAS {a.limit} OPERACIONES (lo que él recibe)")
    r = http.get(f"/v1/operaciones?limit={a.limit}", headers=h)
    ops = r.json().get("operaciones", [])
    if not ops:
        print("  (ninguna)")
    else:
        print(f"  {'FECHA':<12}{'TICKER':<10}{'OPERACIÓN':<22}{'BRUTO':>18}  MON")
        for o in ops:
            bruto = f"{o['bruto']:,.2f}" if o["bruto"] is not None else "—"
            print(f"  {o['fecha'] or '—':<12}{(o['ticker'] or '—'):<10}"
                  f"{(o['operacion'] or '—')[:21]:<22}{bruto:>18}  {o['moneda'] or '—'}")
        if a.json:
            _titulo("JSON CRUDO DE LA PRIMERA")
            print(json.dumps(ops[0], indent=2, ensure_ascii=False))

    print(f"\n{'─' * 66}")
    print("Esto es EXACTAMENTE lo que devuelve la API. Si acá está bien, del otro")
    print("lado también — la capa de Cloudflare ya quedó verificada aparte.\n")
    return 0


def _un_prefijo_vigente(cliente_id: str) -> str | None:
    from core.postgres import get_pool
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT prefijo FROM ext.api_keys WHERE cliente_id = %s AND revocada_at IS NULL "
            "AND (expira_at IS NULL OR expira_at > now()) ORDER BY creada_at DESC LIMIT 1",
            (cliente_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def _token_directo(cliente_id: str, prefijo: str):
    """Emite un token con los MISMOS claims que `/v1/auth/token`, salteando sólo
    la comparación de la API key (que no se puede leer: en la base está el hash).
    Todo lo demás —scope, revocación, filtros— pasa por el camino real."""
    import jwt

    from config import EXT_ISSUER, EXT_TOKEN_TTL_SECONDS
    ahora = db.ahora()
    payload = {"iss": EXT_ISSUER, "sub": cliente_id, "kid": prefijo,
               "iat": int(ahora.timestamp()),
               "exp": int(ahora.timestamp()) + EXT_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, ext_auth.EXT_JWT_SECRET, algorithm="HS256"), 0, None


if __name__ == "__main__":
    raise SystemExit(main())
