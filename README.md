<img width="1050" height="650" alt="image" src="https://github.com/user-attachments/assets/ea308767-1ae9-429a-9091-cd2f47a28ecf" />

# BTrackS Balance Board — Python Logger & Visualizer

Standalone Python tools for collecting and visualizing center-of-pressure
(CoP) data from a [BTrackS](https://www.balancetrackingsystems.com/) balance
board over USB. No Godot, LabVIEW, or proprietary software required to run.

## What's included

| File | Purpose |
| --- | --- |
| `btracks_logger.py` | Reads CoP from the BTrackS board at 25 Hz, logs to CSV, optionally broadcasts a UDP feed to the visualizer. |
| `btracks_viz.py`    | Live dashboard (matplotlib) showing CoP dot, per-corner forces, time-series, and total weight. Can run alongside the logger or read the board directly. |
| `logs/`             | Output folder for CSV files written by the logger. |

## Software requirements

| Component | Tested version | Where to get it |
| --- | --- | --- |
| Windows 10 / 11 (64-bit) | — | — |
| Phidget22 driver | 1.22 or later (x64) | [phidgets.com/downloads/phidget22](https://www.phidgets.com/downloads/phidget22/libraries/windows/) |
| Python | 3.10 or later | [python.org](https://www.python.org/downloads/) |
| Python packages | see below | pip |

> Both scripts use the **Phidget22** API.
> Phidget21 driver to run this project.

## Installation

### 1. Install the Phidget22 driver

Download `Phidget22-x64_*.exe` from the link above and run the installer.
After it finishes, open the **Phidget Control Panel** and plug in the board.
Confirm a "PhidgetBridge 4-Input (1046_0)" device shows up with 4 inputs
and a serial number. If it doesn't, the driver install didn't succeed.

### 2. Install Python

Install Python 3.10 or later from python.org. During install, check
**"Add Python to PATH"**.

Open a terminal (Command Prompt or PowerShell) and verify:

```
python --version
```

You should see `Python 3.10.x` or newer.

### 3. Install Python packages

In the same terminal, from the repo folder:

```
pip install -r requirements.txt
```

Or install them directly:

```
pip install Phidget22 matplotlib
```

- `Phidget22` — Phidget22 Python bindings (used by both scripts)
- `matplotlib` — for the live dashboard

## Running

### Important: close BTrackS Assess before running

Only one program at a time can talk to the Phidget Bridge. Close the
official BTrackS Assess software (and any other program using the board)
before starting the logger or visualizer in direct mode.

### Log to CSV and visualize at the same time (recommended)

Open **two** terminals.

**Terminal 1** — start the logger:

```
python btracks_logger.py
```

The logger will:
1. Connect to the Phidget Bridge
2. Capture a 2-second empty-board baseline (keep the board empty)
3. Start writing a CSV file to `logs/` and broadcasting UDP on port 9001

**Terminal 2** — start the visualizer:

```
python btracks_viz.py --udp 9001
```

The dashboard window will open. Step on the board and watch the CoP dot,
force bars, and time-series traces.

Press **Ctrl+C** in the logger terminal (or close the visualizer window)
to stop.

## Common command-line options

`btracks_logger.py`:

| Flag | Default | What it does |
| --- | --- | --- |
| `--log-prefix PREFIX` | (empty) | Prepend to CSV filename, e.g. `P01_Baseline` |
| `--log-dir DIR` | `logs/` | Where to save CSV files |
| `--no-log` | off | Don't write a CSV file |
| `--no-udp` | off | Don't broadcast UDP |
| `--skip-baseline` | off | Skip the empty-board zero capture at startup |
| `--rate HZ` | 25 | Sample rate in Hz (BTrackS max is 25) |
| `--print-every N` | 25 | Print CoP/weight every N samples (25 = once per second) |
| `--viz-host HOST` | 127.0.0.1 | UDP host for the visualizer |
| `--viz-port PORT` | 9001 | UDP port for the visualizer |

`btracks_viz.py`:

| Flag | Default | What it does |
| --- | --- | --- |
| `--udp PORT` | (off) | Listen for UDP from the logger on this port. Without this flag, the visualizer reads the Phidget directly. |
| `--rate HZ` | 25 | Sample rate for direct mode |

## CSV output format

Each row in `logs/<prefix>_cop_<timestamp>.csv` has:

| Column | Description |
| --- | --- |
| `epoch` | Unix timestamp in seconds (with milliseconds) |
| `datetime` | Human-readable timestamp `YYYY-MM-DD-HH-MM-SS-fff` |
| `raw_FR / raw_BR / raw_BL / raw_FL` | Raw voltage ratio (mV/V) per cell |
| `force_FR_lbs / force_BR_lbs / force_BL_lbs / force_FL_lbs` | Per-cell force in lbs (after baseline subtraction and calibration) |
| `cop_x_cm` | Center of pressure, mediolateral (x), in cm. Positive = right. |
| `cop_y_cm` | Center of pressure, anteroposterior (y), in cm. Positive = forward (toward the handle). |
| `weight_lbs` | Total weight on the board in lbs |

Sample rate: 25 Hz. The board dimensions assumed in the
scripts are 40 cm wide × 60 cm long — adjust `BOARD_WIDTH` and
`BOARD_LENGTH` in both files if your physical board differs.

## Coordinate frame

The default coordinate frame assumes **the user faces the handle**
(handle = front of board):

```
        +Y (forward, toward handle)
            ^
   FL       |       FR
            |
 -X <----- + -----> +X  (right)
            |
   BL       |       BR
            v
        -Y (backward)
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Nipa Anjum.

## Credits

Built on top of the [Phidget22 Python bindings](https://www.phidgets.com/docs/Language_-_Python)
from [Phidgets, Inc.](https://www.phidgets.com/). BTrackS hardware by
[Balance Tracking Systems](https://www.balancetrackingsystems.com/).
