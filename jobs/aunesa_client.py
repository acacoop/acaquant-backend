import logging
from datetime import datetime

import pandas as pd
import requests

import config

logger = logging.getLogger("TradingBot")


class AunesaApiManager:
    def __init__(self):
        self.auth_url = "https://aca.aunesa.com/Irmo/api/login"
        self.base_url = "https://aca.aunesa.com/Irmo/api/cuentas/{}/posicionValuada"
        self.token = None
        self.headers = {"Content-Type": "application/json"}
        self.autenticar()

    def autenticar(self):
        self.token = None  # limpiar token viejo antes de intentar re-auth
        payload = {
            "clientId": config.AUNESA_CLIENT_ID,
            "username": config.AUNESA_USERNAME,
            "password": config.AUNESA_PASSWORD
        }
        try:
            resp = requests.post(self.auth_url, json=payload, headers=self.headers, timeout=10)
            if resp.status_code == 200:
                self.token = resp.json().get("token")
                self.headers["Authorization"] = f"Bearer {self.token}"
                logger.info("🔑 Autenticación Aunesa exitosa.")
            else:
                logger.error(f"Error Auth Aunesa: {resp.status_code}")
        except Exception as e:
            logger.error(f"Falla crítica en autenticación Aunesa: {e}")

    def consultar_cuentas(self, lista_cuentas, desde, hasta):
        """Consulta posiciones de múltiples cuentas en Aunesa.

        Returns:
            tuple (df_consolidado, per_cuenta) donde:
              - df_consolidado: DataFrame con columnas id_cuenta/unidad/cantidad/precio
              - per_cuenta: dict {cuenta_id: {"count": int, "status": "ok|empty|error|auth_failed",
                                              "error": str | None}}
            per_cuenta permite al caller saber exactamente qué cuentas tuvieron datos
            frescos para no borrar posiciones viejas de cuentas que no respondieron.
        """
        per_cuenta: dict[str, dict] = {
            c: {"count": 0, "status": "pending", "error": None} for c in lista_cuentas
        }

        if not self.token:
            self.autenticar()
            if not self.token:
                for c in lista_cuentas:
                    per_cuenta[c] = {"count": 0, "status": "auth_failed", "error": "no token"}
                return pd.DataFrame(), per_cuenta

        df_consolidado = pd.DataFrame()
        params = {
            "desde": desde,
            "hasta": hasta,
            "tipoCuenta": "Comitentes y propias",
            "nivel": "Especie x cuenta",
            "ocultarCerradas": "true"
        }

        for cuenta_id in lista_cuentas:
            url = self.base_url.format(cuenta_id)
            try:
                resp = requests.get(url, params=params, headers=self.headers, timeout=15)

                # Si expiró el token, re-auth y reintentar UNA vez esta cuenta.
                if resp.status_code == 401:
                    self.autenticar()
                    if self.token:
                        resp = requests.get(url, params=params, headers=self.headers, timeout=15)
                    else:
                        per_cuenta[cuenta_id] = {"count": 0, "status": "auth_failed",
                                                 "error": "401 sin re-auth"}
                        continue

                if resp.status_code != 200:
                    per_cuenta[cuenta_id] = {"count": 0, "status": "error",
                                             "error": f"HTTP {resp.status_code}"}
                    continue

                data = resp.json()
                if not data:
                    per_cuenta[cuenta_id] = {"count": 0, "status": "empty", "error": None}
                    continue

                items = [item for item in data if item.get('informacion') == 'Acumulado']
                if not items:
                    per_cuenta[cuenta_id] = {"count": 0, "status": "empty",
                                             "error": "sin filas Acumulado"}
                    continue

                df = pd.DataFrame(items)

                # 1. Limpieza y Conversión Técnica
                df['id_cuenta'] = df['cuenta'].str.extract(r'\[(\d+)\]')
                df['cantidad'] = pd.to_numeric(df['cantidad'], errors='coerce') * -1
                df['precio'] = pd.to_numeric(df['precio'], errors='coerce')

                # 2. Agrupación (Unidad Única por cuenta)
                df_grouped = df.groupby(['id_cuenta', 'unidad', 'tipoTitulo'], as_index=False, dropna=False).agg({
                    'cantidad': 'sum',
                    'precio': 'max'
                })

                # 3. Filtro de Neteos (Vuela si la suma es 0)
                df_final = df_grouped[df_grouped['cantidad'] != 0].copy()

                # 4. Lógica de Precio: Solo 'Fondos de Inversión'
                df_final['precio'] = df_final.apply(
                    lambda x: x['precio'] if x['tipoTitulo'] == 'Fondos de Inversión' else "",
                    axis=1
                )

                # 5. Selección de Columnas Finales
                cols_excel = ['id_cuenta', 'unidad', 'cantidad', 'precio']
                df_consolidado = pd.concat([df_consolidado, df_final[cols_excel]], ignore_index=True)

                per_cuenta[cuenta_id] = {"count": len(df_final), "status": "ok", "error": None}

            except Exception as e:
                per_cuenta[cuenta_id] = {"count": 0, "status": "error", "error": str(e)}

        # --- COLUMNA DE ACTUALIZACIÓN (Solo en la primer fila) ---
        if not df_consolidado.empty:
            df_consolidado['actualizado'] = ""
            ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            df_consolidado.at[0, 'actualizado'] = ahora

        return df_consolidado, per_cuenta