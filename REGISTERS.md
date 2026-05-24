# Hisense VRF — Modbus Register Map

This document maps every register the integration uses against its meaning, scale and direction (R/W). It cross-references the **i-Modkit HCPC-H2M1C** manual and the official **Hisense Mapping Table** spreadsheet shipped with the gateway.

## Address layout

| Region                          | Range         | Notes                                  |
|---------------------------------|---------------|----------------------------------------|
| Indoor unit blocks              | 40000 + n×91  | One 91-register block per indoor unit. |
| Outdoor unit-connection bitmap  | 1000..1005    | One register per refrigerant system.   |
| Outdoor module parameters       | 5000 + s×490 + m×98 | 98-register block per (system, module). |
| Gateway-level                   | 4996..4999    | Four global registers.                 |

The integration uses Modbus function codes **0x03** (Read holding registers) and **0x10** (Write multiple registers). Function code 0x06 is intentionally avoided — it isn't listed as supported in Table 4.1 of the i-Modkit manual.

---

## Indoor unit block (91 registers, offsets 0..90)

Each indoor unit `n` occupies registers `40000 + n*91 .. 40090 + n*91`. Below, **offset** is relative to the unit's base address.

### Status / read-only (offsets 0..32, 77)

| Offset | Name                  | R/W | Type / Scale                                                | Notes |
|-------:|-----------------------|:---:|-------------------------------------------------------------|-------|
|   0    | `REG_UNIT_CODE`       | R   | uint16                                                      | 0 = slot empty (no unit at this index). |
|   1    | `REG_CAPACITY`        | R   | uint16; kBTU/h                                              | Capacity code in thousands of BTU/h. |
|   2    | `REG_STATUS`          | R   | bitmask                                                     | bit0=running, bits1-2=op_state (0=Stop,1=TH_OFF,2=TH_ON,3=Alarm), bit3=oil_return. |
|   3    | `REG_CURR_MODE`       | R   | bitmask                                                     | bits0-4=mode (`MODE_*`), bit5=`filter_sign_reset` (0 → filter alarm). |
|   4    | `REG_FAN_SW`          | R   | bitmask                                                     | Fan speed reported by the wire controller (`FAN_*`). |
|   5    | `REG_MODE_JUMP`       | R   | bitmask                                                     | Modes currently disallowed by the unit (bit=1 → mode can't be set). |
|   6    | `REG_FAN_JUMP`        | R   | bitmask                                                     | Fan speeds currently disallowed. |
|   7    | `REG_MISC`            | R   | bitmask                                                     | bit0=test_run, bit1=remote_ctrl_active, bit2=swing_active, bit3=full_heat_exchange, bit4=auto_swing, bits5-7=louver_position. |
|   8    | `REG_SETPOINT_R`      | R   | uint8 °C; 0xFF = not configured                             | 0xFF maps to `setpoint = None` in code (= UI "unknown"). |
|   9    | `REG_COOL_MAX`        | R   | uint16 °C                                                   | Cooling upper limit. |
|  10    | `REG_COOL_MIN`        | R   | uint16 °C                                                   | Cooling lower limit. |
|  11    | `REG_HEAT_MAX`        | R   | uint16 °C                                                   | Heating upper limit. |
|  12    | `REG_HEAT_MIN`        | R   | uint16 °C                                                   | Heating lower limit. |
|  13    | `REG_RC_GROUP`        | R   | uint16                                                      | Wire-controller group. |
|  14    | `REG_TEMP_CORR`       | R   | uint16 (low 3 bits)                                         | Temp setting correction value 0–4. |
|  15    | `REG_HUMIDITY`        | R   | uint16 %                                                    | Current humidity (newer units) / radiation comp (older). |
|  16    | `REG_TEMP_TG`         | R   | int16 °C                                                    | Heat-exchange tube temperature Tg. |
|  17    | `REG_TEMP_TRMT`       | R   | int16 °C                                                    | Wire-controller thermistor temperature. |
|  18    | `REG_TEMP_TL`         | R   | int16 °C                                                    | Heat-exchange liquid tube temperature Tl. |
|  19    | `REG_OUTLET_TEMP`     | R   | int16 °C                                                    | Blow-out temperature TO. |
|  20    | `REG_INLET_TEMP`      | R   | int16 °C                                                    | Room / inlet temperature TI. |
|  21    | `REG_FREQ_REQ`        | R   | uint16 Hz                                                   | Compressor frequency request Fd. |
|  22    | `REG_ALARM`           | R   | uint8 (hex code stored as int)                              | 0 = no alarm. See `ALARM_CODES` in `sensor.py` for the mapping. |
|  23    | `REG_SHUTDOWN`        | R   | uint16                                                      | Last shutdown reason d1 (values not documented in available sources). |
|  24    | `REG_EXP_HIGH`        | R   | uint16                                                      | Expansion valve opening — high bits. |
|  25    | `REG_EXP_LOW`         | R   | uint16                                                      | Expansion valve opening — low bits. |
|  26    | `REG_FUNC_DISP`       | R   | uint8                                                       | Function selection display (8-bit raw). |
|  27    | `REG_EXP_VALVE`       | R   | uint16 %                                                    | Expansion valve opening %. |
|  28    | `REG_FAN_ACTUAL`      | R   | bitmask                                                     | Actually-running fan speed (`FAN_ACTUAL_*` — distinct from `FAN_*`). |
|  29    | `REG_HOST_SYS`        | R   | uint16                                                      | Outdoor system number (used in device name). |
|  30    | `REG_HOST_ADDR`       | R   | uint16                                                      | Outdoor address number (used in device name). |
|  31    | `REG_UNIT_SYS`        | R   | uint16                                                      | Indoor system number. |
|  32    | `REG_UNIT_ADDR`       | R   | uint16                                                      | Indoor address number. |
|  33–47 | reserved              | —   | —                                                           | — |
|  48–67 | **Function selection 1–20** | R/W | uint16 bitmask                                          | See [Function selection bits](#function-selection-bits-offsets-4867). |
|  68    | clear function selection | R/W | uint16                                                   | — |
|  69–76 | reserved              | —   | —                                                           | — |
|  77    | `REG_DRY_MODE`        | R/W | uint16                                                      | Refreshing dehumidification (0=Dry1, 1=Dry2, 2=Dry3). |

### Control / writable (offsets 78..89)

| Offset | Name                     | R/W | Values                                                 | Notes |
|-------:|--------------------------|:---:|---------------------------------------------------------|-------|
|  78    | `REG_RUN_STOP`           | R/W | 0=Stop, 1=Run                                          | Sent as part of the bundled 5-reg write for ON events. |
|  79    | `REG_SET_MODE`           | R/W | `MODE_*` bitmask                                       | 1=AUTO, 2=COOL, 4=DRY, 8=FAN, 16=HEAT. |
|  80    | `REG_SET_FAN`            | R/W | `FAN_*` bitmask                                        | 1=AUTO, 2=HIGH, 4=MED, 8=LOW. |
|  81    | `REG_SET_SWING`          | R/W | bit0=auto, bits1-3=position (1–7)                       | Position 0 with bit0=1 → "auto", otherwise fixed position 1–7. |
|  82    | `REG_SET_TEMP`           | R/W | °C int (cool 19–30, heat 17–30)                         | The integration sends the cached `int(setpoint)`. |
|  83    | `REG_FILTER_RST`         | R/W | write 1                                                | One-shot: clears the filter alarm. |
|  84    | `REG_PROHIBIT_ALL`       | R/W | 1=lock all, 2=unlock all                                | Convenience over the per-button locks. |
|  85    | `REG_PROHIBIT_SW`        | R/W | 0/1                                                    | Lock on/off button. |
|  86    | `REG_PROHIBIT_MODE`      | R/W | 0/1                                                    | Lock mode button. |
|  87    | `REG_PROHIBIT_FAN`       | R/W | 0/1                                                    | Lock fan button. |
|  88    | `REG_PROHIBIT_SWING`     | R/W | 0/1                                                    | Lock swing button. |
|  89    | `REG_PROHIBIT_TEMP`      | R/W | 0/1                                                    | Lock temperature buttons. |
|  90    | reserved                 | —   | —                                                       | — |

### Mode bitmasks

`REG_CURR_MODE` (R) and `REG_SET_MODE` (W) use the same 5-bit encoding:

| Constant      | Value | HA `HVACMode` |
|---------------|------:|----------------|
| `MODE_AUTO`   | 0x01  | `HEAT_COOL` (requires B8 enabled) |
| `MODE_COOL`   | 0x02  | `COOL` |
| `MODE_DRY`    | 0x04  | `DRY`  |
| `MODE_FAN`    | 0x08  | `FAN_ONLY` |
| `MODE_HEAT`   | 0x10  | `HEAT` |

### Fan speed bitmasks (setpoint side, `REG_FAN_SW` and `REG_SET_FAN`)

| Constant    | Value | HA `fan_mode` |
|-------------|------:|---------------|
| `FAN_AUTO`  | 0x01  | `auto`        |
| `FAN_HIGH`  | 0x02  | `high`        |
| `FAN_MED`   | 0x04  | `medium`      |
| `FAN_LOW`   | 0x08  | `low`         |

### Fan speed bitmasks (actual side, `REG_FAN_ACTUAL`)

Different bit positions than setpoint; only one bit active at a time:

| Constant            | Value | Description |
|---------------------|------:|-------------|
| `FAN_ACTUAL_HH2`    | 0x01  | High-High 2 (turbo) |
| `FAN_ACTUAL_HIGH`   | 0x02  | High |
| `FAN_ACTUAL_MED`    | 0x04  | Medium |
| `FAN_ACTUAL_MEDLOW` | 0x08  | Medium-Low |
| `FAN_ACTUAL_LOW`    | 0x10  | Low |
| `FAN_ACTUAL_MUTE`   | 0x20  | Mute |
| `FAN_ACTUAL_BREEZE` | 0x40  | Breeze |

---

## Function selection bits (offsets 48..67)

Each function-selection register packs up to 8 boolean features (one per bit). To change anything you must:

1. **Stop the indoor unit** (the manual says writes can fail silently otherwise).
2. **Read all 20 registers**, modify only the bits you want to change.
3. Write all 20 with one `0x10` (Write multiple registers) frame.
4. Press **Clear EEPROM** on the gateway (`REG_GW_EEPROM_CLEAR = 4999`, write 1).
5. Reconnect the integration so the firmware re-validates the new configuration.

Bit definitions come from the Hisense Mapping Table sheet `Function selection analysis`.

### Function setting 1 — register 40048

| Bit | Code | Meaning                                            |
|----:|------|----------------------------------------------------|
| 7   | B3   | Enforced 3 min minimum compressor operation time   |
| 6   | C1   | Cool only                                          |
| 5   | B2   | Heat exchanger                                     |
| 4   | D2   | Remote pulse start and stop (`0` fix in firmware)  |
| 3   | D1   | Power supply ON/OFF 1                              |
| 2   | C3   | HA function                                        |
| 1   | B1   | Cancellation of heating temperature compensation   |
| 0   | —    | Indoor sensor (`0` fix)                            |

### Function setting 2 — register 40049

| Bit | Code | Meaning                                            |
|----:|------|----------------------------------------------------|
| 7   | —    | 1=Cool / 0=Heat flag                               |
| 6   | C9   | Automatic swing louver (`0` fix)                   |
| 5   | D3   | Power supply ON/OFF 2                              |
| 4   | B5   | Fixing of operating mode                           |
| 3–0 | B4   | Filter cleaning time enum: 00=Indoor std, 01=100h, 02=1200h, 03=2500h, 04=No indication |

### Function setting 3 — register 40050

| Bit | Code | Meaning                                            |
|----:|------|----------------------------------------------------|
| 7   | B9   | Fixing of fan speed                                |
| 6   | C4   | Drain pump operation at heating                    |
| 5   | B6   | Fixing of setting temperature                      |
| 4   | C8₁  | Remote control temperature control (mode 1)        |
| 3   | B7   | Fixing of cooling                                  |
| 2   | **B8**   | **Automatic COOL/HEAT operation** — enables HVACMode.HEAT_COOL |
| 1   | C6   | Hi speed at heating thermostat-OFF                 |
| 0   | —    | Remote control prohibition (`0` fix)               |

### Function setting 4 — register 40051

| Bit | Code | Meaning                                            |
|----:|------|----------------------------------------------------|
| 7   | —    | Option input/output config change (must be 1 during centralized control) |
| 6   | E5   | Residual operation 1                               |
| 5   | E4   | Precooling / preheating time (00=No, 01=30 min, 02=60 min) |
| 3   | E3   | With humidifier                                    |
| 2   | E2   | Operation to increase fan speed                    |
| 1   | E1   | Air exchange mode (01=Auto, 10=Full heat, 11=Normal) |

### Function setting 5 — register 40052

| Bit | Code | Meaning                                            |
|----:|------|----------------------------------------------------|
| 7   | —    | Wire controller all prohibition (`0` fix)          |
| 6   | C7   | Cancel 3-min protection                            |
| 5   | C8₂  | Remote control temperature control (mode 2)        |
| 4-3 | C5   | Indoor fan Hi speed enum: 00=Standard, 01=Hi 1, 02=Hi 2 |
| 2   | CB   | Forced stop logic selection (00=A contact, 01=B contact) |
| 1   | —    | Reserved                                           |
| 0   | CC   | Standby power                                      |

### Function setting 6 — register 40053

| Bit | Code | Meaning                                            |
|----:|------|----------------------------------------------------|
| 7   | BE   | Current comfort index representation               |
| 6   | BC   | Current temperature representation                 |
| 5   | BD   | Current humidity representation                    |
| 4-3 | BB   | Cooling temp compensation enum: 00=Standard, 01=-1, 02=-2 |
| 2   | D5   | Prevention of heating discharge temperature decrease |
| 1   | D4   | Prevention of cooling discharge temperature decrease |
| 0   | B1₂  | Heating setting temperature +2                     |

### Function setting 7 — register 40054

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | F1   | Turn-off timer auto setting (0.5–24.0 × 2 [h]) |

### Function setting 8 — register 40055

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | —    | Option centralized 1 (must be 1 with centralized controller) |
| 6   | CE   | Swing louver individual                             |
| 5   | CD   | Thermistor / humidity sensor selection              |
| 4   | D6   | Energy saving room temperature control              |
| 2   | D7   | Lift panel falling distance (00=Indoor std, 01=100…06=350, 07=400 cm) |

### Function setting 9 — register 40056

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | —    | Option clear (1=clear, 0=not clear) |
| 6   | EE   | Automatic fan speed mode (informational — not used as capability) |
| 4   | EC   | Forced THOFF stop at cooling |
| 3   | EB   | Fan deceleration when turning off cool/heat (00=no, 01=weak, 10=strong) |
| 1   | CF   | Swing louver swing range (00=standard, 01=airflow-prevention, 10=high-raise) |

### Function setting 10 — register 40057

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | EA   | Indoor residual op when humidifier installed (00=no, 01=120, 02=180) |
| 5   | E8   | GHP function 2 (heating temp control TNOFF fan decel) |
| 4   | E7   | GHP function 1 |
| 2   | E6   | Indoor fan residual op when cooling OFF (00=no, 01=60 min, 10=120 min) |

### Function setting 11 — register 40058

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | F3   | Automatic reset of setting temperature |
| 6   | F7   | Wire controller stops delay |
| 5   | F5   | Refrigeration auto resetting temperature |

### Function setting 12 — register 40059

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | F4   | Automatic reset time (00=30 min CVS std, 01=20, 10=10) |
| 5   | F6   | Heating auto resetting temperature |

### Function setting 13 — register 40060

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | FC   | Cooling lower-limit temperature added value |
| 3   | FD   | Heating upper-limit temperature reduction value |

### Function setting 14 — register 40061

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | FE   | Automatic operating temp at heating (00=5, 01=10, 02=15 °C) |
| 5   | —    | Centralized lock function operation (run/stop, `0` fix) |
| 4   | FF   | Lock function: ON/OFF timer |
| 3   | Fb   | Lock function: auto |
| 2   | FA   | Lock function: fan speed |
| 1   | F9   | Lock function: temperature adjustment |
| 0   | F8   | Lock function: run switch |

### Function setting 15 — register 40062

| Bit | Code | Meaning |
|----:|------|---------|
| 7   | —    | Prepare (placeholder, `0` fix) |

### Function setting 16 — register 40063 (Input setting 1)

Each bit configures how the corresponding physical input pin is interpreted.

| Bit | Meaning |
|----:|---------|
| 7   | Get ready |
| 6   | Remote cool and warm switch |
| 5   | Forced stop |
| 4   | Remote start and stop 2 (stop) |
| 3   | Remote start and stop 2 (drive) |
| 2   | Remote start and stop 1 |
| 1   | Room temperature control (heating) |
| 0   | Room temperature control (cooling) |

### Function setting 17 — register 40064 (Input setting 2)

Same bit semantics as Input setting 1, but bit 7 is "Lifting grille" instead of "Get ready".

### Function setting 18, 19, 20 — registers 40065/40066/40067 (Output setting 1/2/3)

Each bit configures what state the corresponding physical output pin reflects.

| Bit | Meaning |
|----:|---------|
| 7   | Lifting grille (40065, 40066) / Get ready (40067) |
| 6   | Full heat exchange |
| 5   | Heating THON |
| 4   | Heating |
| 3   | Refrigeration THON |
| 2   | Refrigeration |
| 1   | Alarm |
| 0   | Run |

---

## Gateway registers

| Address | Constant            | R/W | Description |
|--------:|---------------------|:---:|-------------|
| 4996    | `GW_ALARM_DISPLAY`  | R/W | Alarm display control on the gateway 7-segment tube. |
| 4997    | `GW_UNIT_COUNT`     | R   | Number of indoor units detected on the H-NET bus. Used for `scan_devices`. |
| 4998    | `GW_CTRL_MODE`      | R   | 1 = gateway is in Modbus control mode (i.e. centralized). |
| 4999    | `GW_EEPROM_CLEAR`   | R/W | Write 1 after function-selection edits. |

---

## Outdoor unit registers

### Connection bitmap (registers 1000..1005)

One register per refrigerant system (0..5). Each register's bit N indicates that module N of that system is connected.

```python
for sys_idx in range(6):
    reg = result.registers[sys_idx]
    for mod_idx in range(5):
        if reg & (1 << mod_idx):
            connected.append((sys_idx, mod_idx))
```

### Per-module parameters (offsets 0..53 within `5000 + s×490 + m×98`)

| Offset | Name                   | Type / Scale          | Notes |
|-------:|------------------------|-----------------------|-------|
|  0–14  | ASCII model name       | uint16, char per reg  | Trim trailing 0/spaces; printable range 0x20–0x7E. |
|  15    | `OU_CAPACITY`          | uint16 kBTU/h         | Outdoor capacity code. |
|  16    | unit identifier        | uint16                | Outdoor unit id. |
|  17    | `OU_RUN_STATUS`        | uint16 enum           | Values not documented in available sources. |
|  18    | `OU_PROTECTION_INFO`   | uint16 enum           | Protection / fault code. |
|  20    | `OU_FAN_STAGE`         | uint16 (0–26)         | Outdoor fan output stage. |
|  29    | `OU_TDO`               | uint8 °C              | Discharge gas temp Tdo. |
|  30    | `OU_TEMP_AMBIENT`      | int8 °C (signed)      | Outdoor ambient Ta. |
|  31    | `OU_PRESSURE_PD`       | uint8 raw             | Discharge pressure (raw). |
|  32    | `OU_PRESSURE_PS`       | uint8 raw             | Suction pressure (raw). |
|  33    | `OU_EV_B`              | uint16 %              | EVB expansion valve opening. |
|  34    | `OU_EV_J`              | uint16 %              | EVJ expansion valve opening. |
|  35    | `OU_TEMP_TSC`          | int8 °C               | Subcooling temperature Tsc/TBg. |
|  36    | `OU_TEMP_TCHG`         | int8 °C               | Tchg. |
|  37    | `OU_TEMP_TD_AVG`       | uint8 °C              | Average discharge temperature. |
|  40    | `OU_TEMP_TD`           | uint8 °C              | Discharge temperature Td. |
|  41–42 | `OU_RUNTIME_HI/LO`     | uint16 + uint16       | `runtime_hours = (hi << 16) | lo`. |
|  43    | `OU_CURRENT_PRI`       | uint16 A              | Primary current. |
|  44    | `OU_CURRENT_SEC`       | uint16 × 0.5 A        | Secondary current. |
|  45    | `OU_INVERTER_STATUS`   | uint16 enum           | Inverter module status. |
|  47    | `OU_TEMP_FIN`          | int8 °C               | FIN temperature. |
|  48    | `OU_FREQ_ACTUAL`       | uint16 Hz             | Inverter actual frequency H1. |
|  50    | `OU_EV_O`              | uint16 %              | EVO expansion valve opening. |
|  51    | `OU_TEMP_TE`           | int8 °C               | Evaporation temperature Te. |
|  52    | `OU_TEMP_TG`           | int8 °C               | Outdoor temperature Tg. |
|  53    | `OU_FAN_STATUS`        | uint16 enum           | FAN controller status. |
|  54–97 | reserved               | —                     | — |

> **Note on outdoor operational data**: in some i-Modkit firmware versions, offsets 17–53 stay at 0 even when the outdoor compressor is active. Offsets 0–16 (model name, capacity, id) are always populated. This is a firmware quirk — the integration reports the raw values; if you see all-zeros for the operational fields it means your gateway doesn't surface them. See CLAUDE.md in the original `homeassistant/` project for the diagnostic notes.

---

## Sources

- Hisense i-Modkit HCPC-H2M1C Modbus Adapter Manual (PDF) — sections 4.1–4.10, 6 (notes & attentions).
- Hisense iModkit Mapping Table (Excel) — sheets `Parameter mapping table`, `Function selection analysis`, `Function selection bit limit`, `Outdoor unit connection confirm`.
- Hisense HiSmart H5 Technical Service Handbook — alarm codes (`ALARM_CODES` in `sensor.py`).

The Excel mapping table is the authoritative source for the function-selection bit definitions; the manual focuses on usage examples and warnings.
