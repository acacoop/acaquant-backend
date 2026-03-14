import requests
import pandas as pd
import logging
import config
from datetime import datetime

logger = logging.getLogger("TradingBot")


class AunesaApiManager:
    def __init__(self):
        self.auth_url = "https://aca.aunesa.com/Irmo/api/login"
        self.base_url = "https://aca.aunesa.com/Irmo/api/cuentas/{}/posicionValuada"
        self.token = None
        self.headers = {"Content-Type": "application/json"}
        self.autenticar()

    def autenticar(self):
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
        if not self.token:
            self.autenticar()
            if not self.token: return None

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
                if resp.status_code == 200:
                    data = resp.json()
                    if not data: continue

                    items = [item for item in data if item.get('informacion') == 'Acumulado']
                    if not items: continue

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

                elif resp.status_code == 401:
                    self.autenticar()

            except Exception as e:
                print(f"⚠️ Error en cuenta {cuenta_id}: {e}")

        # --- COLUMNA DE ACTUALIZACIÓN (Solo en la primer fila) ---
        if not df_consolidado.empty:
            df_consolidado['actualizado'] = ""
            ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            df_consolidado.at[0, 'actualizado'] = ahora

        return df_consolidado