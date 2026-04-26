import os
import sys

# Agregamos el path raíz para acceder a mongo_manager y config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from datetime import datetime, timedelta

import holidays
import pyRofex
from pymongo import ReplaceOne

from core.job_runs import JobRunLogger
from core.mongo import get_mongo_client
from core.rofex_session import inicializar_sesion
from jobs.aunesa_client import AunesaApiManager

# Definición de las cuentas
CUENTAS_OBJETIVO = ["100", "255", "101", "163"]


def obtener_fechas_habiles():
    """
    Calcula el T+2 (próximo próximo día hábil) para el 'Desde'.
    Salta fines de semana y feriados.
    """
    arg_holidays = holidays.Argentina()

    def proximo_habil(fecha_ref):
        proximo = fecha_ref + timedelta(days=1)
        while proximo.weekday() >= 5 or proximo in arg_holidays:
            proximo += timedelta(days=1)
        return proximo

    hoy = datetime.now()
    t_mas_1 = proximo_habil(hoy)
    t_mas_2 = proximo_habil(t_mas_1)

    return t_mas_2.strftime("%d/%m/%Y"), ""


def sincronizar_assets(df):
    """
    Sincroniza las unidades nuevas de Carteras hacia TitulosAPI.AssetsAPI (fuente de verdad).
    No pisa valores existentes — $ifNull solo completa campos vacíos.
    """
    client = get_mongo_client()
    collection = client["TitulosAPI"]["AssetsAPI"]

    unidades = [u for u in df["unidad"].dropna().unique().tolist() if u]
    if not unidades:
        return 0, 0

    insertados = 0
    for u in unidades:
        result = collection.update_one(
            {"unidad": u},
            [{"$set": {
                "unidad":       u,
                "calificacion": {"$ifNull": ["$calificacion", ""]},
                "cartera":      {"$ifNull": ["$cartera",      ""]},
                "clase_activo": {"$ifNull": ["$clase_activo", ""]},
                "emisor":       {"$ifNull": ["$emisor",       ""]},
                "ticker":       {"$ifNull": ["$ticker",       ""]},
                "vencimiento":  {"$ifNull": ["$vencimiento",  None]},
                "instrumento":  {"$ifNull": ["$instrumento",  ""]},
            }}],
            upsert=True,
        )
        if result.upserted_id:
            insertados += 1
    return insertados, 0


def actualizar_precios_mercado():
    """
    Para cada asset cuyo INSTRUMENTO contiene el patrón 'MERV - XMEV',
    consulta el LAST price via REST pyRofex y actualiza SOLO el campo
    'precio' en Carteras via $set. FCI sin INSTRUMENTO no son tocados.
    """
    client = get_mongo_client()
    db_val = client["Valuaciones"]

    assets = list(db_val["Assets"].find(
        {"INSTRUMENTO": {"$regex": "MERV - XMEV", "$options": "i"}},
        {"_id": 0, "unidad": 1, "INSTRUMENTO": 1}
    ))

    print(f"🔍 Assets con INSTRUMENTO MERV-XMEV encontrados: {len(assets)}")
    if not assets:
        return 0

    if not inicializar_sesion():
        print("⚠️ No se pudo inicializar sesión pyRofex. Se omite actualización de precios.")
        return 0

    actualizados = 0
    for asset in assets:
        instrumento = asset.get("INSTRUMENTO", "").strip()
        unidad = asset.get("unidad", "").strip()
        if not instrumento or not unidad:
            continue
        try:
            resp = pyRofex.get_market_data(
                ticker=instrumento,
                entries=[pyRofex.MarketDataEntry.LAST]
            )
            la = resp.get("marketData", {}).get("LA")
            if la and la.get("price"):
                precio = float(la["price"])
                db_val["Carteras"].update_many(
                    {"unidad": unidad},
                    {"$set": {"precio": precio}}
                )
                print(f"   ✅ {instrumento} → {precio}")
                actualizados += 1
            else:
                print(f"   ⚠️ Sin LA en respuesta para {instrumento}: {resp}")
        except Exception as e:
            print(f"   ❌ Error para {instrumento}: {e}")

    return actualizados


UNIDADES_EXACTAS_EXCLUIDAS = {"ARS", "USDL"}
UNIDADES_CONTIENEN_EXCLUIDAS = ["[1] Depósito U$", "OTC", "2024", "2025", "DLR"]


def filtrar_unidades(df):
    """Elimina filas cuya unidad no debería estar en Carteras."""
    def debe_excluir(unidad):
        if not isinstance(unidad, str):
            return False
        if unidad in UNIDADES_EXACTAS_EXCLUIDAS:
            return True
        return any(p in unidad for p in UNIDADES_CONTIENEN_EXCLUIDAS)

    mask = df["unidad"].apply(debe_excluir)
    filtrados = mask.sum()
    if filtrados:
        print(f"🚫 Unidades excluidas: {filtrados} filas → {df[mask]['unidad'].unique().tolist()}")
    return df[~mask].copy()


def guardar_en_mongo(df, cuentas_ok: list[str] | None = None):
    """
    Upsert atómico de Valuaciones.Carteras usando la clave (id_cuenta, unidad).

    `cuentas_ok` limita el delete de posiciones obsoletas a las cuentas que
    efectivamente devolvieron datos frescos en esta corrida. Sin este scope,
    una falla de Aunesa en una sola cuenta borraba todas sus posiciones
    históricas. Si `cuentas_ok` es None se usa la lista de id_cuenta presentes
    en `df` (fallback).
    """
    client = get_mongo_client()
    collection = client["Valuaciones"]["Carteras"]

    registros = df.to_dict(orient="records")
    if not registros:
        return 0

    ahora = datetime.utcnow()
    for r in registros:
        r["timestamp"] = ahora
    ops = [
        ReplaceOne(
            {"id_cuenta": r.get("id_cuenta"), "unidad": r.get("unidad")},
            r,
            upsert=True
        )
        for r in registros
    ]
    collection.bulk_write(ops, ordered=False)

    # Eliminar filas que ya no vienen en el snapshot, SOLO dentro de las
    # cuentas que sí respondieron. Si cuentas_ok es None, derivarlo del df.
    if cuentas_ok is None:
        cuentas_ok = sorted({r.get("id_cuenta") for r in registros if r.get("id_cuenta")})

    if cuentas_ok:
        claves_actuales = [
            {"id_cuenta": r.get("id_cuenta"), "unidad": r.get("unidad")}
            for r in registros
        ]
        filtro_delete = {"id_cuenta": {"$in": cuentas_ok}}
        if claves_actuales:
            filtro_delete["$nor"] = claves_actuales
        collection.delete_many(filtro_delete)

    return len(registros)


def run():
    with JobRunLogger("carteras") as job:
        job.log("🚀 Iniciando Actualización de Carteras...")

        fecha_desde, fecha_hasta = obtener_fechas_habiles()
        job.log(f"📅 Consulta Desde (T+2): {fecha_desde}")
        job.set_stat("fecha_desde", fecha_desde)
        job.set_stat("cuentas_objetivo", CUENTAS_OBJETIVO)

        api_manager = AunesaApiManager()
        df_carteras, per_cuenta = api_manager.consultar_cuentas(
            CUENTAS_OBJETIVO,
            desde=fecha_desde,
            hasta=fecha_hasta,
        )

        job.set_stat("cuentas_resultado", per_cuenta)
        for cta, info in per_cuenta.items():
            marker = "✅" if info["status"] == "ok" else "⚠️"
            detalle = f"{info['count']} posiciones" if info["status"] == "ok" else info["status"]
            if info.get("error"):
                detalle = f"{detalle} — {info['error']}"
            job.log(f"  {marker} Cuenta {cta}: {detalle}")

        cuentas_ok = [c for c, info in per_cuenta.items() if info["status"] == "ok"]
        cuentas_fallidas = [c for c in CUENTAS_OBJETIVO if c not in cuentas_ok]
        job.set_stat("cuentas_ok", cuentas_ok)
        job.set_stat("cuentas_fallidas", cuentas_fallidas)

        if cuentas_fallidas:
            job.error(
                f"Cuentas sin datos frescos: {cuentas_fallidas}. "
                f"Posiciones viejas de estas cuentas se preservan en Mongo."
            )

        if df_carteras is None or df_carteras.empty:
            job.error("No se recuperaron datos de la API.")
            return

        df_carteras = filtrar_unidades(df_carteras)
        job.log(f"📊 Registros consolidados (post-filtro): {len(df_carteras)}")
        job.set_stat("registros_sincronizados", len(df_carteras))

        cantidad = guardar_en_mongo(df_carteras, cuentas_ok=cuentas_ok)
        job.log(f"✅ Valuaciones.Carteras actualizado: {cantidad} registros.")

        insertados, _ = sincronizar_assets(df_carteras)
        job.set_stat("assets_insertados", insertados)
        job.log(f"✅ Valuaciones.Assets: {insertados} nuevos insertados.")

        actualizados = actualizar_precios_mercado()
        job.set_stat("precios_actualizados", actualizados)
        job.log(f"✅ Precios de mercado actualizados: {actualizados} instrumentos.")

        job.log("🏁 Proceso finalizado.")

    sys.exit()


def limpiar_carteras_existentes():
    """Borra de Valuaciones.Carteras los docs con unidades que nunca deberían estar."""
    client = get_mongo_client()
    collection = client["Valuaciones"]["Carteras"]

    filtro = {"$or": [
        {"unidad": {"$in": list(UNIDADES_EXACTAS_EXCLUIDAS)}},
        *[{"unidad": {"$regex": p.replace("[", "\\[").replace("]", "\\]").replace("$", "\\$")}}
          for p in UNIDADES_CONTIENEN_EXCLUIDAS]
    ]}

    resultado = collection.delete_many(filtro)
    print(f"🧹 Carteras limpiadas: {resultado.deleted_count} docs eliminados.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true",
                        help="Elimina docs con unidades excluidas de Valuaciones.Carteras")
    args = parser.parse_args()

    if args.clean:
        limpiar_carteras_existentes()
    else:
        run()
