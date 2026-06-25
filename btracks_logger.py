"""
BTrackS standalone data collector + CSV logger (Phidget22 backend).

USAGE
    python btracks_logger.py                       # log + UDP, default settings
    python btracks_logger.py --no-udp              # log only, no visualizer feed
    python btracks_logger.py --no-log              # UDP only, no CSV
    python btracks_logger.py --log-prefix Test1    # CSV named Test1_<date>.csv
    python btracks_logger.py --log-dir my_data     # save CSVs to my_data/

Stop with Ctrl+C.
"""

import argparse
import csv
import os
import socket
import sys
import time
from datetime import datetime

from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
from Phidget22.BridgeGain import BridgeGain
from Phidget22.PhidgetException import PhidgetException


# Calibration (from Calibration.xlsx)

CAL_SLOPE: list[float] = [99.7216, 100.1399, 102.3415, 200.7457]
CAL_INTERCEPT: list[float] = [-1.3472, -2.8684, 5.2427, -1.2463]


BOARD_WIDTH: float = 40.0   # cm, left-right
BOARD_LENGTH: float = 60.0  # cm, front-back
HALF_W: float = BOARD_WIDTH / 2.0
HALF_L: float = BOARD_LENGTH / 2.0

SENSOR_X: list[float] = [HALF_W, HALF_W, -HALF_W, -HALF_W]
SENSOR_Y: list[float] = [HALF_L, -HALF_L, -HALF_L, HALF_L]
SENSOR_NAMES: list[str] = ["FR", "BR", "BL", "FL"]


MIN_FORCE: float = 5.0     # lbs
DEFAULT_RATE_HZ: int = 25  # BTrackS native sample rate

_baseline: list[float] = [0.0, 0.0, 0.0, 0.0]


# Phidget setup

def open_channels() -> list[VoltageRatioInput]:
    """Open and configure the 4 Phidget22 VoltageRatioInput channels
    (gain=128, data interval = 40 ms = 25 Hz)."""
    channels: list[VoltageRatioInput] = []
    attach_err: Exception | None = None

    for attempt in range(1, 4):
        try:
            channels = []
            for i in range(4):
                ch: VoltageRatioInput = VoltageRatioInput()
                ch.setChannel(i)
                ch.openWaitForAttachment(10000)
                channels.append(ch)
            attach_err = None
            break
        except PhidgetException as e:
            attach_err = e
            print(f"  Attach attempt {attempt}/3 failed: {e}. Retrying...")
            for ch in channels:
                try:
                    ch.close()
                except PhidgetException:
                    pass
            channels = []
            time.sleep(1.0)

    if attach_err is not None:
        print("  *** Phidget attach FAILED. Common causes:")
        print("      - BTrackS Assess (or another program) is using the device")
        print("      - USB cable not connected")
        print("      - Phidget22 driver not installed")
        raise attach_err

    serial: int = channels[0].getDeviceSerialNumber()
    print(f"  Serial: {serial}, Inputs: {len(channels)}")

    for ch in channels:
        ch.setBridgeGain(BridgeGain.BRIDGE_GAIN_128)
        ch.setDataInterval(40)  # 40 ms = 25 Hz
    time.sleep(0.5)
    print("  Gain=128, DataInterval=40 ms (25 Hz)")
    return channels


def capture_baseline(channels: list[VoltageRatioInput]) -> None:
    """Capture empty-board raw mV/V offset (zero reference)."""
    global _baseline
    print("  Capturing baseline (keep board EMPTY for 2 seconds)...")
    samples: list[list[float]] = []
    for _ in range(50):
        samples.append([ch.getVoltageRatio() for ch in channels])
        time.sleep(0.04)
    for i in range(4):
        _baseline[i] = sum(s[i] for s in samples) / len(samples)
    print(f"  Baseline: "
          f"{SENSOR_NAMES[0]}={_baseline[0]:+.4f}  "
          f"{SENSOR_NAMES[1]}={_baseline[1]:+.4f}  "
          f"{SENSOR_NAMES[2]}={_baseline[2]:+.4f}  "
          f"{SENSOR_NAMES[3]}={_baseline[3]:+.4f}")


def read_sensors(channels: list[VoltageRatioInput]) -> tuple[
        list[float], list[float], float, float, float]:
    """Read all 4 cells, subtract baseline, apply calibration, compute CoP.

    Returns (raw, forces_lbs, cop_x_cm, cop_y_cm, total_weight_lbs).
    """
    raw: list[float] = [ch.getVoltageRatio() for ch in channels]
    forces: list[float] = []
    for i in range(4):
        f: float = CAL_SLOPE[i] * (raw[i] - _baseline[i]) + CAL_INTERCEPT[i]
        forces.append(max(f, 0.0))
    total: float = sum(forces)
    if total < MIN_FORCE:
        return raw, forces, 0.0, 0.0, total
    cop_x: float = sum(forces[i] * SENSOR_X[i] for i in range(4)) / total
    cop_y: float = sum(forces[i] * SENSOR_Y[i] for i in range(4)) / total
    return raw, forces, cop_x, cop_y, total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTrackS standalone data collector + CSV logger"
    )
    parser.add_argument("--rate", type=int, default=DEFAULT_RATE_HZ,
                        help=f"Sample rate in Hz (default {DEFAULT_RATE_HZ}, "
                             f"BTrackS max=25)")
    parser.add_argument("--no-log", action="store_true",
                        help="Disable CSV logging")
    parser.add_argument("--no-udp", action="store_true",
                        help="Disable UDP feed to the visualizer")
    parser.add_argument("--log-dir", default="logs",
                        help="Directory to save CSV log (default 'logs/')")
    parser.add_argument("--log-prefix", default="",
                        help="Filename prefix (default empty — use 'cop')")
    parser.add_argument("--viz-host", default="127.0.0.1",
                        help="UDP host for visualizer (default 127.0.0.1)")
    parser.add_argument("--viz-port", type=int, default=9001,
                        help="UDP port for visualizer (default 9001)")
    parser.add_argument("--skip-baseline", action="store_true",
                        help="Skip the empty-board baseline capture")
    parser.add_argument("--print-every", type=int, default=25,
                        help="Print CoP/weight every N samples "
                             "(default 25 = once per second)")
    args = parser.parse_args()

    interval: float = 1.0 / args.rate

    print("BTrackS Logger: Connecting...")
    print("  (Close BTrackS Assess first if it's open.)")
    channels: list[VoltageRatioInput] = open_channels()
    print("BTrackS Logger: Connected")

    if not args.skip_baseline:
        capture_baseline(channels)
        print("BTrackS Logger: Ready — you can step on the board now.")
    else:
        print("BTrackS Logger: Skipping baseline (raw cell offsets remain)")

    sock: socket.socket | None = None
    viz_target: tuple[str, int] | None = None
    if not args.no_udp:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        viz_target = (args.viz_host, args.viz_port)
        print(f"BTrackS Logger: Sending UDP to "
              f"{viz_target[0]}:{viz_target[1]}")

    csv_file = None
    csv_writer = None
    csv_path: str = ""
    if not args.no_log:
        os.makedirs(args.log_dir, exist_ok=True)
        ts: str = (datetime.now().strftime("%Y-%m-%d-%H-%M-%S") +
                   f"-{datetime.now().microsecond // 1000:03d}")
        prefix: str = (args.log_prefix + "_") if args.log_prefix else ""
        csv_path = os.path.join(args.log_dir, f"{prefix}cop_{ts}.csv")
        csv_file = open(csv_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "epoch", "datetime",
            "raw_FR", "raw_BR", "raw_BL", "raw_FL",
            "force_FR_lbs", "force_BR_lbs", "force_BL_lbs", "force_FL_lbs",
            "cop_x_cm", "cop_y_cm", "weight_lbs",
        ])
        print(f"BTrackS Logger: Logging to {csv_path}")
    else:
        print("BTrackS Logger: CSV disabled")

    print("BTrackS Logger: Press Ctrl+C to stop.\n")

    sample_count: int = 0
    try:
        while True:
            t_start: float = time.perf_counter()
            raw, forces, cop_x, cop_y, weight = read_sensors(channels)
            now: float = time.time()

            # UDP feed to visualizer.
            if sock is not None and viz_target is not None:
                msg: str = (
                    f"FULL,{cop_x:.4f},{cop_y:.4f},"
                    f"{forces[0]:.2f},{forces[1]:.2f},"
                    f"{forces[2]:.2f},{forces[3]:.2f},"
                    f"{weight:.2f}\n"
                )
                try:
                    sock.sendto(msg.encode("utf-8"), viz_target)
                except OSError:
                    pass

            # CSV row.
            if csv_writer is not None:
                dt_str: str = (datetime.fromtimestamp(now)
                               .strftime("%Y-%m-%d-%H-%M-%S") +
                               f"-{int(now * 1000) % 1000:03d}")
                csv_writer.writerow([
                    f"{now:.3f}", dt_str,
                    f"{raw[0]:.6f}", f"{raw[1]:.6f}",
                    f"{raw[2]:.6f}", f"{raw[3]:.6f}",
                    f"{forces[0]:.2f}", f"{forces[1]:.2f}",
                    f"{forces[2]:.2f}", f"{forces[3]:.2f}",
                    f"{cop_x:.4f}", f"{cop_y:.4f}", f"{weight:.2f}",
                ])

            # Periodic console print.
            sample_count += 1
            if args.print_every > 0 and sample_count % args.print_every == 0:
                print(f"  CoP: X={cop_x:+6.2f} cm  Y={cop_y:+6.2f} cm  "
                      f"Weight={weight:5.1f} lbs "
                      f"({weight * 0.4536:5.1f} kg)")

            elapsed: float = time.perf_counter() - t_start
            sleep_time: float = interval - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\nBTrackS Logger: Stopped after {sample_count} samples.")
    finally:
        if csv_file is not None:
            csv_file.close()
            print(f"BTrackS Logger: CSV saved -> {csv_path}")
        if sock is not None:
            sock.close()
        for ch in channels:
            try:
                ch.close()
            except PhidgetException:
                pass
        print("BTrackS Logger: Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
