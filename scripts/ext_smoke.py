"""scripts/ext_smoke.py — verificación END-TO-END de la API externa (`/ext`).

**Contra la base REAL, en el Droplet.** Claude no tiene acceso a producción
(REGLA #0), así que ésta es la forma de comprobar que la cadena entera
—autenticación, scope, paginación, cursor y revocación— funciona de verdad y no
sólo en los tests unitarios con la base simulada.

    python -m scripts.ext_smoke

Qué hace: crea un cliente TEMPORAL (`cli_smoke_…`), le autoriza UNA cuenta,
corre las comprobaciones y **lo borra al final, pase lo que pase** (bloque
`finally`; el borrado del cliente arrastra su key y sus cuentas por CASCADE).
No toca ningún cliente existente y no escribe una sola fila de operaciones.

Las lecturas van in-process (sin red): así verifica el código y la base sin
depender de Cloudflare. La capa de CF Access se comprueba aparte, con un curl
desde afuera — son dos cosas distintas y conviene no mezclarlas.

Opciones:
    --cuenta ID    usar esa cuenta en vez de la del último boleto ingresado
    --dejar        NO borrar el cliente temporal (para depurar a mano)
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from api.ext import db
from config import EXT_JWT_SECRET
from core.postgres import get_pool

_ID = "cli_smoke_tmp"
_AJENA = "__cuenta_que_no_es_suya__"

_ok = 0
_fallos: list[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    global _ok
    if condicion:
        _ok += 1
        print(f"  ✓ {nombre}")
    else:
        _fallos.append(nombre)
        print(f"  ✗ {nombre}{('  → ' + detalle) if detalle else ''}")


def _cuenta_de_prueba() -> str | None:
    """La cuenta del último boleto ingresado.

    `ORDER BY id DESC LIMIT 1` usa la PK: es un lookup, no un scan de la tabla
    (REGLA #4 — nada de recorrer 490k filas para elegir un ejemplo).
    """
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id_cuenta FROM operaciones "
                    "WHERE id_cuenta IS NOT NULL ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None


def _crear(cuenta: str) -> str:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ext.clientes WHERE id = %s", (_ID,))  # restos de una corrida cortada
        cur.execute(
            "INSERT INTO ext.clientes (id, nombre, ver_aranceles, creado_por, notas) "
            "VALUES (%s,'SMOKE TEST (temporal)',true,'ext_smoke',"
            "'creado y borrado por scripts/ext_smoke.py')",
            (_ID,),
        )
        cur.execute("INSERT INTO ext.cuentas_autorizadas (cliente_id, id_cuenta, agregada_por) "
                    "VALUES (%s,%s,'ext_smoke')", (_ID, cuenta))
        key, prefijo = db.generar_key()
        cur.execute("INSERT INTO ext.api_keys (prefijo, key_hash, cliente_id, creada_por) "
                    "VALUES (%s,%s,%s,'ext_smoke')", (prefijo, db.hash_key(key), _ID))
    return key


def _borrar() -> None:
    with get_pool().connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM ext.clientes WHERE id = %s", (_ID,))       # CASCADE: keys + cuentas
        cur.execute("DELETE FROM ext.requests_log WHERE cliente_id = %s", (_ID,))


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke end-to-end de /ext contra la base real.")
    ap.add_argument("--cuenta", help="id_cuenta a usar (default: la del último boleto)")
    ap.add_argument("--dejar", action="store_true", help="no borrar el cliente temporal")
    a = ap.parse_args()

    if not EXT_JWT_SECRET:
        print("ERROR: falta EXT_JWT_SECRET en el .env → /ext no está montado.")
        return 2

    cuenta = a.cuenta or _cuenta_de_prueba()
    if not cuenta:
        print("ERROR: no hay operaciones en la base para probar.")
        return 2

    print(f"\nSMOKE /ext — cuenta de prueba: {cuenta}\n")
    from api.ext.app import ext_app
    cli = TestClient(ext_app, raise_server_exceptions=False)

    key = _crear(cuenta)
    try:
        # 1 — la API key se cambia por un token
        r = cli.post("/v1/auth/token", json={"apiKey": key})
        check("la API key devuelve un token", r.status_code == 200, r.text[:200])
        if r.status_code != 200:
            return 1
        token = r.json()["token"]
        h = {"Authorization": f"Bearer {token}"}

        # 2 — una key inventada NO
        r = cli.post("/v1/auth/token", json={"apiKey": "avk_live_zzzz_inventada"})
        check("una API key inventada da 401", r.status_code == 401)

        # 3 — el cliente ve SUS cuentas
        r = cli.get("/v1/cuentas", headers=h)
        check("/v1/cuentas devuelve el scope",
              r.status_code == 200 and [c["cuenta"] for c in r.json()["cuentas"]] == [cuenta],
              r.text[:200])

        # 4 — cobertura de datos
        r = cli.get("/v1/meta", headers=h)
        m = r.json() if r.status_code == 200 else {}
        check("/v1/meta responde", r.status_code == 200, r.text[:200])
        print(f"      datos: {m.get('primera_operacion')} → {m.get('ultima_operacion')}"
              f"  ({m.get('total_operaciones')} operaciones)"
              f"  última ingesta: {m.get('ultima_ingesta')}")

        # 5 — EL CHEQUE QUE IMPORTA: ninguna fila fuera del scope
        r = cli.get("/v1/operaciones?limit=200", headers=h)
        filas = r.json().get("operaciones", []) if r.status_code == 200 else []
        check("/v1/operaciones responde", r.status_code == 200, r.text[:200])
        check("NINGUNA fila fuera del scope",
              all(f["cuenta"] == cuenta for f in filas),
              f"cuentas distintas: {sorted({f['cuenta'] for f in filas})}")
        if filas:
            print(f"      ejemplo: {filas[0]['fecha']}  {filas[0]['ticker']}  "
                  f"{filas[0]['operacion']}  {filas[0]['bruto']} {filas[0]['moneda']}")
            check("los montos son NÚMEROS, no texto",
                  filas[0]["bruto"] is None or isinstance(filas[0]["bruto"], int | float))
            check("no se filtran campos internos",
                  not ({"segmento", "nivel_3", "es_cierre", "etapa",
                    "anulado", "actualizado_en", "instrumento"} & set(filas[0])))

        # 6 — paginación: la segunda página no repite ni saltea
        r1 = cli.get("/v1/operaciones?limit=2", headers=h)
        p1 = r1.json()
        if p1["paginacion"]["hay_mas"]:
            r2 = cli.get(f"/v1/operaciones?limit=2&cursor={p1['paginacion']['siguiente_cursor']}",
                         headers=h)
            b1 = {f["boleto"] for f in p1["operaciones"]}
            b2 = {f["boleto"] for f in r2.json()["operaciones"]}
            check("el cursor avanza sin repetir", bool(b2) and not (b1 & b2))
        else:
            print("  – paginación: la cuenta tiene ≤2 boletos, no se pudo probar")

        # 7 — cuenta ajena: 403, no lista vacía
        r = cli.get(f"/v1/operaciones?cuenta={_AJENA}", headers=h)
        check("pedir una cuenta ajena da 403",
              r.status_code == 403 and r.json()["detail"]["code"] == "cuenta_no_autorizada",
              r.text[:200])

        # 8 — los anulados no salen (la fila existe en la base, no en la respuesta)
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM operaciones "
                        "WHERE id_cuenta = %s AND anulado_en IS NOT NULL", (cuenta,))
            anulados_en_base = cur.fetchone()[0]
        r = cli.get("/v1/operaciones?limit=1000", headers=h)
        boletos = {f["boleto"] for f in r.json().get("operaciones", [])}
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT boleto FROM operaciones "
                        "WHERE id_cuenta = %s AND anulado_en IS NOT NULL", (cuenta,))
            anulados = {b for (b,) in cur.fetchall()}
        check(f"los anulados NO salen ({anulados_en_base} en la base)",
              not (boletos & anulados),
              f"se filtraron: {sorted(boletos & anulados)}")

        # 9 — FAIL-CLOSED en vivo: sin cuentas, 403 (no "ve todo")
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM ext.cuentas_autorizadas WHERE cliente_id = %s", (_ID,))
        r = cli.get("/v1/operaciones", headers=h)
        check("sin cuentas autorizadas → 403 (NO 've todo')",
              r.status_code == 403 and r.json()["detail"]["code"] == "sin_cuentas",
              r.text[:200])
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO ext.cuentas_autorizadas (cliente_id, id_cuenta, agregada_por) "
                        "VALUES (%s,%s,'ext_smoke')", (_ID, cuenta))

        # 10 — revocar la key mata el token YA emitido
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("UPDATE ext.api_keys SET revocada_at = now() WHERE cliente_id = %s", (_ID,))
        r = cli.get("/v1/cuentas", headers=h)
        check("revocar la key mata el token vigente",
              r.status_code == 401 and r.json()["detail"]["code"] == "credencial_revocada",
              r.text[:200])

        # 11 — quedó rastro en la auditoría
        with get_pool().connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ext.requests_log WHERE cliente_id = %s", (_ID,))
            n = cur.fetchone()[0]
        check("cada request quedó auditado", n > 0, f"filas en requests_log: {n}")

    finally:
        if a.dejar:
            print(f"\n⚠️  cliente temporal '{_ID}' NO borrado (--dejar).")
        else:
            _borrar()
            print(f"\n🧹 cliente temporal '{_ID}' borrado.")

    print(f"\n{'─' * 60}")
    if _fallos:
        print(f"❌ {len(_fallos)} FALLARON: {', '.join(_fallos)}")
        return 1
    print(f"✅ {_ok}/{_ok} OK — la API externa funciona end-to-end contra la base real.")
    print("   Falta comprobar la capa de Cloudflare desde afuera (ver docs/API_EXTERNA.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
