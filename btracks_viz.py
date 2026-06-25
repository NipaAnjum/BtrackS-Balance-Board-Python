"""

Two input modes:
  1. UDP mode (recommended): receives the feed from `btracks_logger.py`,
     so you can log and visualize simultaneously.
         python btracks_logger.py            # terminal 1
         python btracks_viz.py --udp 9001    # terminal 2

"""

import argparse
import collections
import socket
import sys
import threading
import time
from datetime import datetime

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation


# Board dimensions in cm
BOARD_W: float = 40.0
BOARD_L: float = 60.0
HALF_W: float = BOARD_W / 2.0
HALF_L: float = BOARD_L / 2.0

# Calibration (from Calibration.xlsx) 
CAL_SLOPE: list[float] = [99.7216, 100.1399, 102.3415, 200.7457]
CAL_INTERCEPT: list[float] = [-1.3472, -2.8684, 5.2427, -1.2463]


SENSOR_X: list[float] = [HALF_W, HALF_W, -HALF_W, -HALF_W]
SENSOR_Y: list[float] = [HALF_L, -HALF_L, -HALF_L, HALF_L]
SENSOR_NAMES: list[str] = ["FR", "BR", "BL", "FL"]
SENSOR_COLORS: list[str] = ["#e74c3c", "#e67e22", "#2ecc71", "#3498db"]

MIN_FORCE: float = 10.0
TRAIL_LENGTH: int = 150  # ~6 seconds at 25 Hz


class BTrackSData:
    """Thread-safe container for latest sensor data."""
    def __init__(self) -> None:
        self.cop_x: float = 0.0
        self.cop_y: float = 0.0
        self.forces: list[float] = [0.0, 0.0, 0.0, 0.0]
        self.total_force: float = 0.0
        self.cop_x_history: collections.deque = collections.deque(maxlen=TRAIL_LENGTH)
        self.cop_y_history: collections.deque = collections.deque(maxlen=TRAIL_LENGTH)
        self.time_history: collections.deque = collections.deque(maxlen=TRAIL_LENGTH)
        self.lock: threading.Lock = threading.Lock()
        self.start_time: float = time.time()


def voltage_to_force(idx: int, voltage: float, baseline: float = 0.0) -> float:
    """force = slope * (voltage - baseline) + intercept, clamped to >= 0."""
    force: float = CAL_SLOPE[idx] * (voltage - baseline) + CAL_INTERCEPT[idx]
    return max(force, 0.0)


def phidget_reader_thread(data: BTrackSData, rate: int) -> None:
    """Reads directly from Phidget hardware.

    Captures a 2-second empty-board baseline at startup, then subtracts it
    from each raw reading so that Excel intercepts (which assume a specific
    cell offset at factory time) don't show up as phantom weight on a board
    that has drifted since then. Same approach as btracks_bridge.py.
    """
    from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput

    channels: list[VoltageRatioInput] = []
    for i in range(4):
        ch = VoltageRatioInput()
        ch.setChannel(i)
        ch.openWaitForAttachment(5000)
        channels.append(ch)

    # --- Capture baseline (empty board, 2 s average) ---
    print("  Visualizer: capturing baseline (keep board EMPTY for 2 seconds)...")
    n_baseline_samples: int = 50
    baseline_sums: list[float] = [0.0, 0.0, 0.0, 0.0]
    for _ in range(n_baseline_samples):
        for i, ch in enumerate(channels):
            baseline_sums[i] += ch.getVoltageRatio()
        time.sleep(0.04)
    baseline: list[float] = [s / n_baseline_samples for s in baseline_sums]
    print(f"  Visualizer: baseline = "
          f"FR={baseline[0]:+.4f}  BR={baseline[1]:+.4f}  "
          f"BL={baseline[2]:+.4f}  FL={baseline[3]:+.4f}")
    print("  Visualizer: ready — step on the board.")

    interval: float = 1.0 / rate
    while True:
        forces: list[float] = []
        for i, ch in enumerate(channels):
            v: float = ch.getVoltageRatio()
            forces.append(voltage_to_force(i, v, baseline[i]))

        total: float = sum(forces)
        if total >= MIN_FORCE:
            cx: float = sum(f * x for f, x in zip(forces, SENSOR_X)) / total
            cy: float = sum(f * y for f, y in zip(forces, SENSOR_Y)) / total
        else:
            cx, cy = 0.0, 0.0

        now: float = time.time()
        with data.lock:
            data.cop_x = cx
            data.cop_y = cy
            data.forces = forces
            data.total_force = total
            data.cop_x_history.append(cx)
            data.cop_y_history.append(cy)
            data.time_history.append(now - data.start_time)

        time.sleep(interval)


def udp_reader_thread(data: BTrackSData, port: int) -> None:
    """Reads full data from UDP feed from btracks_logger.py."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))
    sock.settimeout(1.0)

    while True:
        try:
            packet, _ = sock.recvfrom(512)
            text: str = packet.decode("utf-8").strip()
            if text.startswith("FULL,"):
                parts = text.split(",")
                cx: float = float(parts[1])
                cy: float = float(parts[2])
                f0: float = float(parts[3])
                f1: float = float(parts[4])
                f2: float = float(parts[5])
                f3: float = float(parts[6])
                total: float = float(parts[7])
                now: float = time.time()
                with data.lock:
                    data.cop_x = cx
                    data.cop_y = cy
                    data.forces = [f0, f1, f2, f3]
                    data.total_force = total
                    data.cop_x_history.append(cx)
                    data.cop_y_history.append(cy)
                    data.time_history.append(now - data.start_time)
            elif text.startswith("COP,"):
                parts = text.split(",")
                cx = float(parts[1])
                cy = float(parts[2])
                now = time.time()
                with data.lock:
                    data.cop_x = cx
                    data.cop_y = cy
                    data.cop_x_history.append(cx)
                    data.cop_y_history.append(cy)
                    data.time_history.append(now - data.start_time)
        except socket.timeout:
            pass


def create_dashboard(data: BTrackSData) -> tuple:
    """Build the matplotlib figure layout."""
    fig = plt.figure(figsize=(14, 8), facecolor="#1a1a2e")
    fig.canvas.manager.set_window_title(
        "BTrackS Balance Board — Live Dashboard"
    )

    gs = fig.add_gridspec(2, 3, hspace=0.35, wspace=0.35,
                          left=0.06, right=0.96, top=0.93, bottom=0.08)

    ax_map = fig.add_subplot(gs[0, 0])
    ax_map.set_xlim(-HALF_W - 2, HALF_W + 2)
    ax_map.set_ylim(-HALF_L - 2, HALF_L + 2)
    ax_map.set_aspect("equal")
    ax_map.set_facecolor("#0f0f23")
    ax_map.set_title("Center of Pressure", color="white", fontsize=12, fontweight="bold")
    ax_map.tick_params(colors="gray", labelsize=8)

    board_rect = patches.Rectangle((-HALF_W, -HALF_L), BOARD_W, BOARD_L,
                                    linewidth=2, edgecolor="#444", facecolor="#1a1a2e")
    ax_map.add_patch(board_rect)
    
    ax_map.axhline(0, color="#333", linewidth=0.5, linestyle="--")
    ax_map.axvline(0, color="#333", linewidth=0.5, linestyle="--")
    
    for i in range(4):
        ax_map.text(SENSOR_X[i] * 0.75, SENSOR_Y[i] * 0.75, SENSOR_NAMES[i],
                    color=SENSOR_COLORS[i], fontsize=9, ha="center", va="center",
                    fontweight="bold")

    cop_trail, = ax_map.plot([], [], color="#00d4ff", linewidth=1, alpha=0.4)
    cop_dot, = ax_map.plot([], [], "o", color="#00ff88", markersize=12, markeredgecolor="white",
                            markeredgewidth=1.5)

    ax_bars = fig.add_subplot(gs[0, 1])
    ax_bars.set_facecolor("#0f0f23")
    ax_bars.set_title("Sensor Forces", color="white", fontsize=12, fontweight="bold")
    ax_bars.set_ylim(0, 0.001)
    ax_bars.tick_params(colors="gray", labelsize=8)
    bars = ax_bars.bar(SENSOR_NAMES, [0, 0, 0, 0], color=SENSOR_COLORS, edgecolor="#333")
    bar_labels = []
    for bar in bars:
        lbl = ax_bars.text(bar.get_x() + bar.get_width() / 2, 1, "0.0",
                           ha="center", va="bottom", color="white", fontsize=10, fontweight="bold")
        bar_labels.append(lbl)

    ax_info = fig.add_subplot(gs[0, 2])
    ax_info.set_facecolor("#0f0f23")
    ax_info.axis("off")
    ax_info.set_title("Live Values", color="white", fontsize=12, fontweight="bold")
    info_text = ax_info.text(0.5, 0.5, "", transform=ax_info.transAxes,
                             ha="center", va="center", fontsize=14, color="white",
                             fontfamily="monospace", linespacing=2.0)

    # --- CoP X Time Series (bottom-left, two columns wide) ---
    ax_cx = fig.add_subplot(gs[1, 0:2])
    ax_cx.set_facecolor("#0f0f23")
    ax_cx.set_title("CoP X (left/right) over time", color="white", fontsize=11)
    ax_cx.set_ylim(-HALF_W, HALF_W)
    ax_cx.set_ylabel("cm", color="gray", fontsize=9)
    ax_cx.set_xlabel("seconds", color="gray", fontsize=9)
    ax_cx.tick_params(colors="gray", labelsize=8)
    ax_cx.axhline(0, color="#333", linewidth=0.5, linestyle="--")
    line_cx, = ax_cx.plot([], [], color="#e74c3c", linewidth=1.5)

    # --- CoP Y Time Series (bottom-right) ---
    ax_cy = fig.add_subplot(gs[1, 2])
    ax_cy.set_facecolor("#0f0f23")
    ax_cy.set_title("CoP Y (front/back)", color="white", fontsize=11)
    ax_cy.set_ylim(-HALF_L, HALF_L)
    ax_cy.set_ylabel("cm", color="gray", fontsize=9)
    ax_cy.set_xlabel("seconds", color="gray", fontsize=9)
    ax_cy.tick_params(colors="gray", labelsize=8)
    ax_cy.axhline(0, color="#333", linewidth=0.5, linestyle="--")
    line_cy, = ax_cy.plot([], [], color="#3498db", linewidth=1.5)

    artists = {
        "cop_trail": cop_trail,
        "cop_dot": cop_dot,
        "bars": bars,
        "bar_labels": bar_labels,
        "info_text": info_text,
        "line_cx": line_cx,
        "line_cy": line_cy,
        "ax_cx": ax_cx,
        "ax_cy": ax_cy,
    }
    return fig, artists


def update_frame(frame: int, data: BTrackSData, artists: dict) -> list:
    """Called every animation frame to update all plots."""
    with data.lock:
        cx: float = data.cop_x
        cy: float = data.cop_y
        forces: list[float] = list(data.forces)
        total: float = data.total_force
        trail_x: list[float] = list(data.cop_x_history)
        trail_y: list[float] = list(data.cop_y_history)
        times: list[float] = list(data.time_history)

    artists["cop_dot"].set_data([cx], [cy])
    artists["cop_trail"].set_data(trail_x, trail_y)

    max_force: float = max(max(forces), 0.0001)
    artists["bars"][0].axes.set_ylim(0, max_force * 1.3)
    for i, bar in enumerate(artists["bars"]):
        bar.set_height(forces[i])
        artists["bar_labels"][i].set_position((bar.get_x() + bar.get_width() / 2, forces[i] + max_force * 0.02))
        artists["bar_labels"][i].set_text(f"{forces[i]:.2f}")

    now_str: str = datetime.now().strftime("%Y-%m-%d\n%H:%M:%S")
    artists["info_text"].set_text(
        f"{now_str}\n\n"
        f"CoP X:  {cx:+.2f} cm\n"
        f"CoP Y:  {cy:+.2f} cm\n\n"
        f"Weight: {total:.1f} lbs"
    )

    if len(times) > 1:
        artists["line_cx"].set_data(times, trail_x)
        artists["line_cy"].set_data(times, trail_y)
        artists["ax_cx"].set_xlim(max(0, times[-1] - 6), times[-1] + 0.5)
        artists["ax_cy"].set_xlim(max(0, times[-1] - 6), times[-1] + 0.5)

    return []


def main() -> None:
    parser = argparse.ArgumentParser(description="BTrackS Live Visualizer (standalone)")
    parser.add_argument("--udp", type=int, default=0,
                        help="Listen on UDP port (0 = read hardware directly)")
    parser.add_argument("--rate", type=int, default=25, help="Hardware read rate in Hz")
    args = parser.parse_args()

    data = BTrackSData()

    if args.udp > 0:
        print(f"Visualizer: Listening on UDP port {args.udp}")
        thread = threading.Thread(target=udp_reader_thread, args=(data, args.udp), daemon=True)
    else:
        print("Visualizer: Reading directly from BTrackS hardware")
        thread = threading.Thread(target=phidget_reader_thread, args=(data, args.rate), daemon=True)

    thread.start()

    fig, artists = create_dashboard(data)
    anim = FuncAnimation(fig, update_frame, fargs=(data, artists),
                         interval=40, blit=False, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nViz stopped.")
        sys.exit(0)
