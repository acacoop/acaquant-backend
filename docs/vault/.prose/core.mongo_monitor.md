Espía de queries Mongo. Engancha un `CommandListener` oficial de pymongo que captura cada comando (find/aggregate/count/update) con su duración y los guarda en un ring-buffer en memoria de 2000 registros. Arranca apagado: un toggle del Manager lo prende cuando se quiere medir latencia, y `get_records()` devuelve la copia. Resume colección + filtro de cada comando sin volcar el payload entero.

Conecta con: se registra desde `core.mongo` (antes de crear el MongoClient). Lo prende/lee el panel Manager para diagnosticar queries lentas de la API.
