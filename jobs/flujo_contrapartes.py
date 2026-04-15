import sys
import os
import requests
from datetime import date
from pymongo import InsertOne

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config
from core.mongo import get_mongo_client

AUTH_URL  = "https://aca.aunesa.com/Irmo/api/login"
INFOS_URL = "https://aca.aunesa.com/Irmo/api/operaciones/informes"

TIPOS_EXCLUIR = {
    "Concurrencia - Caución colocadora (Apertura)",
    "Concurrencia - Caución colocadora (Cierre)",
    "Futuros Financieros - Compra",
    "Futuros Financieros - Venta",
}

CAMPOS = {"boleto", "concertacion", "tipoOperacion", "cuenta", "denominacion",
          "instrumento", "condiciones", "bruto", "segmento", "contraparte"}


def autenticar():
    resp = requests.post(
        AUTH_URL,
        json={
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD,
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("token")
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def inferir_moneda(condiciones):
    if not condiciones:
        return ""
    c = condiciones.upper()
    if "USD" in c:
        return "USD"
    if "ARS" in c:
        return "ARS"
    return ""


def normalizar_cuenta(cuenta_str):
    try:
        return int(str(cuenta_str).strip())
    except (ValueError, TypeError):
        return None


def main():
    hoy = date.today().strftime("%d/%m/%Y")
    print(f"Fecha: {hoy}\n")

    client = get_mongo_client()
    col_contrapartes = client["CashFlow"]["Contrapartes"]
    col_flujo        = client["CashFlow"]["Flujo"]

    # ── 1. Borrar docs de hoy ─────────────────────────────────────────────────
    del_result = col_flujo.delete_many({"concertacion": hoy})
    print(f"🗑️  {del_result.deleted_count} docs de hoy eliminados\n")

    # ── 2. Auth ───────────────────────────────────────────────────────────────
    print("Autenticando...")
    headers = autenticar()
    print("Auth OK\n")

    # ── 3. Contrapartes con cuenta ────────────────────────────────────────────
    docs = list(col_contrapartes.find(
        {"cuenta": {"$exists": True, "$ne": ""}},
        {"_id": 0, "contraparte": 1, "cuenta": 1}
    ))
    print(f"{len(docs)} contrapartes con cuenta asignada\n")

    # ── 4. Fetch por contraparte ──────────────────────────────────────────────
    registros = {}  # boleto -> doc (dedup)

    for doc in docs:
        cp        = doc["contraparte"]
        cuenta_id = normalizar_cuenta(doc["cuenta"])

        if cuenta_id is None:
            print(f"  SKIP {cp} — cuenta inválida")
            continue

        params = {
            "cuenta":         cuenta_id,
            "fechaConcDesde": hoy,
            "fechaConcHasta": hoy,
        }

        try:
            resp = requests.get(INFOS_URL, params=params, headers=headers, timeout=60)

            if resp.status_code == 204:
                print(f"  [{cuenta_id}] {cp} → sin operaciones")
                continue

            if resp.status_code == 401:
                print("  Re-autenticando...")
                headers = autenticar()
                resp = requests.get(INFOS_URL, params=params, headers=headers, timeout=60)

            resp.raise_for_status()
            data = resp.json()

            if not data:
                print(f"  [{cuenta_id}] {cp} → respuesta vacía")
                continue

            count = 0
            for r in data:
                if r.get("tipoOperacion") in TIPOS_EXCLUIR:
                    continue
                r["contraparte"] = cp
                rec = {k: r.get(k) for k in CAMPOS}
                rec["moneda"] = inferir_moneda(rec.get("condiciones", ""))

                boleto = rec.get("boleto")
                if boleto is not None:
                    if boleto not in registros:
                        registros[boleto] = rec
                else:
                    # Sin boleto: usar id único para no perderlo
                    registros[f"_no_boleto_{len(registros)}"] = rec

                count += 1

            print(f"  [{cuenta_id}] {cp} → {count} operaciones")

        except Exception as e:
            print(f"  [{cuenta_id}] {cp} → ERROR: {e}")

    # ── 5. Insertar ───────────────────────────────────────────────────────────
    if registros:
        ops = [InsertOne(r) for r in registros.values()]
        col_flujo.bulk_write(ops, ordered=False)
        print(f"\n✅ {len(registros)} documentos insertados para {hoy}")
    else:
        print(f"\n⚠️  Sin operaciones para insertar en {hoy}")

    # ── 6. Dedup global por boleto ────────────────────────────────────────────
    print("\nVerificando duplicados en toda la colección...")
    pipeline = [
        {"$match": {"boleto": {"$ne": None}}},
        {"$group": {"_id": "$boleto", "ids": {"$push": "$_id"}, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
    ]
    duplicados = list(col_flujo.aggregate(pipeline))

    if not duplicados:
        print("✅ Sin duplicados")
    else:
        ids_a_borrar = []
        for d in duplicados:
            # Conservar el primero, borrar el resto
            ids_a_borrar.extend(d["ids"][1:])
        result = col_flujo.delete_many({"_id": {"$in": ids_a_borrar}})
        print(f"🧹 {result.deleted_count} duplicados eliminados ({len(duplicados)} boletos afectados)")



if __name__ == "__main__":
    main()
