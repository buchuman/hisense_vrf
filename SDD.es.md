> 🌐 [English](SDD.md) · **Español**

# Hisense VRF — Software Design Document

**Versión:** 1.0.0  
**Última actualización:** 2026-05-24  
**Quality scale:** Platinum

---

## 1. Visión general

`hisense_vrf` es una **custom integration** para Home Assistant que controla unidades VRF Hisense a través de un **gateway i-Modkit Modbus TCP** (modelo `HCPC-H2M1C`). Reemplaza a la integración anterior `acmodbus` (v1), reescrita desde cero para corregir bugs de write-and-verify, adaptarse a las capabilities reales de cada unidad y alcanzar el nivel Platinum del [HA Integration Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale).

### Goals

- **Correctness:** cada cambio del usuario se confirma con un read del hardware antes de declararlo aplicado.
- **Observability:** todas las operaciones se loguean con atribución de usuario; un sensor `last_write_status` por unit expone el último resultado.
- **Adaptabilidad por unit:** los `hvac_modes`, `fan_modes` y `supported_features` de cada climate se derivan dinámicamente de los 20 registros de function-selection (40048-40067).
- **Resiliencia operativa:** tolerancia a fallos transitorios del gateway (timeouts, TID jitter, disconnects), recovery automático tras restart.
- **Mantenibilidad:** 193 tests con 96% coverage, mypy strict 0 errors, 100% de strings traducibles vía `translation_key`.

### Non-goals

- No soporta múltiples gateways en una sola config entry (un gateway = un config entry).
- No persiste datos de la integración fuera del entity registry / recorder estándar de HA.
- No publica `pyacmodbus` a PyPI (se bundlea en deploys HA OS).

---

## 2. Arquitectura

### 2.1 Stack

```
┌─────────────────────────────────────────────────────────────────┐
│                  Home Assistant frontend (Lovelace)              │
└─────────────────────────────────────────────────────────────────┘
                                ▲
                                │ entity state, services
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  hisense_vrf integration (custom_components/hisense_vrf/)        │
│                                                                  │
│  ┌──────────────┐  ┌────────────┐  ┌─────────────┐  ┌────────┐ │
│  │ climate.py   │  │ sensor.py  │  │ switch.py   │  │ ...    │ │
│  │ binary_*.py  │  │ select.py  │  │ button.py   │  │        │ │
│  └──────┬───────┘  └─────┬──────┘  └──────┬──────┘  └────────┘ │
│         └─────────┬──────┴────────────────┘                     │
│                   ▼                                              │
│           ┌─────────────────────┐                                │
│           │ HisenseVRFController │   (controller.py)             │
│           └──────────┬──────────┘                                │
│                      │                                            │
│                      ▼                                            │
│             ┌──────────────┐                                     │
│             │ ACModbusClient│   (bundled pyacmodbus/)            │
│             └──────────────┘                                     │
└──────────────────────│──────────────────────────────────────────┘
                       │ Modbus TCP (FC 0x03 / 0x10)
                       ▼
              ┌─────────────────┐
              │ i-Modkit gateway │ @ <gateway-ip>:502
              └────────┬────────┘
                       │ Hisense H-LINK proprietary bus
                       ▼
       ┌─────────────────────────────────┐
       │ N indoor units + M outdoor      │
       └─────────────────────────────────┘
```

### 2.2 Layout del repositorio

```
homeassistant-v2/
├── custom_components/hisense_vrf/
│   ├── __init__.py              ← setup/unload, sys.path para pyacmodbus bundle
│   ├── config_flow.py           ← ConfigFlow + OptionsFlow + reconfigure step
│   ├── controller.py            ← lógica central (~750 líneas)
│   ├── entity.py                ← base classes Indoor/Gateway/Outdoor entity
│   ├── climate.py               ← single climate entity per indoor unit
│   ├── sensor.py                ← ~50 sensors per indoor + outdoor + gateway
│   ├── binary_sensor.py         ← running / alarm / filter + EXP bits
│   ├── switch.py                ← power + 5 prohibit locks + gateway alarm
│   ├── select.py                ← louver + dry mode
│   ├── button.py                ← refresh/reset/lock/discover/eeprom buttons
│   ├── experimental.py          ← ExpBitBinarySensor / ExpEnumSensor (EXP)
│   ├── exp_descriptors.py       ← (auto-generado) 100 BIT + 3 ENUM EXP descriptors
│   ├── diagnostics.py           ← async_get_config_entry_diagnostics
│   ├── const.py                 ← DOMAIN, CONF_*, signals, thresholds
│   ├── manifest.json            ← version 1.0.0, quality_scale platinum
│   ├── quality_scale.yaml       ← per-rule status (Bronze + Silver + Gold + Platinum)
│   ├── strings.json             ← fuente de verdad para traducciones
│   ├── icons.json               ← mdi:* mappings per translation_key
│   ├── translations/en.json     ← sync de strings.json
│   └── brand/                   ← icon.png + logo.png para servir local
├── pyacmodbus-stub/
│   └── pyacmodbus/__init__.py   ← cliente Modbus + data model (~750 líneas)
├── tests/                       ← 193 tests, 96% coverage
├── mypy.ini                     ← strict (con 3 supresiones por HA framework)
├── pyproject.toml
└── README.md
```

### 2.3 Plataformas HA expuestas

| Platform | Entities por unit | Notas |
|----------|-------------------|-------|
| `climate` | 1 (`ac_unit`) | la entidad principal de control |
| `sensor` | ~50 normales + ~3 EXP enum + 2 diagnostic | inlet/outlet temp, setpoint, op_state, alarm, fan_actual, expansion_valve, etc. |
| `binary_sensor` | 7 normales + 100 EXP bit | running, alarm, filter, swing_active, oil_return, test_run, remote_control_active |
| `switch` | 6 | power + 5 prohibit (on_off, mode, fan, swing, temp) |
| `select` | 2 | louver (auto + 0..7), dry_mode (dry1/dry2/dry3) |
| `button` | 4 | refresh_unit, reset_filter, lock_all, unlock_all |
| Gateway-level | 5 entities | unit_count, eeprom, alarm_display switch, refresh_all + eeprom_clear + discover buttons |
| Outdoor-level | ~25 sensors | temperatures, pressures, current, frequency, runtime, valve openings |

**El número de entities escala por unit**: aproximadamente **~170 por indoor** (de las cuales ~103 son EXP, `disabled_by_default=True`) + **~25 por outdoor module** + 5 a nivel gateway. Como referencia, un deployment 7-indoor / 1-outdoor registra ~1100 entities en total, ~700 de ellas EXP ocultas por default.

---

## 3. Módulos principales

### 3.1 `controller.py` — `HisenseVRFController`

Clase central que mantiene el estado y mediates entre las entidades y el cliente Modbus. **Reemplaza al `DataUpdateCoordinator` estándar** porque la lógica de write-and-verify + off-pending + dynamic-devices no encaja con el patrón polling-puro del coordinator.

**Estado mantenido:**

- `unit_indices: list[int]` — units descubiertas (offset 0, 1, 2, 10, ...).
- `unit_identifiers: dict[int, (host_sys, host_addr)]` — usado para naming `ac_indoor_unit_NNNN`.
- `unit_capacities: dict[int, int]` — para el campo `model` del device_info.
- `outdoor_units: list[(sys, mod)]` — modules outdoor detectados.
- `indoor_states: dict[int, ACDeviceState | None]` — último read OK por unit (None mientras unavailable).
- `gateway_state: GatewayState | None`.
- `outdoor_states: dict[(sys, mod), OutdoorUnitState | None]`.
- `pending: dict[int, dict[str, Any]]` — write verify overlay activo.
- `_off_pending: dict[int, dict[str, Any]]` — acumulado mientras unit OFF.
- `_off_timers: dict[int, TimerHandle]` — TTL timers.
- `last_write_status: dict[int, dict[str, Any]]` — para el sensor diagnostic.
- `_unit_locks: dict[int, asyncio.Lock]` — serializa writes por unit (el global lock está dentro de `ACModbusClient`).
- Counters: `_unit_read_failures`, `_gateway_read_failures`, `_unit_write_failures`, `_last_known_unit_count`.

**API pública:**

| Método | Propósito |
|--------|-----------|
| `async_initial_scan()` | connect + scan + identifiers + capacity + outdoor + seed gateway baseline |
| `async_start_polling()` / `async_stop_polling()` | controla el background task |
| `async_refresh_all()` / `async_refresh_unit(idx)` | on-demand reads (botones) |
| `async_write_and_verify(idx, pending, verify_fn, write_fn)` | core write con verify+retry |
| `async_send_on_with_pending(idx, mode_override)` | bundled write para evento ON con off-pending |
| `accumulate_off_pending(idx, attrs, user)` | sumar a la cola off-pending + (re)iniciar TTL |
| `get_display_state(idx)` | estado con pending + off_pending overlays aplicados (para UI) |
| `is_field_pending(idx, field)` | para `assumed_state` en entities |
| `unit_name(idx)` / `unit_model(idx)` / `unit_register_base(idx)` | helpers de naming |
| `async_shutdown()` | cancel timers + disconnect (llamado en unload) |

**Dispatcher signals emitidos:**

- `SIGNAL_UPDATE` (`hisense_vrf_update_{entry_id}`): cualquier cambio de estado → todas las entities se redibujan.
- `signal_new_indoor(entry_id)`: una unit nueva fue detectada (gw_unit_count changed) → platforms crean entities en caliente.
- `signal_new_outdoor(entry_id)`: módulo outdoor nuevo.

### 3.2 `pyacmodbus/__init__.py` — Cliente Modbus + data model

Stub library bundled con el integration (no está en PyPI). Encapsula:

- **`ACModbusClient`**: wrapper sobre `AsyncModbusTcpClient` con `asyncio.Lock` global (el gateway acepta una conexión TCP a la vez). Métodos:
  - `connect()`, `disconnect()`, `_require_client()`.
  - `scan_devices()` → list[int] de unit indices con `unit_code != 0`.
  - `read_device(idx)` → `ACDeviceState`.
  - `read_unit_identifiers(idx)`, `read_unit_capacity(idx)`.
  - `read_gateway()`, `read_outdoor_connections()`, `read_outdoor_unit(sys, mod)`.
  - `turn_on(idx)`, `turn_off(idx)`, `set_setpoint(idx, t)`, `set_mode(idx, m)`, `set_fan_speed(idx, f)`, `set_swing(idx, auto, pos)`, `set_dry_mode(idx, v)`, `set_prohibition(idx, reg, on)`, `lock_all(idx)`, `unlock_all(idx)`, `reset_filter(idx)`.
  - `write_control_block(idx, run, mode, fan, swing, temp)` — frame Modbus 0x10 de 5 regs para el ON event bundled.
  - `set_alarm_display(on)`, `clear_eeprom()` (gateway-level).
- **Data model** (`@dataclass`): `ACDeviceState`, `GatewayState`, `OutdoorUnitState`.
- **Constantes**: register offsets, mode bitmasks, fan bitmasks, alarm codes, base address (`BASE_ADDR=40000`, `UNIT_STRIDE=91`).
- **Excepciones**: `CannotConnect`, `ModbusReadError`.

**Timeout configurable** `_MODBUS_TIMEOUT_S = 1.5` (vs default pymodbus 3 s) — reduce el peor caso del initial_scan de ~54 s a ~27 s.

### 3.3 `experimental.py` + `exp_descriptors.py` — EXP entities

103 entities **diagnostic** por unit que exponen bits y enums de los registros de function-selection (40048-40067). El descriptor se auto-genera de una tabla, una clase Python genérica (`ExpBitBinarySensor` / `ExpEnumSensor`) las instancia con `translation_key` y `disabled_default=True`.

**Decisión:** las EXP son útiles para diagnóstico pero rara vez cambian. Como agregan ~700 entities y bloatean la DB del recorder, se mantienen *disabled* por default. El user las habilita una a una desde Settings → Devices cuando las necesita.

### 3.4 `config_flow.py`

- **User step:** host + port + parámetros (`verify_delay_s`, `verify_retries`, `off_pending_ttl_s`, `polling_enabled`, `poll_interval_s`, `poll_spacing_s`, `poll_gateway_every_n_cycles`).
- **Reconfigure step:** permite cambiar host/port sin recrear el entry (Gold rule).
- **Options flow:** ajuste runtime de los parámetros (sin host/port, eso requiere reconfigure).
- **Unique ID:** `{host}:{port}` (Bronze rule).

---

## 4. Flows clave

### 4.1 Setup inicial

```
async_setup_entry()
  ├─ ACModbusClient(host, port, timeout=1.5)
  ├─ controller = HisenseVRFController(...)
  ├─ controller.async_initial_scan()
  │   ├─ client.connect()
  │   ├─ scan_devices() → [0, 1, 2, 3, 4, 5, 6]
  │   ├─ for idx: read_unit_identifiers(idx) → (host_sys, host_addr)
  │   ├─ for idx: read_unit_capacity(idx) → kBTU
  │   ├─ read_outdoor_connections() → [(0,0)]
  │   ├─ read_gateway() → seed _last_known_unit_count
  │   └─ _prune_stale_devices()
  ├─ entry.runtime_data = controller
  ├─ async_forward_entry_setups(PLATFORMS) → 6 platforms crean entities
  └─ controller.async_start_polling() (si polling_enabled)
```

Failure modes:
- `CannotConnect` → `ConfigEntryNotReady("Cannot connect to host:port")` → HA reintenta con backoff.
- `ModbusReadError` en cualquier paso → idem.

### 4.2 Polling loop

```python
while True:
    cycle += 1
    for idx in unit_indices:
        if pending[idx]: skip   # verify en progreso, no pisar
        await _read_and_track_unit(idx)
        if poll_spacing_s > 0: await sleep(poll_spacing_s)
    if cycle % poll_gateway_every_n_cycles == 0:
        await _read_and_track_gateway()
        for ou in outdoor_units:
            await read_outdoor_unit(ou)
    _notify()
    await sleep(poll_interval_s)
```

`_read_and_track_unit` y `_read_and_track_gateway` incrementan counters de fallos consecutivos; al cruzar `UNAVAILABLE_THRESHOLD=3` la entity queda `unavailable` y se logea WARNING `UNAVAILABLE`. Al primer read OK posterior, se logea `AVAILABLE recovered`.

`_read_and_track_gateway` también gatilla `_rescan_for_dynamic_devices` si `gw_unit_count` cambió respecto a `_last_known_unit_count`.

### 4.3 Write-and-verify (unit ON)

```
async_write_and_verify(idx, pending_attrs, verify_fn, write_fn)
  ├─ acquire unit_lock
  ├─ pending[idx].update(pending_attrs)          # UI overlay activo
  ├─ last_write_status[idx] = PENDING + notify()
  ├─ try:
  │     await write_fn()                          # write Modbus
  │   except ModbusReadError:
  │     _clear_pending; last_write_status=FAILED
  │     _on_write_failed(idx)                     # counter para repair issue
  │     return False
  ├─ for attempt in 1..verify_retries+1:
  │     await sleep(verify_delay_s)
  │     state = await read_device(idx)
  │     if verify_fn(state):
  │         _clear_pending; last_write_status=CONFIRMED
  │         _on_write_confirmed(idx)              # reset counter, delete repair
  │         return True
  └─ _clear_pending; last_write_status=FAILED
     _on_write_failed(idx)                         # 3 fallos consecutivos → repair issue
     return False
```

### 4.4 Off-pending + bundled ON

Cuando el usuario cambia algo y la unit está OFF, `accumulate_off_pending(idx, attrs)` lo guarda en `_off_pending[idx]` y arranca un `loop.call_later(off_pending_ttl_s, expire)`.

Al pedir ON (climate.set_hvac_mode != OFF, o switch.power.on, o turn_on):

```
async_send_on_with_pending(idx, mode_override?)
  ├─ pick mode, fan, setpoint, swing del off_pending o del state
  ├─ if cualquiera missing → persistent_notification + return False
  ├─ swing_reg = encode(auto, position)
  ├─ flush off_pending (timer cancelado)
  └─ async_write_and_verify(idx, ..., write_fn=client.write_control_block(idx, 1, mode, fan, swing_reg, temp))
```

El `write_control_block` envía un único frame Modbus 0x10 con los 5 control registers (40078-40082). Esto es lo que el manual del i-Modkit recomienda para encender una unit con configuración: una operación atómica en vez de 5 writes individuales.

### 4.5 Dynamic devices

Cada gateway poll, `_read_and_track_gateway` compara `new_state.unit_count` con `_last_known_unit_count`. Si difiere → `_rescan_for_dynamic_devices()`:

```
_rescan_for_dynamic_devices()
  ├─ current_indoor = await client.scan_devices()
  ├─ current_outdoor = await client.read_outdoor_connections()
  ├─ for new_idx in current_indoor - known_indoor:
  │     read_unit_identifiers + read_unit_capacity
  │     unit_indices.append(new_idx)
  │     dispatcher.async_dispatcher_send(signal_new_indoor(entry_id), new_idx)
  └─ idem para outdoor
```

Cada platform en su `async_setup_entry` se suscribió al signal y crea las entities en caliente con `async_add_entities(...)`. Removals NO se manejan acá — quedan unavailable y `_prune_stale_devices` las limpia en el próximo reload.

### 4.6 Repair issues

`_on_write_failed(idx)` incrementa `_unit_write_failures[idx]`. Al llegar a `WRITE_FAILED_ISSUE_THRESHOLD=3` consecutivos:

```python
ir.async_create_issue(
    hass, DOMAIN, f"write_failed_{entry_id}_{idx}",
    is_fixable=False, severity=ir.IssueSeverity.WARNING,
    translation_key="write_failed_repeated",
    translation_placeholders={"unit_name": ..., "threshold": "3"},
)
```

El próximo `_on_write_confirmed(idx)` borra el issue con `ir.async_delete_issue`. La descripción traducida lista las 5 causas típicas (locks, alarma, cooldown, capacidad outdoor, wire controller).

---

## 5. Decisiones de diseño y trade-offs

### 5.1 Por qué no usar `DataUpdateCoordinator` estándar

El coordinator estándar de HA asume un patrón poll-only con `_async_update_data() → dict`. Nuestro flujo requiere:
- Polls + on-demand reads + writes + verify cycles **en la misma lock**.
- Estado intermedio (`pending`, `_off_pending`) que las entities usan vía `assumed_state`.
- Optimistic UI overlay (`get_display_state`) que combina hardware + pending.
- Dispatcher signals scoped por entry_id para new-device hot-add.

Implementar todo eso sobre el coordinator añadía complejidad. La clase `HisenseVRFController` es ~750 líneas y encapsula todo de forma autoexplicativa.

### 5.2 Write-and-verify vs optimistic

Alternativa popular: marcar el comando como exitoso apenas se envía y dejar que el polling normal corrija si hay drift. La descartamos porque:

- Las units VRF a veces **silenciosamente rechazan comandos** (locks, alarma, capacidad outdoor) sin que el frame Modbus falle. Sin verify, el user no se entera de que su cambio nunca se aplicó.
- El `last_write_status` es valioso para troubleshooting — sería opaco si no hubiera ciclo de verify.

Trade-off: cada acción del user tarda `verify_delay_s × (verify_retries + 1)` antes de retornar (default = 2s × 4 = 8s peor caso). Aceptable.

### 5.3 Off-pending TTL en vez de write inmediato

Hisense **no acepta** cambios de mode/fan/setpoint cuando la unit está OFF. Si el user mueve el thermostato a 24 °C con la AC apagada, el setpoint queda en hardware en el último valor conocido. Al presionar ON, la unit arranca con setpoint stale.

Solución del off-pending: acumular los cambios en memoria y enviarlos en un **bundled write atómico** al momento del ON. El user ajusta todo lo que quiere mientras la unit está OFF, presiona ON una vez, y la AC arranca configurada como pidió.

TTL=30s evita que cambios olvidados se apliquen después de horas.

### 5.4 EXP entities disabled-by-default

Las 100 BIT + 3 ENUM EXP entities por unit son diagnostic — exponen bits del registro de function-selection. Útiles para entender "por qué AUTO no funciona en esta unit" o "qué hace este bit indocumentado". Pero el user promedio NO las inspecciona.

Hasta v0.x estaban enabled. Resultado en un deployment multi-indoor representativo: cientos de entities adicionales × cambios silenciosos causaron crecimiento notable del recorder DB y un cold startup cercano a dos minutos. En v1.0.0 pasaron a `disabled_default=True`; el user opt-in solo a las EXP que necesita. El mismo deployment vio el cold startup bajar aproximadamente un orden de magnitud.

### 5.5 Naming `ac_indoor_unit_<host_sys><host_addr>`

El user pidió esta convención en lugar del más natural `ac_indoor_unit_<index>`. La razón es operativa: `host_sys` + `host_addr` son los IDs físicos de la unit en el bus H-LINK (visibles en el wire controller). El usuario navegando los entities ve nombres que corresponden a los devices reales, no al orden de discovery (que puede cambiar).

Trade-off: si la unit cambia de address físico, el entity_id no migra solo — requiere remove + re-add del config entry.

### 5.6 Timeout pymodbus 1.5 s (vs 3 s default)

El gateway típicamente responde en <500 ms. El default de 3 s era demasiado conservador para nuestro caso, multiplicando el peor caso del setup. 1.5 s deja ~3× safety margin. Si aparecen errores espurios `ModbusReadError` en operación normal, conviene volver a subir.

### 5.7 `pyacmodbus` como dependencia PyPI

Desde v1.1.0, `pyacmodbus` está [publicado en PyPI](https://pypi.org/project/pyacmodbus/) y declarado como requirement estándar en `manifest.json`:

```json
"requirements": ["pyacmodbus>=1.0.0"]
```

Home Assistant instala la librería automáticamente al cargar el config entry por primera vez. El `__init__.py` de la integración la importa con un `from pyacmodbus import ...` normal — sin bundle, sin trucos de `sys.path`.

El código de la librería standalone vive en `pyacmodbus-stub/` en el repo (usado durante desarrollo local y para empaquetar). El workflow CI `.github/workflows/publish-pyacmodbus.yml` publica versiones nuevas a PyPI vía [OIDC trusted publishing](https://docs.pypi.org/trusted-publishers/) cada vez que se publica una release de GitHub.

Nota histórica: las versiones ≤ v1.0.0 bundleaban la librería adentro del directorio de la integración y la cargaban con `importlib.util.spec_from_file_location`. Ese workaround evitaba pip-install desde un proyecto PyPI inexistente; se eliminó en v1.1.0.

### 5.8 Strict typing con 3 excepciones

`mypy.ini` activa strict completo excepto:
- `disallow_subclassing_any = False`
- `disallow_untyped_decorators = False`  
- `disallow_any_generics = False`

Las clases base de HA (`ClimateEntity`, `SensorEntity`, etc.) y decoradores como `@callback` aparecen como `Any` para mypy porque no resuelve completo los tipos del framework. HA core mismo desactiva estos flags para integraciones — replicamos el mismo enfoque.

---

## 6. Mapa de registros (resumen)

### 6.1 Indoor units

`base = 40000 + unit_index × 91`

| Offset | Reg | Tipo | Notas |
|--------|-----|------|-------|
| 0 | UNIT_CODE | uint16 | 0 = slot vacío |
| 1 | CAPACITY | uint16 | × 8 = BTU/1000 |
| 2 | STATUS | bitmask | bit0=running, bit1-2=op_state, bit3=oil_return |
| 3 | CURR_MODE | bitmask | MODE_AUTO/COOL/DRY/FAN/HEAT |
| 4 | FAN_SW | bitmask | FAN_AUTO/HIGH/MED/LOW |
| 5 | MODE_JUMP | bitmask | modo en ejecución (puede diferir en transición) |
| 6 | FAN_JUMP | bitmask | fan en ejecución |
| 7 | MISC | bitmask | swing_active, full_heat_exchange, auto_swing, louver_pos |
| 8 | SETPOINT_R | uint8 | °C, 0xFF = no configurado |
| 9-12 | COOL/HEAT MAX/MIN | uint16 | límites de temperature por modo |
| 17 | TEMP_TRMT | int16 | temp del control remoto (signed) |
| 19 | OUTLET_TEMP | int16 | °C |
| 20 | INLET_TEMP | int16 | °C |
| 22 | ALARM | uint16 | código alarma (decimal del hex documentado) |
| 28 | FAN_ACTUAL | bitmask | velocidad real (HH2, HIGH, MED, MEDLOW, LOW, MUTE, BREEZE) |
| 48-67 | FUNCTION_SELECTION | uint8 × 20 | bits decodificados como EXP entities |
| 78 | RUN_STOP | R/W | 0=stop, 1=run |
| 79-82 | SET_MODE/FAN/SWING/TEMP | R/W | objetivos |
| 84-89 | PROHIBIT_* | R/W | locks (1=bloquear, 0/2=limpiar) |

### 6.2 Gateway

| Reg | Constante | R/W |
|-----|-----------|-----|
| 4996 | ALARM_DISPLAY | R/W |
| 4997 | UNIT_COUNT | R |
| 4998 | CTRL_MODE | R (1 = Modbus active) |
| 4999 | EEPROM_CLEAR | R/W |

### 6.3 Outdoor

- **Conexiones:** regs 1000-1005 (uno por system 0..5; cada bit = un module).
- **Datos:** `base = 5000 + system × 490 + module × 98`. Offsets 0-14 ASCII model name, 15 capacity code, 16 ID, 17-53 parámetros operacionales (zero cuando compresor inactivo).

---

## 7. Testing

### 7.1 Estructura

```
tests/
├── conftest.py                ← fixtures shared: mock_client, config_entry, setup_integration
├── __init__.py                ← helpers: make_indoor_state, make_gateway_state, make_outdoor_state, indoor_uid, gateway_uid, outdoor_uid
├── test_pyacmodbus.py         ← 24 tests del cliente Modbus
├── test_controller.py         ← 51 tests: poll, write-verify, off-pending, debounce, dynamic-devices, repair, stale-devices
├── test_climate.py            ← acciones HVAC
├── test_sensor.py             ← indoor + outdoor + gateway sensors + EXP enum
├── test_binary_sensor.py      ← running, alarm, filter, gateway, EXP bit
├── test_switch.py             ← power + prohibits + gateway alarm
├── test_select.py             ← louver + dry mode
├── test_button.py             ← refresh, reset_filter, lock, eeprom, discover
├── test_init.py               ← setup_entry happy/error, dynamic-devices end-to-end
├── test_config_flow.py        ← config + options + reconfigure flows
├── test_diagnostics.py        ← async_get_config_entry_diagnostics
└── test_smoke.py              ← imports + manifest sanity
```

**193 tests, 30 s wall-clock, 96% coverage** (`pytest --cov`).

### 7.2 Patrones clave

- **Mock del client**: todos los métodos pymodbus son `AsyncMock` con defaults razonables (en `conftest.py`).
- **`setup_integration` fixture**: `yield` dentro del `with patch(...)` para mantener el mock activo durante reloads disparados por options flow.
- **Entity_id discovery**: `er.async_get(hass).async_get_entity_id(platform, DOMAIN, uid)` con `indoor_uid(idx, suffix)` — robusto a cambios de traducción.
- **Polling disabled por default en tests**: `CONF_POLLING_ENABLED: False` en `config_entry` fixture; evita lingering tasks que rompan otras tests.

---

## 8. Quality scale — estado por nivel

### Bronze (18/18)
- ✅ action-setup (exempt), appropriate-polling, brands (exempt — custom_component), common-modules, config-flow, config-flow-test-coverage, dependency-transparency, docs-* (high-level, install, removal), entity-event-setup, entity-unique-id, has-entity-name, runtime-data, test-before-configure, test-before-setup, unique-config-entry, docs-actions (exempt).

### Silver (10/10)
- ✅ action-exceptions (exempt), config-entry-unloading, docs-config-params, docs-install-params, entity-unavailable, integration-owner, log-when-unavailable, parallel-updates, reauthentication-flow (exempt — Modbus TCP no auth), test-coverage.

### Gold (21/21)
- ✅ devices, diagnostics, discovery (exempt), discovery-update-info (exempt), docs-data-update, docs-examples, docs-known-limitations, docs-supported-devices, docs-supported-functions, docs-troubleshooting, docs-use-cases, dynamic-devices, entity-category, entity-device-class, entity-disabled-by-default, entity-translations, exception-translations (exempt), icon-translations, reconfiguration-flow, repair-issues, stale-devices.

### Platinum (3/3)
- ✅ async-dependency, inject-websession (exempt — no HTTP), strict-typing.

---

## 9. Deploy

### 9.1 Dev → Prod

```bash
# Desde la raíz del proyecto, con /Volumes/config/ montado
cp -r custom_components/hisense_vrf/* /Volumes/config/custom_components/hisense_vrf/
cp -r pyacmodbus-stub/pyacmodbus /Volumes/config/custom_components/hisense_vrf/
rm -rf /Volumes/config/custom_components/hisense_vrf/__pycache__ \
       /Volumes/config/custom_components/hisense_vrf/pyacmodbus/__pycache__
```

Después: reload de la integración desde la UI **o** restart de HA prod si hubo cambios en `pyacmodbus/` (Python cache).

### 9.2 Restart automatizado via API

Script en `/tmp/disable_exp.py` muestra el patrón:
1. Trigger `POST /api/services/homeassistant/restart`
2. Poll HTTP hasta detectar shutdown (HTTP 000)
3. Sleep ~8 s para final flush del registry
4. (Edit del registry si necesario, mientras HA está apagado)
5. Poll hasta HTTP 200

Token long-lived en [[reference_ha_access]].

---

## 10. Future work

Items conocidos no urgentes:

- **PyPI publish** de `pyacmodbus` → eliminar bundle + `sys.path` workaround.
- **Recorder exclude** de las EXP que el user reactive — actualmente ninguna está excluida, si reactiva alguna su history se vuelve a acumular.
- **Higiene del recorder DB** — en deployments donde el DB creció (típicamente porque se habilitaron EXP entities), configurar `recorder.purge_keep_days` y disparar `recorder.purge` para recuperar espacio.
- **HACS publish** una vez que `pyacmodbus` esté en PyPI.
- **Outdoor `compressor_active`**: cuando los registros operacionales 17-53 son todos cero, devolver `None` en lugar del raw 0 (cosmético).
- **Sensor de energía**: `current_primary × voltaje_conocido → kW` para el dashboard de energía de HA.
