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
| `cuenta` | `cuenta` | Sin cambio |
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
           cuenta: "362", segmento: "SENEBI", moneda: "ARS" }
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
```

Todos los comandos hacen `drop()` + `insert_many()` — son idempotentes y seguros de re-ejecutar.
