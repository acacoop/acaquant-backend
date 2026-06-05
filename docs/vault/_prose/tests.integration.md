Marcador de paquete de los tests de integración (`tests/integration/__init__.py`). Estos tests corren contra Atlas real (marca `pytest -m integration`) y se excluyen del run unit por defecto. No contiene lógica propia.

Conecta con: agrupa los tests que pegan a Mongo vivo (ej. `test_comercial_integration`); requieren `MONGO_URI` / Atlas up o skipean.
