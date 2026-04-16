# API Migrations — Mapping de colecciones origen → destino

Este documento registra la transformación de cada colección legacy a su versión API. Mientras las colecciones convivan, este es el mapa de referencia para mantener los datos sincronizados.

**Comando de sincronización general:**
```bash
cd /root/TradingAV && /root/TradingAV/venv/bin/python -m scripts.api_migrate <comando>
```

---

## 1. AccionistasAPI

| | Origen | Destino |
|---|---|---|
| **Database** | `CashFlow` | `CuentasAPI` |
| **Colección** | `Accionistas` | `AccionistasAPI` |
| **Comando** | `python -m scripts.api_migrate accionistas` + `python -m scripts.api_migrate mover` |
| **Borra origen** | No |

### Mapping de campos

| Campo origen | Campo destino | Transformación |
|---|---|---|
| `cuenta` | `cuenta` | Sin cambio (valor original completo) |
| `cuenta` | `id_cuenta` | Extraído: regex `[N]` → `"N"` (string) |
| `cuenta` | `nombre` | Extraído: todo después de `[N] ` |
| `accionista` | `grupo` | Renombrado |

### Ejemplo

```
ORIGEN:  { cuenta: "[139] LA SEGUNDA SEGUROS DE RETIRO SA RVP", accionista: "LA SEGUNDA" }
DESTINO: { cuenta: "[139] LA SEGUNDA SEGUROS DE RETIRO SA RVP", id_cuenta: "139", nombre: "LA SEGUNDA SEGUROS DE RETIRO SA RVP", grupo: "LA SEGUNDA" }
```

---

## 2. ContrapartesAPI

| | Origen | Destino |
|---|---|---|
| **Database** | `CashFlow` | `CuentasAPI` |
| **Colección** | `Contrapartes` | `ContrapartesAPI` |
| **Comando** | `python -m scripts.api_migrate contrapartes` + `python -m scripts.api_migrate mover` |
| **Borra origen** | No |

### Mapping de campos

| Campo origen | Campo destino | Transformación |
|---|---|---|
| `denominacion` | `cuenta` | Renombrado |
| `cuenta` | `id_cuenta` | Renombrado |
| `contraparte` | `nombre` | Renombrado |
| `segmento` | `grupo` | Renombrado |

### Campos descartados

| Campo origen | Motivo |
|---|---|
| `_id` | Interno Mongo |

### Ejemplo

```
ORIGEN:  { denominacion: " FCI ADCAP PESOS PLUS", cuenta: "362", contraparte: "ADCAP", segmento: "Fondos" }
DESTINO: { cuenta: " FCI ADCAP PESOS PLUS", id_cuenta: "362", nombre: "ADCAP", grupo: "Fondos" }
```

---

## 3. MesaAPI (Flujo de Contrapartes)

| | Origen | Destino |
|---|---|---|
| **Database** | `CashFlow` | `OperacionesAPI` |
| **Colección** | `Flujo` | `MesaAPI` |
| **Comando** | `python -m scripts.api_migrate flujo` |
| **Borra origen** | No |

### Mapping de campos

| Campo origen | Campo destino | Transformación |
|---|---|---|
| `instrumento` | `unidad` | Renombrado |
| `bruto` | `bruto` | Sin cambio |
| `contraparte` | `contraparte` | Sin cambio |
| `concertacion` | `concertacion` | Sin cambio |
| `boleto` | `boleto` | Sin cambio |
| `cuenta` | `id_cuenta` | Renombrado |
| `segmento` | `segmento` | Sin cambio |
| `moneda` | `moneda` | Sin cambio |

### Campos descartados

| Campo origen | Motivo |
|---|---|
| `_id` | Interno Mongo |
| `condiciones` | No requerido en la API |
| `tipoOperacion` | No requerido en la API |
| `denominacion` | Redundante con `contraparte` |

### Ejemplo

```
ORIGEN:  { instrumento: "[05493] TX24", bruto: 589800000, contraparte: "ADCAP", condiciones: "ARS 48hs NG",
           concertacion: "2023-11-09", boleto: "BOL 2023038769", tipoOperacion: "SENEBI Contado - Compra",
           cuenta: "362", denominacion: " FCI ADCAP PESOS PLUS", segmento: "SENEBI", moneda: "ARS" }

DESTINO: { unidad: "[05493] TX24", bruto: 589800000, contraparte: "ADCAP",
           concertacion: "2023-11-09", boleto: "BOL 2023038769",
           id_cuenta: "362", segmento: "SENEBI", moneda: "ARS" }
```

---

## 4. FlujosAPI (Movimientos de Dinero)

| | Origen | Destino |
|---|---|---|
| **Database** | `CashFlow` | `OperacionesAPI` |
| **Colección** | `Movimientos` | `FlujosAPI` |
| **Comando** | `python -m scripts.api_migrate movimientos` |
| **Borra origen** | No |

### Mapping de campos

| Campo origen | Campo destino | Transformación |
|---|---|---|
| `comprobante` | `boleto` | Renombrado |
| `cuenta` | `cuenta` | Sin cambio |
| `fecha` | `concertacion` | Renombrado + convertido de `dd/mm/yyyy` → `YYYY-MM-DD` |
| `informacion` | `informacion` | Sin cambio |
| `total` | `bruto` | Renombrado |
| `unidad` | `unidad` | Sin cambio |

### Campos descartados

| Campo origen | Motivo |
|---|---|
| `_id` | Interno Mongo |
| `estado` | No requerido en la API |
| `lugar` | No requerido en la API |
| `uso` | No requerido en la API |

### Ejemplo

```
ORIGEN:  { comprobante: "CD 2025003117", cuenta: "[1005] FIBIGER, BRANCO NAHUEL", estado: "DIS",
           fecha: "02/07/2025", informacion: "Depósito - TR 20250702124409871",
           lugar: "Local", total: 100000, unidad: "ARS", uso: "GRAL" }

DESTINO: { boleto: "CD 2025003117", cuenta: "[1005] FIBIGER, BRANCO NAHUEL",
           concertacion: "2025-07-02", informacion: "Depósito - TR 20250702124409871",
           bruto: 100000, unidad: "ARS" }
```

---

## 5. CarterasAPI (Posiciones por cuenta)

| | Origen | Destino |
|---|---|---|
| **Database** | `Valuaciones` | `PortfolioAPI` |
| **Colección** | `Carteras` | `CarterasAPI` |
| **Comando** | `python -m scripts.api_migrate carteras` |
| **Borra origen** | No |

### Mapping de campos

| Campo origen | Campo destino | Transformación |
|---|---|---|
| `id_cuenta` | `id_cuenta` | Sin cambio |
| `unidad` | `unidad` | Sin cambio |
| `cantidad` | `cantidad` | Sin cambio |
| `precio` | `precio` | Sin cambio |
| `timestamp` | `timestamp` | Truncado a fecha (datetime sin hora: `YYYY-MM-DDT00:00:00`) |

### Campos descartados

| Campo origen | Motivo |
|---|---|
| `_id` | Interno Mongo |
| `actualizado` | No requerido en la API |

### Ejemplo

```
ORIGEN:  { id_cuenta: "101", unidad: "[840] CAFCI577-840 - SBS AHORRO PESOS Clase B",
           cantidad: 0.00006814, precio: 164.067031, actualizado: "",
           timestamp: ISODate("2026-04-16T14:00:21.706Z") }

DESTINO: { id_cuenta: "101", unidad: "[840] CAFCI577-840 - SBS AHORRO PESOS Clase B",
           cantidad: 0.00006814, precio: 164.067031,
           timestamp: ISODate("2026-04-16T00:00:00Z") }
```

---

## 6. AumAPI (Snapshots históricos AuM)

| | Origen | Destino |
|---|---|---|
| **Database** | `Valuaciones` | `PortfolioAPI` |
| **Colección** | `AuM` | `AumAPI` |
| **Comando** | `python -m scripts.api_migrate aum` |
| **Borra origen** | No |

### Mapping de campos

| Campo origen | Campo destino | Transformación |
|---|---|---|
| `fecha_snapshot` | `fecha` | Renombrado + convertido de string `YYYY-MM-DD` a datetime |
| `id_cuenta` | `id_cuenta` | Sin cambio |
| `unidad` | `unidad` | Sin cambio |
| `cantidad` | `cantidad` | Sin cambio |
| `cuenta` | `cuenta` | Sin cambio |
| `precio` | `precio` | Sin cambio |
| `valuacion` | `valuacion` | Sin cambio |

### Campos descartados

| Campo origen | Motivo |
|---|---|
| `_id` | Interno Mongo |
| `timestamp` | Redundante con `fecha_snapshot` |
| `tipoTitulo` | No requerido en la API |

### Ejemplo

```
ORIGEN:  { fecha_snapshot: "2026-03-28", id_cuenta: "1010", unidad: "[839] TXAR",
           cantidad: 608, cuenta: "[1010] FORCINITI, DARIO GUILLERMO", precio: 661.5,
           timestamp: ISODate("2026-03-28T23:00:11.666Z"), tipoTitulo: "Acciones",
           valuacion: 402192 }

DESTINO: { fecha: ISODate("2026-03-28T00:00:00Z"), id_cuenta: "1010", unidad: "[839] TXAR",
           cantidad: 608, cuenta: "[1010] FORCINITI, DARIO GUILLERMO", precio: 661.5,
           valuacion: 402192 }
```

---

## Resincronización rápida

Si se actualizan datos en las colecciones origen y necesitás reflejarlos en las API:

```bash
cd /root/TradingAV

# Accionistas (regenera en CashFlow, luego mueve a CuentasAPI)
/root/TradingAV/venv/bin/python -m scripts.api_migrate accionistas
/root/TradingAV/venv/bin/python -m scripts.api_migrate mover

# Contrapartes (idem)
/root/TradingAV/venv/bin/python -m scripts.api_migrate contrapartes
/root/TradingAV/venv/bin/python -m scripts.api_migrate mover

# Flujo (directo a OperacionesAPI)
/root/TradingAV/venv/bin/python -m scripts.api_migrate flujo

# Movimientos (directo a OperacionesAPI)
/root/TradingAV/venv/bin/python -m scripts.api_migrate movimientos

# Carteras (directo a PortfolioAPI)
/root/TradingAV/venv/bin/python -m scripts.api_migrate carteras

# AuM (directo a PortfolioAPI)
/root/TradingAV/venv/bin/python -m scripts.api_migrate aum
```

Todos los comandos hacen `drop()` + `insert_many()` — son idempotentes y seguros de re-ejecutar.
