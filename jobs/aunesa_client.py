import logging

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

