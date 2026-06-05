Cliente único de la API del custodio Aunesa. Centraliza el login (token Bearer cacheado, con re-auth automático ante 401) y un GET genérico con retry de timeout, reemplazando las copias de `_autenticar` que estaban dispersas por jobs/services/scripts. Endpoints conocidos: listado de cuentas, posición valuada, consolidados generales e informes de operaciones.

Conecta con: lee credenciales de `config` (AUNESA_*); pega a `aca.aunesa.com/Irmo/api`; lo usan los jobs de negocio/aranceles/operaciones (`negocio_movimientos`, `aranceles`, `sync_comitentes`) y services que consultan al custodio.
