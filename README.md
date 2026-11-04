# Barcode Scanning Vision Gate

Functional design and Python host-software design for a conveyor-mounted vision gate that automatically reads the 1D barcode / 2D QR code on shoe boxes and reliably associates each scan result with the correct box.

## Table of Contents

1. [Overview](#1-overview)
2. [Known Conditions and Equipment](#2-known-conditions-and-equipment)
3. [System Design](#3-system-design)
4. [Dual-Photoelectric Trigger Scheme](#4-dual-photoelectric-trigger-scheme)
5. [Timing and Feasibility Analysis](#5-timing-and-feasibility-analysis)
6. [Wiring and Network](#6-wiring-and-network)
7. [Control Logic and Code-to-Box Association](#7-control-logic-and-code-to-box-association)
8. [Industrial PC Selection](#8-industrial-pc-selection)
9. [I/O Allocation and Terminals](#9-io-allocation-and-terminals)
10. [Mechanical Design and BOM](#10-mechanical-design-and-bom)
11. [Python Host Software Design](#11-python-host-software-design)
12. [Testing, Acceptance, Commissioning](#12-testing-acceptance-commissioning)
13. [Assumptions and Open Items](#13-assumptions-and-open-items)
14. [Conclusion](#14-conclusion)
15. [References](#15-references)

---

## 1. Overview

The vision gate is installed on a conveyor line. It reads the 1D/2D code on the outer carton of each shoe box and outputs an OK/NG result to the upper system, a PLC, or a reject mechanism.

### 1.1 Functional Goals

- Read barcodes/QR codes reliably at a conveyor speed of **0.8 m/s**.
- Build box-tracking information from two photoelectric sensors and decide which box each scan result belongs to.
- Support two installation modes: **left-right scanning** and **front-rear scanning**.
- Output OK/NG results to the upper system, PLC, or reject mechanism.
- Provide logging, alarms, parameter configuration, and traceability.

### 1.2 Design Principles

- Unified mechanical structure compatible with both scanning directions.
- Industrial electrical interfaces: photoelectric sensors and actuators connect through industrial DI/DO.
- Traceable control logic: scan results are strongly bound to box tracks.
- The Python host handles **device management + track association + log traceability**. It does **not** run heavy image algorithms.

---

## 2. Known Conditions and Equipment

### 2.1 Process Conditions

| Item | Value |
| --- | --- |
| Conveyor speed | 0.8 m/s |
| Target | 1D barcode or 2D QR code on the outer carton |
| Box attitude after guiding | Code on **left/right side**, or on **front/rear end** |
| Mode selection | One of the two modes is enabled per site, depending on guide direction |
| Trigger | Dual photoelectric sensors |
| Photoelectric input | Industrial DI of the industrial PC |
| Camera | OPT-IDD1-M050G-L08 |

### 2.2 Camera Key Parameters

| Item | Value |
| --- | --- |
| Model | OPT-IDD1-M050G-L08 |
| Sensor | CMOS, global shutter |
| Resolution | 2448 x 2048 |
| Max processing frame rate | 30 fps |
| Max decode speed | 80 codes/s |
| Lens | 8 mm |
| Reading distance | 50 mm ~ 2000 mm |
| Communication | TCP / UDP / Serial / Ethernet/IP / FTP / HTTP / Modbus / Profinet / MC / Fins |
| Electrical I/O | 3 opto-isolated DI, 3 opto-isolated DO, 1 RS232, 1 Gigabit Ethernet |
| Power | 24 VDC ±20%, < 30 W |
| Protection | IP62 / IP65 |

Field of view (FOV) vs. working distance:

| Working distance | FOV |
| --- | --- |
| 50 mm | 47.6 x 40.6 mm |
| 100 mm | 91.2 x 76.3 mm |
| 300 mm | 254.5 x 216.1 mm |
| 600 mm | 518.9 x 424.1 mm |
| 1000 mm | 842.4 x 704.8 mm |
| 2000 mm | 1680.8 x 1406.2 mm |

### 2.3 Installation Constraints

- Boxes travel along the conveyor centerline.
- Side cameras are angled relative to the conveying direction (suggested 10°~20°) to reduce specular reflection.
- Side camera to nearest box surface: about **250 mm**.
- Top camera to nearest box surface: about **120 mm**.

---

## 3. System Design

### 3.1 System Components

- Mechanical gantry
- 2 scanning cameras
- 2 photoelectric sensors
- 1 RK3588 industrial PC
- 1 industrial switch
- 1 set of 24 V power supply
- 1 tri-color light / buzzer
- Optional: reject cylinder or NG sorting interface

### 3.2 Operating Modes

| Mode | Applies when | Cameras enabled |
| --- | --- | --- |
| **A: Left-Right (LR)** | Code on left/right side of box | Left + right cameras read both sides simultaneously or nearly so |
| **B: Front-Rear (FB)** | Code on front/rear end of box | Front + rear cameras trigger separately according to their own installation offsets |

Mode selection rules:

- Choose one mode per project according to the box attitude produced by the guide mechanism.
- By default, **do not enable both groups at once**, to avoid duplicate investment and added association complexity.
- Reserve mounting holes for both modes on the frame; only install the camera group that is needed.

### 3.3 Layout Schematics

Left-right mode (top view):

```
 flow ──────────────────────────────────────►
        PE1     PE2     Reading plane
         │       │          │
 ────────┼───────┼──────────┼────────  conveyor (box on centerline)
         │       │      Camera_L  (10°~20° to flow, left)
         │       │      Camera_R  (10°~20° to flow, right)
```

- **PE1**: entry detection, creates the box track.
- **PE2**: secondary confirmation and speed measurement, main trigger reference.
- **Camera_L / Camera_R**: angled 10°~20° relative to flow to reduce reflection.

Front-rear mode: the front and rear cameras are triggered separately according to their own installation reference distances; simultaneous triggering is not required.

Side view: the side camera is ~250 mm from the box, the top camera ~120 mm; the sensors are mounted at the height of the box's stable blocking surface.

---

## 4. Dual-Photoelectric Trigger Scheme

### 4.1 Roles

- **PE1**: detects a box entering the gate and creates a tracking object.
- **PE2**: detects the box reaching the pre-read reference position, corrects the speed, and generates the actual trigger time.

### 4.2 Installation Recommendations

- Prefer **through-beam 24 V industrial** sensors.
- PE1 and PE2 spaced **100~150 mm** along the conveying direction.
- PE2 to camera best-read plane should be adjustable; initial suggestion **80~120 mm**.
- Mount the sensors at the height of the box's stable blocking surface, to avoid sensing only the lid or a gap.

### 4.3 Why Two Sensors

A single sensor can only do "shoot when in position" and struggles with:

- small conveyor speed fluctuations
- varying box lengths
- adjacent boxes in continuous flow getting mixed up

Dual sensors provide: entry time, re-confirmation time, estimated line speed, estimated box length, and a more reliable code-box binding window.

### 4.4 Recommended Scan Strategy

Use **dual-sensor tracking + continuous camera scanning + per-box independent time-window binding**. Do not use "one trigger, one frame".

Reasoning: at 0.8 m/s and 30 fps, the frame interval is ~33 ms, during which a box advances ~26.7 mm. A single frame can miss because of reflection, slight vibration, a code only partly in view, or a blurred frame.

Mechanism:

1. **PE1 rising edge**: create a `BoxTrack`.
2. **PE2 rising edge**: correct speed and compute the read time window for this box.
3. While any active `BoxTrack` exists, the camera keeps scanning.
4. Each code result returned by the camera is bound by the host to a box according to the time windows.
5. When all active `BoxTrack`s have ended, delay and then stop continuous scanning.

### 4.5 Closing Conditions

Closing only "a delay after PE1 goes low" is not enough: PE1 low only means the box tail left PE1, not that it left the best read area, and in continuous flow several boxes share one scan period. Use a combination:

- **Per-box window closes** when any of the following holds:
  - PE1 falling edge + `tail_delay_ms`
  - predicted time of leaving the read zone is reached
  - `max_track_window_ms` is reached
  - first valid code read + `track_hold_after_ok_ms`
- **Continuous scanning stops** when the number of active `BoxTrack`s is 0 and `idle_off_delay_ms` has elapsed.

This keeps scanning uninterrupted for continuous boxes while each box still has an independent, traceable time window.

### 4.6 Fallback If the Camera Lacks Continuous Mode

Prefer the camera's native "continuous read / external-enable continuous decode" mode. If the camera supports only single trigger / single return, trigger **3~5 times** inside the box's valid window until a valid code is read or the window times out. This is a sub-optimal fallback.

---

## 5. Timing and Feasibility Analysis

### 5.1 Conveyor Speed

- 0.8 m/s = 800 mm/s.
- With PE1–PE2 spacing of 100 mm, a box passes both sensors in ~125 ms.
- With PE2-to-reading-plane distance of 100 mm, ~125 ms remain after PE2 detection before reading.

Conclusion: acquiring DI in Python and sending camera commands over the network is **feasible**. For stricter determinism, reserve a backup hardware link **IPC DO -> camera DI**.

### 5.2 Camera Capability

The camera decodes up to 80 codes/s, far above the normal box rate. Even in continuous flow, bottlenecks are more likely to be lighting/reflection, code position dispersion, insufficient box gap, or poor code quality.

### 5.3 Field-of-View Risk

With the 8 mm lens the FOV is fixed per working distance:

- Top camera at ~120 mm: FOV only about 100 mm.
- Side camera at ~250 mm: FOV about 200 mm.

If boxes are large or the label position varies, the code may fall **outside the FOV**. Confirm:

- max/min box size
- tolerance of label position relative to the box edge
- minimum code size and module width

If the code position varies widely: raise working distance to 250~350 mm and re-check the FOV, use a shorter focal length, or require the guide to steer the code area into a fixed window.

### 5.4 Initial Multi-Frame Parameters

| Parameter | Initial range |
| --- | --- |
| `track_hold_after_ok_ms` | 30~80 ms |
| `tail_delay_ms` | 50~100 ms |
| `idle_off_delay_ms` | 100~200 ms |
| `max_track_window_ms` | 250~500 ms |

At 0.8 m/s, 50 ms ≈ 40 mm and 100 ms ≈ 80 mm of travel.

---

## 6. Wiring and Network

### 6.1 Wiring Principles

- Photoelectric signals connect to the IPC **industrial DI**.
- Both cameras connect via a **gigabit switch** to IPC **ETH0**.
- IPC **ETH1** connects separately to the dispatch host / upper-level network.
- IPC DO outputs OK / NG / alarm / reject signals.
- With a FriendlyElec generic dev board, an extra 24 V -> 12 V DC/DC is usually needed and GPIO is **not** industrial 24 V DI/O.

### 6.2 Dual-NIC Topology

| NIC | Purpose | Example subnet |
| --- | --- | --- |
| ETH0 | Camera device network (via industrial switch to Camera A/B) | 192.168.10.0/24 |
| ETH1 | Dispatch host network (result reporting, parameter delivery, remote maintenance) | 192.168.20.0/24 |

Example addresses:

- Device network: IPC ETH0 `192.168.10.10`, Camera A `192.168.10.101`, Camera B `192.168.10.102`
- Dispatch network: IPC ETH1 `192.168.20.10`, dispatch host `192.168.20.100`

Reasons: camera traffic is isolated from upper-level traffic; two cameras occupy only one device-side port; easy to add a maintenance PC or a third Ethernet device later.

### 6.3 Camera I/O Reserve

The camera's 17-pin M12 I/O can be reserved for a backup hardware trigger input, a read-success output, or linked external light / capture signals.

- Default architecture: `PE -> IPC DI`, `IPC -> network command triggers camera`.
- Optional enhancement: `IPC DO -> camera DI0`, so a deterministic hardware pulse is emitted after Python decides the timing.

### 6.4 Photoelectric Wiring

- If the IPC DI supports PNP input, prefer **PNP normally-open** sensors.
- If the IPC supports only dry contacts, use relay-output sensors or add interface relays.
- Route I/O cables in separate ducts from network cables.
- Number all I/O cables and keep a wire list.

---

## 7. Control Logic and Code-to-Box Association

### 7.1 Main Flow

```
PE1 rise  -> create BoxTrack
PE2 rise  -> match track, compute speed, open scan window, ensure scan session running
Camera results stream in -> bind to track by time window
Window closes / results complete -> decide OK/NG -> output DO -> save record
No active tracks + idle delay -> stop scan session
```

### 7.2 Exception Branches

| Code | Condition |
| --- | --- |
| `CAM_TIMEOUT` | Camera timed out with no return |
| `NO_READ` | Neither camera read a code |
| `AMBIGUOUS` | Two cameras read different target codes |
| `SENSOR_SEQUENCE_ERROR` | Abnormal photoelectric order |
| `TRACK_OVERLAP` | Box gap too small, windows overlap |
| `DEVICE_OFFLINE` | Network disconnect or device offline |
| `UNBOUND_RESULT` | Result falls in no active window |

### 7.3 Track-Queue Model

The system must not just ask "which camera read the code"; it decides the owning box through **dual-sensor timing + continuous scan session + per-box time window**.

- One `BoxTrack` per box, holding all its timing information.
- A camera result can only fall into the valid window of some `BoxTrack`.
- Multiple `BoxTrack`s may coexist in the same continuous scan session.

`BoxTrack` fields:

```
track_id
mode                      # LR / FB
pe1_on_ts, pe1_off_ts
pe2_on_ts, pe2_off_ts
speed_mm_s
length_mm
scan_window_start_ts
scan_window_end_ts
scan_close_reason
camera_results[]
final_code
final_status              # OK / NO_READ / AMBIGUOUS / TIMEOUT
```

### 7.4 Binding Strategy

**LR mode**
- Both Camera_L and Camera_R results bind to the same `BoxTrack`.
- Both return the same code -> `OK`; only one side returns a valid code -> still `OK`; different codes -> `AMBIGUOUS`.

**FB mode**
- Calibrate an offset for the front and rear cameras separately; each binds to the current `BoxTrack`.
- Consistent results or only one valid code -> `OK`; conflict -> `AMBIGUOUS`.

### 7.5 Binding Priority (Continuous Scan)

1. Match by `camera_id + result_ts` falling inside a time window.
2. If exactly one `BoxTrack` matches, bind directly.
3. If several match, attribute to the one with the smallest distance to the window center.
4. If still not unique -> `AMBIGUOUS`.
5. If no active window matches -> `UNBOUND_RESULT`.

### 7.6 Anti-Mix-Up Mechanism

- FIFO `BoxTrack` queue built from PE1/PE2.
- Independent return-validity window per `BoxTrack`.
- A camera result must match an active window.
- If adjacent boxes' windows overlap, alarm and treat as NG.

### 7.7 Optional Encoder

If the line has large speed fluctuation, box gaps below 30~50 mm, or widely varying box length, reserve an **encoder** interface and use conveyor pulses instead of pure time estimation.

---

## 8. Industrial PC Selection

### 8.1 Minimum Requirements

| Item | Requirement |
| --- | --- |
| CPU | RK3588 / RK3588J |
| Memory | 8 GB or more |
| Storage | 64 GB eMMC or more; SSD support recommended |
| Ethernet | >= 2 gigabit ports |
| Industrial DI | >= 2 channels |
| Industrial DO | >= 3 channels |
| DI/O level | 24 V industrial, preferably opto-isolated |
| Power input | 9~36 VDC or direct 24 VDC |
| Operating temperature | at least 0~50 °C; -20~60 °C or higher preferred |

### 8.2 FriendlyElec RK3588 Assessment

Common FriendlyElec RK3588 platforms such as NanoPC-T6 provide 3.3 V GPIO expansion, not built-in industrial 24 V DI/O. If the project requires the IPC to ship with industrial DI/O, a generic FriendlyElec board is **not recommended** as the final production IPC. If it must be used, add isolated DI/DO modules and a 24 V DC/DC converter. This increases wiring complexity and cabinet space, and weakens reliability.

### 8.3 Recommended Models

| Option | Level | Highlights | Note |
| --- | --- | --- | --- |
| **Advantech AFE-R761** | Preferred | RK3588, industrial design, 4 x DI / 4 x DO isolated, rich Ethernet/serial/CAN | Higher cost, lowest integration risk |
| **Forlinx FCU3501** | Recommended | RK3588J, 2 opto-isolated dry-contact DI, 2 isolated relay DO, 9~36 VDC | Enough for a basic version; verify DO count for OK/NG/reject/light, extend if needed |
| **JWIPC JEA-E088** | Alternative, needs confirmation | RK3588, Phoenix-terminal GPIO | Confirm with vendor that 4-in/4-out is true isolated 24 V DI/O, not TTL/GPIO |

### 8.4 Conclusion

- First choice: **Advantech AFE-R761**.
- Cost-effective: **Forlinx FCU3501**.
- FriendlyElec: only for development/validation, not final production, unless the vendor supplies a custom carrier with industrial DI/O.

---

## 9. I/O Allocation and Terminals

### 9.1 Industrial PC I/O

| Point | Signal | Direction | Purpose |
| --- | --- | --- | --- |
| DI0 | PE1_IN | Input | Box entry detection |
| DI1 | PE2_IN | Input | Position confirmation / speed |
| DI2 | REJECT_FB | Input | Reject-position feedback (optional) |
| DI3 | DOOR_EMG | Input | Door / e-stop / interlock (optional) |
| DO0 | STACK_OK | Output | Green light or OK output |
| DO1 | STACK_NG | Output | Red light / buzzer |
| DO2 | REJECT_CMD | Output | Reject action |
| DO3 | CAM_TRIG_BAK | Output | Backup hardware trigger |

### 9.2 Terminal Allocation

```
XT1 Power
  XT1-01 +24V_IN        XT1-02 0V_IN
  XT1-03 +24V_CAM_A     XT1-04 0V_CAM_A
  XT1-05 +24V_CAM_B     XT1-06 0V_CAM_B
  XT1-07 +24V_SENSOR    XT1-08 0V_SENSOR

XT2 Inputs
  XT2-01 PE1_SIG        XT2-02 PE1_COM
  XT2-03 PE2_SIG        XT2-04 PE2_COM
  XT2-05 REJECT_FB      XT2-06 REJECT_COM

XT3 Outputs
  XT3-01 OK_OUT         XT3-02 OK_COM
  XT3-03 NG_OUT         XT3-04 NG_COM
  XT3-05 REJECT_OUT     XT3-06 REJECT_COM
  XT3-07 CAM_TRIG_OUT   XT3-08 CAM_TRIG_COM
```

### 9.3 Alarms and Interlocks

- Alarm after N consecutive boxes with no read.
- Camera offline: inhibit continued automatic running.
- Alarm if a photoelectric state is stuck on/off.
- Alarm on reject-execution failure.
- Two policies: "alarm only, line keeps running" and "alarm and stop line".

---

## 10. Mechanical Design and BOM

### 10.1 Gantry

Aluminum profile or welded square tube, forming a standard scanning station:

- Adjustable crossbeam height for different box heights.
- Left-right and front-rear camera brackets share a platform with reserved holes.
- Photoelectric brackets adjustable back and forth.
- Camera brackets have an angle scale for field calibration and restoration.
- Cables run in drag chains or metal ducts.

### 10.2 Adjustment Axes per Camera Bracket

X (along conveying), Y (lateral), Z (height), Yaw (horizontal angle to flow), Pitch.

### 10.3 Structural Notes

- Keep at least **150 mm** of unobstructed stable zone before and after the read area.
- Do not mount camera brackets on thin, vibrating conveyor plates.
- For highly reflective boxes, reserve space for polarizers and light hoods.
- In dusty environments, reserve quick-clean access for the lens cover.

### 10.4 Initial BOM

| # | Item | Spec / suggestion | Qty | Note |
| --- | --- | --- | --- | --- |
| 1 | Smart scanning camera | OPT-IDD1-M050G-L08 | 2 | LR or FB, two enabled |
| 2 | Camera bracket | Custom adjustable | 2 | With angle adjustment |
| 3 | Mechanical gantry | Aluminum profile / welded | 1 | Compatible with both modes |
| 4 | Photoelectric sensor | 24 V through-beam | 2 | PE1, PE2 |
| 5 | Sensor bracket | Custom | 2 | Position adjustable |
| 6 | RK3588 IPC | AFE-R761 / FCU3501 / equivalent | 1 | Built-in industrial DI/O |
| 7 | Industrial switch | 5-port gigabit unmanaged | 1 | At least 3 gigabit ports available |
| 8 | 24 VDC supply | 24 V / 10 A or more | 1 | Sized with margin |
| 9 | Power terminal block | 24 V distribution | 1 set | With fuse, terminals |
| 10 | Tri-color light + buzzer | 24 V industrial | 1 | Run / alarm indication |
| 11 | Electrical cabinet | Wall or floor mount | 1 | Cooling, dust protection |
| 12 | Network cable | Industrial flexible | as needed | Camera to switch |
| 13 | I/O cable | Shielded multicore | as needed | Sensors, DO, reserve |
| 14 | Camera M12 cable | OEM or compatible | 2 sets | Power / I/O / Ethernet |
| 15 | Relay module | 24 V interposing relay | as needed | Drive buzzer/valve (optional) |
| 16 | Reject mechanism | Cylinder / arm / pusher | 1 set | If NG reject is needed |
| 17 | Reject-position sensor | 24 V industrial | 1 | Optional |
| 18 | Encoder | Incremental | 1 | Reserve for high speed / small gaps |

### 10.5 Power Estimate

- Cameras: 30 W x 2 = 60 W
- IPC: 15~40 W
- Sensors, switch, light, margin: 20~40 W

Use at least 120 W (24 V / 5 A or more); choose **24 V / 10 A** if reject valves, heaters, fill lights, or more peripherals are added.

---

## 11. Python Host Software Design

### 11.1 Goals and Boundaries

Responsibilities:

- Manage connections, heartbeat, and scan sessions of the two cameras.
- Acquire dual-photoelectric DI states and build box tracks.
- Control the cameras' continuous scan session while active boxes exist.
- Bind camera results to box tracks correctly.
- Output OK / NG / AMBIGUOUS / FAULT.
- Provide debugging, parameter configuration, and alarm/log UI.
- Store run records, alarm records, and config snapshots.
- Reserve PLC / MES / WMS integration.

Out of scope: image / decoding algorithms, conveyor speed closed-loop control, mechanical guiding, and electrical safety interlock.

### 11.2 Architecture Choice

| Option | Pros | Cons |
| --- | --- | --- |
| **A: PySide6 desktop app + asyncio tasks** | Intuitive on-site debugging, easy parameter changes, one codebase | Must coordinate UI thread and background tasks |
| B: Background service + Web UI | Strong remote operation | Higher dev cost; inconvenient without network |
| C: Pure daemon + config file | Simplest, lowest footprint | Hard to debug; high maintenance cost for non-developers |

**Recommended: Option A**, because the deployment is a single RK3588 IPC where on-site maintenance and calibration UI have priority.

### 11.3 Runtime Environment

- Hardware: RK3588 / RK3588J, >= 8 GB RAM, >= 64 GB storage, >= 2 GbE, >= 2 DI, >= 3 DO.
- OS: Ubuntu 22.04 ARM64 or equivalent Linux; Python 3.10+.
- GUI: PySide6; async: asyncio; DB: SQLite; config: YAML; packaging: PyInstaller or Nuitka; service: systemd.
- Logging: `logging` + rotating files.

Network constraints:

- ETH0 (device network) carries camera traffic only; ETH1 (dispatch network) carries reporting, parameter delivery, remote maintenance.
- Camera traffic must not share a network with the dispatch host.
- Software must allow separate bind addresses for ETH0 and ETH1; device discovery, heartbeat, and result reception default to ETH0.

### 11.4 Layered Architecture

```
UI layer        Dashboard / Device / Config / Alarm / History
Business layer  RuntimeService, TrackManager, TriggerScheduler,
                ScanSessionController, ResultBinder, DecisionEngine, AlarmService
Device layer    DiDoService, CameraClient A/B, PlcClient / MesClient / SchedulerClient
Storage layer   SQLite, CSV/JSON export, NG-image or raw-payload archive
```

Data flow:

- `PE1/PE2`, camera results, and user operations all enter the **EventBus** first.
- `RuntimeService` consumes main business events serially.
- `TrackManager`, `TriggerScheduler`, `ScanSessionController` jointly maintain box time windows and the continuous scan session.
- `ResultBinder` and `DecisionEngine` handle code ownership and final decision.
- `Repository`, `SchedulerClient`, `DiDoService` handle persistence, upstream reporting, and field output.

Design principles:

- UI decoupled from business; UI never operates low-level devices directly.
- `BoxTrack` is the core domain model.
- All device events go through one event channel before reaching business logic.
- Cameras, DI/O, and reporting use the adapter pattern.
- Config is hot-loadable, but changes to key runtime parameters are controlled.

### 11.5 Directory Structure

```text
vision_gate_app/
├─ app/            main.py, bootstrap.py, lifecycle.py
├─ config/         default.yaml, schema.py, manager.py
├─ domain/         models, enums, events, track_manager, scheduler,
│                  scan_session, binder, decision_engine, alarm_service
├─ devices/
│  ├─ dio/         base.py, rk3588_dio.py, simulator.py
│  ├─ camera/      base.py, opt_client.py, simulator.py
│  └─ report/      plc_client.py, mes_client.py, noop_client.py
├─ infra/          db/ (models, repository, migrations), logging/, utils/
├─ services/       runtime_service, health_service, archive_service, config_service
├─ ui/             main_window.py, viewmodels/, pages/ (dashboard, device,
│                  config, alarm, history), widgets/
├─ tests/          unit/, integration/, e2e/
└─ scripts/        run_local.py, seed_demo_data.py
```

### 11.6 MVP (Minimum Viable Product)

The MVP solves only the main chain: read config -> init DI/O and two cameras -> event queue -> create `BoxTrack` from `PE1/PE2` -> open per-box window -> control continuous scan session -> bind results -> output OK/NG -> save to SQLite -> show live status in one main window.

Not required in the MVP: full history page, alarm acknowledgement flow, full PLC/MES/WMS stack, hot parameter update, report export.

Minimal skeleton:

```text
vision_gate_mvp/
├─ app/        main.py, bootstrap.py
├─ config/     default.yaml
├─ domain/     enums, models, track_manager, scheduler, scan_session,
│              binder, decision_engine
├─ devices/    dio.py, camera.py, scheduler_client.py
├─ services/   runtime_service.py, event_bus.py, repository.py
├─ ui/         main_window.py
├─ data/
└─ logs/
```

Startup chain:

```
main.py -> load default.yaml -> bootstrap creates EventBus
 -> init DiDoService / CameraClient / SchedulerClient / Repository
 -> init TrackManager / TriggerScheduler / ScanSessionController / ResultBinder / DecisionEngine
 -> init MainWindow -> start RuntimeService
 -> dio_watch_loop / camera_fetch_loop / event_loop -> UI main loop
```

MVP main window shows: camera A/B online state, PE1/PE2 live state, scan-session state, active `BoxTrack` count, latest 10 results, OK/NG counters, current alarm.

Skeleton code (`app/main.py`):

```python
import asyncio
from PySide6.QtWidgets import QApplication
from qasync import QEventLoop

from app.bootstrap import build_app


def main() -> None:
    qt_app = QApplication([])
    loop = QEventLoop(qt_app)
    asyncio.set_event_loop(loop)

    container = build_app()
    container.window.show()

    with loop:
        loop.run_until_complete(container.runtime.start())
        loop.run_forever()


if __name__ == "__main__":
    main()
```

`app/bootstrap.py`:

```python
from dataclasses import dataclass

from devices.camera import CameraService
from devices.photoelectric import DiDoService
from devices.scheduler_client import SchedulerClient
from services.event_bus import EventBus
from services.repository import Repository
from services.runtime_service import RuntimeService
from ui.main_window import MainWindow


@dataclass
class AppContainer:
    runtime: RuntimeService
    window: MainWindow


def build_app() -> AppContainer:
    event_bus = EventBus()
    dio = DiDoService(event_bus)
    camera = CameraService(event_bus)
    scheduler_client = SchedulerClient()
    repo = Repository("data/app.db")
    runtime = RuntimeService(event_bus, dio, camera, scheduler_client, repo)
    window = MainWindow(runtime)
    return AppContainer(runtime=runtime, window=window)
```

`services/runtime_service.py`:

```python
class RuntimeService:
    def __init__(self, event_bus, dio, camera, scheduler_client, repo):
        self.event_bus = event_bus
        self.dio = dio
        self.camera = camera
        self.scheduler_client = scheduler_client
        self.repo = repo

    async def start(self) -> None:
        await self.dio.start()
        await self.camera.start()
        asyncio.create_task(self.event_loop())

    async def event_loop(self) -> None:
        while True:
            event = await self.event_bus.get()
            await self.handle_event(event)
```

MVP dependencies: `PySide6`, `qasync`, `PyYAML`, `pydantic` (or dataclasses + manual validation). Use the built-in `sqlite3`; no ORM required.

Upgrade path: `scheduler_client.py` grows into a full dispatch-protocol adapter; `main_window.py` splits into Dashboard/Device/Config/Alarm/History pages; `repository.py` splits into multiple repositories with migrations; `dio.py`/`camera.py` split into `base + implementation + simulator`.

### 11.7 Domain Models

```python
class RunMode(Enum):
    LR = "LR"
    FB = "FB"

class TrackStatus(Enum):
    CREATED = "CREATED"
    TRACKING = "TRACKING"
    WINDOW_OPEN = "WINDOW_OPEN"
    WAITING_RESULT = "WAITING_RESULT"
    FINALIZED = "FINALIZED"
    EXPIRED = "EXPIRED"

class DecisionStatus(Enum):
    OK = "OK"
    NO_READ = "NO_READ"
    AMBIGUOUS = "AMBIGUOUS"
    TIMEOUT = "TIMEOUT"
    FAULT = "FAULT"

class DeviceStatus(Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"
```

```python
@dataclass
class CameraTriggerPlan:
    camera_id: str
    trigger_ts: float
    trigger_offset_mm: float
    trigger_sent: bool = False


@dataclass
class CameraResult:
    camera_id: str
    code: str | None
    raw_payload: dict
    result_ts: float
    success: bool


@dataclass
class BoxTrack:
    track_id: str
    mode: RunMode
    created_ts: float
    pe1_on_ts: float | None = None
    pe1_off_ts: float | None = None
    pe2_on_ts: float | None = None
    pe2_off_ts: float | None = None
    speed_mm_s: float | None = None
    length_mm: float | None = None
    scan_window_start_ts: float | None = None
    scan_window_end_ts: float | None = None
    first_ok_ts: float | None = None
    scan_close_reason: str | None = None
    status: TrackStatus = TrackStatus.CREATED
    trigger_plans: list[CameraTriggerPlan] = field(default_factory=list)
    camera_results: list[CameraResult] = field(default_factory=list)
    final_code: str | None = None
    final_status: DecisionStatus | None = None
    alarm_codes: list[str] = field(default_factory=list)


@dataclass
class DeviceHealth:
    device_id: str
    device_type: str
    status: DeviceStatus
    last_heartbeat_ts: float | None
    message: str = ""
```

### 11.8 Event Model

```python
class EventType(Enum):
    PE_RISE = "PE_RISE"
    PE_FALL = "PE_FALL"
    CAMERA_RESULT = "CAMERA_RESULT"
    CAMERA_HEARTBEAT = "CAMERA_HEARTBEAT"
    TIMER_TRIGGER = "TIMER_TRIGGER"
    TRACK_TIMEOUT = "TRACK_TIMEOUT"
    DEVICE_FAULT = "DEVICE_FAULT"
    OPERATOR_CMD = "OPERATOR_CMD"

@dataclass
class AppEvent:
    event_id: str
    event_type: EventType
    source: str
    ts: float
    payload: dict
```

Use a lightweight in-memory event bus (no complex message middleware): backed by `asyncio.Queue`; device services push into one queue; the business coordinator consumes key events serially; the UI subscribes via signals/slots or a read-only state cache; the continuous-scan session state is maintained only by the domain layer.

### 11.9 Core Modules

**AppController** – startup/shutdown, init config/DB/logging/devices, start background tasks.
`startup()`, `shutdown()`, `restart_runtime()`.

**RuntimeService** – coordinates DI, cameras, scheduler, binder, decision engine; consumes events; advances `BoxTrack` lifecycle.
`start()`, `stop()`, `handle_event(event)`.

**TrackManager** – create/query/update/release `BoxTrack`s; maintain the active queue; recycle timed-out tracks.

```python
class TrackManager:
    def create_track(self, ts: float, mode: RunMode) -> BoxTrack: ...
    def get_active_tracks(self) -> list[BoxTrack]: ...
    def match_track_for_pe2(self, ts: float) -> BoxTrack: ...
    def match_last_open_track(self) -> BoxTrack | None: ...
    def finalize_track(self, track_id: str, status: DecisionStatus) -> BoxTrack: ...
    def cleanup_expired(self, now_ts: float) -> list[BoxTrack]: ...
```

Rules: active tracks are kept in ascending creation order; `PE2` matches the earliest entered, not-yet-triggered track; on overlap ambiguity, alarm immediately and mark `TRACK_OVERLAP`.

**TriggerScheduler** – computes each box's read window, manages window open/close, asks `ScanSessionController` to start/stop.

```python
class TriggerScheduler:
    def open_scan_window(self, track: BoxTrack, mode: RunMode) -> BoxTrack: ...
    def prepare_window_close(self, track: BoxTrack, pe1_fall_ts: float) -> BoxTrack: ...
    def close_expired_windows(self, now_ts: float) -> list[BoxTrack]: ...
```

Window start is normally `pe2_on_ts`. Window end is jointly constrained by: PE1 fall + `tail_delay_ms`; predicted leave-zone time; `max_track_window_ms`; first valid code + `track_hold_after_ok_ms`. If speed is missing/abnormal, fall back to `line_speed_mm_s`.

**ScanSessionController** – starts the session when active tracks go 0 -> 1; stops it when active tracks return to 0 and `idle_off_delay_ms` has passed.

```python
class ScanSessionController:
    async def ensure_running(self) -> None: ...
    async def stop_if_idle(self) -> None: ...
    def is_running(self) -> bool: ...
```

Requirements: do not start/stop the camera for each box in continuous flow; session state is visible to the UI; if the camera only supports single trigger, degrade to "continuous triggering inside the window".

**CameraClient** – one TCP connection per camera; start/stop continuous scan; receive results; report heartbeat.

```python
class BaseCameraClient(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def start_scan_session(self) -> None: ...
    async def stop_scan_session(self) -> None: ...
    async def trigger_once(self, track_id: str) -> None: ...
    async def fetch_loop(self) -> None: ...
    def get_health(self) -> DeviceHealth: ...

class OptCameraClient(BaseCameraClient):
    def parse_payload(self, payload: bytes) -> CameraResult: ...
```

Requirements: independent client/connection state per camera; every result carries `camera_id`; keep the original packet in `raw_payload`; prefer the camera's native continuous-read mode; otherwise trigger 3~5 times per active window until the first valid code or window end.

**DiDoService** – poll/subscribe DI changes, write DO, hide IPC driver differences.

```python
class BaseDiDoService(ABC):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def read_di(self, channel: int) -> bool: ...
    async def write_do(self, channel: int, value: bool) -> None: ...
    async def watch_inputs(self) -> None: ...
```

Requirements: debounce input edges; support pulse outputs; DI/DO mapping is config-driven.

**ResultBinder** – binds results to the right `BoxTrack`, checks conflicts and timeouts.

```python
class ResultBinder:
    def bind(self, result: CameraResult, active_tracks: list[BoxTrack]) -> BoxTrack | None: ...
    def resolve_final_code(self, track: BoxTrack) -> tuple[str | None, DecisionStatus]: ...
```

**DecisionEngine** – `evaluate(track) -> DecisionStatus`:

| Condition | Result |
| --- | --- |
| Two cameras consistent | `OK` |
| Only one camera succeeded | `OK` |
| Both failed | `NO_READ` |
| Results conflict | `AMBIGUOUS` |
| No result returned in time | `TIMEOUT` |
| Device fault prevents decision | `FAULT` |

**AlarmService** – unified alarm codes, levels, and recovery; broadcasts to UI, DB, and external interfaces.
`raise_alarm(code, message, level="ERROR")`, `clear_alarm(code)`, `list_active_alarms()`.

### 11.10 State Machines

System: `BOOT -> LOAD_CONFIG -> INIT_DEVICES -> SELF_CHECK -> READY -> RUNNING -> FAULT -> STOPPING`

Per-box lifecycle: `CREATED -> TRACKING -> WINDOW_OPEN -> WAITING_RESULT -> FINALIZED (OK | NO_READ | AMBIGUOUS | TIMEOUT | FAULT)`

Standard processing sequence:

1. PE1 rising edge
2. `TrackManager.create_track()`
3. PE2 rising edge
4. `TrackManager.match_track_for_pe2()`
5. `TriggerScheduler.open_scan_window()`
6. `ScanSessionController.ensure_running()`
7. `CameraClient` streams results
8. `ResultBinder.bind()`
9. `DecisionEngine.evaluate()`
10. `TriggerScheduler.prepare_window_close()`
11. `DiDoService.write_do()`
12. `Repository.save_scan_record()`
13. `ScanSessionController.stop_if_idle()`
14. UI refresh

Timeouts: `trigger_timeout_ms` (session start/confirm failed), `camera_result_timeout_ms` (no result after window opens), `track_ttl_ms` (total track lifetime). On timeout: record an alarm, mark the track `TIMEOUT`, output `NG`, archive.

### 11.11 Continuous-Arrival Timing

| Stage | Field state | PE1 / PE2 | Track #1 | Track #2 | Scan session | Binding / output |
| --- | --- | --- | --- | --- | --- | --- |
| t0 | No box | idle | – | – | stopped | – |
| t1 | Box#1 reaches gate | PE1 rise | create, `CREATED -> TRACKING` | – | stopped | – |
| t2 | Box#1 at read reference | PE2 rise (Box#1) | open window | – | **start** | camera returns continuously |
| t3 | Box#2 enters | PE1 rise (Box#2) | still active | create | running | keep scanning |
| t4 | Box#2 at read reference | PE2 rise (Box#2) | may still be open | open window | running | both tracks coexist |
| t5 | First result returned | – | hit, decided | waiting | running | bind to #1, output OK/NG(T1) |
| t6 | Second result returned | – | done | hit, decided | idle-off delay if no tracks | bind to #2, output OK/NG(T2) |
| t7 | Both done | idle | released | released | stop after `idle_off_delay_ms` | session ends |

Key rules:

- Window opens on `PE2` rising edge (main read reference).
- Any active `BoxTrack` starts or holds the scan session.
- `Track #1` and `Track #2` windows may overlap in continuous flow.
- Binding uses the window containing `result_ts`, not "the nearest box".
- Single-box window close = `first_ok + hold`, `PE1 fall + tail_delay`, predicted leave time, `max_track_window_ms`.
- Session close = active tracks 0 and `idle_off_delay_ms` elapsed.

Takeaway: the scan session is a **shared resource**; the per-box time window is an **independent resource**. `ResultBinder` finds which window each shared result belongs to.

### 11.12 Concurrency Model

- Qt main thread hosts the UI; `asyncio` hosts device communication and business logic, bridged by `qasync`.
- Background tasks: `camera_a_fetch_loop`, `camera_b_fetch_loop`, `dio_watch_loop`, `runtime_event_loop`, `health_check_loop`, `archive_flush_loop`, `report_retry_loop`.
- Track-related business processing stays serial to reduce race conditions; camera receive and DI watching run in parallel.
- All shared-state changes go through `RuntimeService`; no coroutine modifies `active_tracks` directly.

### 11.13 UI Design

| Page | Content |
| --- | --- |
| **Dashboard** | Mode, camera online state, PE1/PE2 live state, speed estimate, latest results, OK/NG stats, current alarm |
| **Device** | Camera connection, scan-session state, DI/DO state, network latency, session starts / read success rate / timeouts |
| **Config** | Mode, camera IP/port, photoelectric mapping, trigger distances, window parameters, timeouts, output pulse widths |
| **History** | Scan history, filtered queries, CSV export |
| **Alarm** | Current/historical alarms, recovery time, acknowledging user |

Interaction principles: UI never touches DB or device drivers directly; it reads state via `ViewModel` or read-only services; all parameter changes go through `ConfigService`; parameters that take effect at runtime need a second confirmation.

### 11.14 Configuration (`config/default.yaml`)

```yaml
app:
  site_name: "WM QR Gate"
  debug: false

runtime:
  mode: "LR"
  line_speed_mm_s: 800
  track_ttl_ms: 1500

trigger:
  scan_mode: "continuous_window"
  sensor_distance_mm: 120
  pe2_to_cam_a_mm: 100
  pe2_to_cam_b_mm: 100
  trigger_timeout_ms: 300
  camera_result_timeout_ms: 500
  min_box_gap_mm: 50
  tail_delay_ms: 80
  idle_off_delay_ms: 150
  track_hold_after_ok_ms: 50
  max_track_window_ms: 400
  allow_single_camera_ok: true
  ambiguity_policy: ng

network:
  eth0_bind: "192.168.10.10"
  eth1_bind: "192.168.20.10"
  scheduler_host: "192.168.20.100"
  scheduler_port: 9100

camera:
  camera_a:
    id: "CAM_A"
    ip: "192.168.10.101"
    port: 3000
    enabled: true
  camera_b:
    id: "CAM_B"
    ip: "192.168.10.102"
    port: 3000
    enabled: true

dio:
  di_map:
    pe1: 0
    pe2: 1
    reject_fb: 2
  do_map:
    ok: 0
    ng: 1
    reject: 2
    cam_trigger_backup: 3
  pulse_ms:
    ok: 80
    ng: 120
    reject: 200

storage:
  db_path: "./data/app.db"
  log_dir: "./logs"
  export_dir: "./exports"

report:
  plc_enabled: false
  mes_enabled: false
```

Effect rules: network addresses and channel mapping need a runtime restart; log level hot-switches; mode switching is allowed only in standby; window parameters can be tuned online but should be saved only when idle.

Parameters to make configurable/calibratable: PE1–PE2 spacing, PE2 -> camera reference distance, camera angle, minimum allowed box gap, line-speed compensation factor, timeouts.

### 11.15 Database

Tables: `scan_record`, `camera_result_record`, `alarm_record`, `config_snapshot`, `system_event_log`.

`scan_record`

| Field | Type | Description |
| --- | --- | --- |
| id | INTEGER | Primary key |
| track_id | TEXT | Track ID |
| mode | TEXT | LR / FB |
| code_value | TEXT | Final code |
| status | TEXT | OK / NO_READ / AMBIGUOUS / TIMEOUT |
| pe1_ts / pe2_ts | TEXT | PE timestamps |
| speed_mm_s | REAL | Estimated speed |
| result_json | TEXT | Raw result |
| created_at | TEXT | Creation time |

`camera_result_record`: `id`, `track_id`, `camera_id`, `code_value`, `success` (0/1), `result_ts`, `raw_payload`.

`alarm_record`: `id`, `alarm_code`, `alarm_level` (INFO/WARN/ERROR), `message`, `start_ts`, `end_ts`, `ack_user`.

`config_snapshot`: `id`, `version`, `config_json`, `updated_at`.

`system_event_log`: `id`, `event_type`, `source`, `payload`, `created_at`.

```python
class ScanRepository:
    async def save_scan_record(self, track: BoxTrack) -> None: ...
    async def save_camera_result(self, result: CameraResult, track_id: str) -> None: ...
    async def save_alarm(self, alarm: dict) -> None: ...
    async def save_event(self, event: AppEvent) -> None: ...
    async def query_scan_records(self, filters: dict) -> list[dict]: ...
```

### 11.16 Logging

- Files: `app.log` (main), `runtime.log` (business), `device.log` (device comms), `alarm.log` (alarms).
- Fields: timestamp, level, module, `track_id`, `camera_id`, message.
- Levels: INFO (state change, online/offline, normal read), WARN (single timeout, single no-read, minor jitter), ERROR (device offline, consecutive no-read, I/O fault).

```text
2026-03-18 15:12:01.233 | INFO  | runtime | track_id=T20260318151201231 | created track
2026-03-18 15:12:01.411 | INFO  | camera  | camera_id=CAM_A | trigger sent
2026-03-18 15:12:01.563 | WARN  | binder  | track_id=T20260318151201231 | single camera result only
```

### 11.17 External Interfaces

**Interface split** – dispatch host: device online, result receiving, parameter sync. PLC: on-site action interlock. MES/WMS: business data archiving and traceability. This avoids loading every upper-level interaction onto one protocol.

**PLC minimum signals**

| Signal | Type | Direction | Description |
| --- | --- | --- | --- |
| SYSTEM_READY | DO | to PLC | System ready |
| SCAN_OK | DO | to PLC | Current box scanned OK |
| SCAN_NG | DO | to PLC | Current box scan failed |
| REJECT_ACT | DO | to PLC | Request reject |
| LINE_RUN | DI | from PLC | Line running |
| RESET_CMD | DI | from PLC | Fault reset |

```python
class PlcClient(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def notify_ready(self, value: bool) -> None: ...
    async def notify_scan_result(self, result: DecisionStatus, track_id: str) -> None: ...
    async def notify_reject(self, track_id: str) -> None: ...
```

**Dispatch host (ETH1)** – the MVP implements result reporting and heartbeat only; parameter delivery, task orchestration, and remote control come in phase 2.

```python
class SchedulerClient(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def report_result(self, payload: dict) -> bool: ...
    async def report_heartbeat(self, payload: dict) -> bool: ...
```

Minimal report fields: `device_id`, `track_id`, `mode`, `final_code`, `status`, `created_at`.

**MES**

```python
class MesClient(ABC):
    async def report_scan_record(self, payload: dict) -> bool: ...
```

```json
{
  "device_id": "VG-01",
  "line_id": "LINE-01",
  "track_id": "T20260318151201231",
  "mode": "LR",
  "final_code": "6901234567890",
  "status": "OK",
  "process_time_ms": 242,
  "created_at": "2026-03-18T15:12:01.233"
}
```

Suggested upload fields: device ID, line ID, shift, timestamp, `track_id`, scan result, final code, mode (LR/FB), processing time, alarm code.

Protocol suggestions: PLC via Modbus TCP or dry contacts; MES/WMS via HTTP API or MQTT; follow the customer's protocol standard if one exists.

### 11.18 Exception Handling

| Category | Examples | Strategy |
| --- | --- | --- |
| Device | Camera offline, DI driver error | Alarm; stop if necessary |
| Business | Box mix-up, conflicting codes, timeout | Judge NG, store, notify |
| Configuration | Invalid IP, channel conflict | Block startup |
| Storage | DB write failure | Retry and alarm |
| Network | PLC/MES timeout | Retry; cache for later re-report |

Recovery: camera offline -> auto-reconnect, enter fault state beyond a threshold; MES report failure -> local cache + background retry; occasional DB write failure -> short retry; config parse failure -> fall back to the last valid config.

### 11.19 Start, Stop, Recovery

Startup: `read config -> init logging -> init DB -> init DI/O -> init cameras -> create event queue -> start background tasks -> self-check done -> READY`

Stop: `stop accepting events -> end background tasks -> flush pending DB writes -> close device connections -> save run snapshot -> exit`

Recovery: keep the latest active-track snapshot, active alarms, and latest config version. After restart, old tracks do **not** take part in decisions; only alarm history and config are restored.

### 11.20 Key Pseudocode

```python
async def runtime_loop():
    while running:
        event = await event_queue.get()
        await runtime_service.handle_event(event)


async def handle_pe_rise(sensor_name: str, ts: float):
    if sensor_name == "PE1":
        track = track_manager.create_track(ts, mode=current_mode)
        publish_ui_track_created(track)
        return

    if sensor_name == "PE2":
        track = track_manager.match_track_for_pe2(ts)
        track.pe2_on_ts = ts
        track.speed_mm_s = speed_estimator.calc(track.pe1_on_ts, ts)
        scheduler.open_scan_window(track, current_mode)
        await scan_session.ensure_running()


async def handle_pe_fall(sensor_name: str, ts: float):
    if sensor_name == "PE1":
        track = track_manager.match_last_open_track()
        if track is not None:
            track.pe1_off_ts = ts
            scheduler.prepare_window_close(track, ts)


async def handle_camera_result(result: CameraResult):
    track = binder.bind(result, track_manager.get_active_tracks())
    if track is None:
        alarm_service.raise_alarm("UNBOUND_RESULT", "camera result cannot bind")
        return

    track.camera_results.append(result)
    if result.success and track.first_ok_ts is None:
        track.first_ok_ts = result.result_ts
    repo.save_camera_result(result, track.track_id)

    final_code, final_status = binder.resolve_final_code(track)
    if final_status is not None:
        track.final_code = final_code
        track.final_status = decision_engine.evaluate(track)
        await output_result(track)


async def output_result(track: BoxTrack):
    if track.final_status == DecisionStatus.OK:
        await dio.write_pulse("ok")
    else:
        await dio.write_pulse("ng")

    if track.final_status in {DecisionStatus.NO_READ, DecisionStatus.AMBIGUOUS, DecisionStatus.TIMEOUT}:
        await dio.write_pulse("reject")

    await repo.save_scan_record(track)
    await reporter.report_track(track)
    track_manager.finalize_track(track.track_id, track.final_status)
    await scan_session.stop_if_idle()
```

### 11.21 Deployment

```text
/opt/vision-gate/
├─ app/  config/  data/  logs/  exports/  run.sh
```

- Statically configure both NICs and record them in the deployment manual (ETH0 device network, ETH1 dispatch network).
- systemd service `vision-gate.service`: start on boot, auto-restart on crash, log to file and journal.
- Release package: executable, default config, release notes, DB init script, device commissioning manual.

### 11.22 Development Order

1. Build the MVP skeleton.
2. Finish `domain` and `devices` layers in a no-UI version.
3. Build the **DI simulator** and **camera simulator**.
4. Close the full loop: track, window, continuous scan, bind, decide, store.
5. Add the minimal dispatch-host interface.
6. Add the full UI pages.
7. Finally add PLC/MES and export.

Version constraints: dual-camera mode only; single-lane single-column boxes only; camera results returned over the network; no image saving by default.

Strongly recommended capabilities: simulation mode, parameter import/export, one-click log packaging, raw camera-packet archive, quick search of the latest 100 results.

Deliverables: source code, MVP skeleton project, config templates, SQLite init script, systemd service file, operation manual, commissioning notes, change log.

---

## 12. Testing, Acceptance, Commissioning

### 12.1 Software Tests

- **Unit**: `TrackManager`, `TriggerScheduler`, `ScanSessionController`, `ResultBinder`, `DecisionEngine`, `ConfigManager`.
- **Integration**: DI event -> track -> window -> continuous scan -> result -> decision; continuous flow with session hold and multi-box windows; single-camera success; two-camera conflict; no-result timeout; camera offline recovery; dispatch heartbeat and result report.
- **Simulation**: simulate DI edges, camera code returns, speed fluctuation, too-small box gaps.
- **End-to-end (before site commissioning, via replay scripts)**: normal continuous flow, code on one side only, same code on both sides, different codes on both sides, delayed code return, device dropout and recovery.

### 12.2 FAT

| # | Test | Criterion |
| --- | --- | --- |
| 1 | Power-up | Software auto-starts, self-check clean |
| 2 | Camera offline alarm | Fault shown within 5 s |
| 3 | PE1/PE2 trigger | DI state matches physical action |
| 4 | LR scanning | Continuous success rate meets target |
| 5 | FB scanning | Continuous success rate meets target |
| 6 | One-side read | Correctly judged OK |
| 7 | Conflicting codes | Judged AMBIGUOUS/NG |
| 8 | Continuous flow | No mix-up, no lost box |
| 9 | Output linkage | OK/NG/reject signals correct |
| 10 | Log traceability | Complete records queryable |

### 12.3 SAT

| # | Test | Criterion |
| --- | --- | --- |
| 1 | Site network | Cameras, IPC, PLC all online |
| 2 | Site lighting | Stable reading under site light |
| 3 | Box sizes | Representative box types processed |
| 4 | Dirt / wrinkles | Success rate within target |
| 5 | Minimum box gap | No mix-up |
| 6 | Reject mechanism | NG boxes rejected correctly |
| 7 | Fault recovery | System continues after reset |

### 12.4 Acceptance Targets (placeholders until site-sample statistics exist)

- Normal sample read success rate >= 99.5%
- Continuous stability >= 8 hours
- Code-to-box binding accuracy 100%
- Self-check time <= 30 s
- Also verify 500+ consecutive boxes at 0.8 m/s, different box types / materials / slight dirt, small gaps, and network cut / camera power loss / photoelectric faults.

Final targets depend on real sample tests and commercial commitments.

### 12.5 Implementation Steps

1. Install mechanical gantry and cabling.
2. Install photoelectric sensors and fix PE1/PE2 spacing.
3. Install cameras; calibrate best working distance and angle.
4. Connect IPC DI/O and network.
5. Configure camera parameters, protocol, and host parameters.
6. Dry-run with representative box samples.
7. Online commissioning with continuous flow.
8. Verify OK/NG/reject linkage.
9. Export and archive parameter backups.

---

## 13. Assumptions and Open Items

### 13.1 Design Assumptions

- Only a single column of boxes passes the line at any moment.
- Box attitude is stable after guiding and does not rotate inside the gate.
- LR and FB modes do not switch frequently within a shift.
- The camera can decode independently; the host mainly handles timing and binding.
- Nominal line speed 0.8 m/s with small fluctuation.
- Photoelectric positions can be mechanically fine-tuned.

If these fail, adjust: sensor layout, binding strategy, camera lens / working distance, IPC I/O count, and whether to add an encoder and buffer section.

### 13.2 Items to Confirm

- Max / min box dimensions
- Minimum box gap
- Code size, position tolerance, and print quality
- Whether the code position is fixed or labels are randomly placed
- Box material and reflectivity
- Strong ambient light, direct sunlight, or flickering overhead lights on site
- Need to integrate with PLC / MES / WMS
- Need to store NG images or raw camera packets
- Need for a reject mechanism and reject-position confirmation sensor
- Need to switch between LR and FB modes on the same line

Once these are filled in, the document can be refined into a detailed BOM, electrical schematics, software interface protocol, and FAT/SAT test cases.

---

## 14. Conclusion

With the known conditions, **dual photoelectric sensors + 2 smart code-reading cameras + RK3588 industrial PC + Python host software** is a viable solution. The key risks are not compute power but:

1. Whether the code area really falls inside the camera FOV.
2. Whether the code-to-box association window is reliable.
3. Whether the IPC truly has industrial DI/O rather than plain GPIO.

Without an encoder the solution can still go live, but PE1/PE2 spacing, PE2 -> camera reference distance, camera angles, and the minimum allowed box gap should all be configurable and calibratable. In software, first get the main chain right: **event flow -> track -> trigger -> bind -> decide -> output -> store**; PLC/MES, export, and enhanced alarms follow easily afterward.

---

## 15. References

- Camera datasheet: `扫码相机规格书-OPT-IDD1-M050G-L08-xxx-ESD.pdf`
- FriendlyElec NanoPC-T6 Wiki: https://wiki.friendlyelec.com/wiki/index.php/NanoPC-T6
- FriendlyElec product page: https://www.friendlyelec.com/index.php?route=product/product&product_id=290
- Advantech AFE-R761: https://www.advantech.com/en-us/products/880a61e5-3fed-41f3-bf53-8be2410c0f19/afe-r761/mod_be4c17f0-88c3-406d-911e-bf9ddedf3b37
- Advantech AFE-R761 datasheet: https://www.proxis.ua/files/documents/AFE-R761_DS%28100725%2920251007101445.pdf
- Forlinx FCU3501: https://www.forlinx.net/product/166.html
- JWIPC JEA-E088: https://www.jwipc.com/products-show/jea-e088.html
- JWIPC industrial edge controller brochure: https://www.jwipc.com/upload/download/2024-11/673337b5e0667.pdf
