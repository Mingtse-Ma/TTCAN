"""Render README illustrations from exported schedules. Requires Pillow only.

These illustrations are data views, not browser screenshots or bus simulation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1400, 980
INK, MUTED, LINE = "#14263c", "#64748b", "#dce4ef"
BLUE, TEAL, PURPLE, AMBER, PINK = "#2563eb", "#0d9488", "#7c3aed", "#d97706", "#db2777"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default(size=size)


def text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: object,
         size: int = 17, fill: str = INK, bold: bool = False, width: int | None = None) -> None:
    value = str(value)
    face = font(size, bold)
    if width:
        while value and draw.textlength(value, font=face) > width:
            value = value[:-4] + "..." if len(value) > 4 else ""
    draw.text(xy, value, fill=fill, font=face)


def load_case(directory: Path) -> dict:
    config = json.loads((directory / "effective_config.json").read_text())
    return {
        "directory": directory,
        "config": config,
        "matrix": json.loads((directory / "system_matrix.json").read_text()),
        "report": json.loads((directory / "validation_report.json").read_text()),
        "triggers": {node["node_id"]: json.loads(
            (directory / "nodes" / (node["node_id"] + "_trigger_list.json")).read_text())
            for node in config["nodes"]},
    }


def canvas(case: dict, title: str, subtitle: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), "#f3f6fb")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 136), fill="#10243e")
    text(draw, (44, 24), "TTCAN / SCHEDULE STUDIO", 15, "#8fb5ee", True)
    text(draw, (44, 52), title, 31, "#ffffff", True)
    text(draw, (44, 98), subtitle, 16, "#ccd9eb")
    net, nodes = case["config"]["network"], case["config"]["nodes"]
    values = [
        ("NETWORK NODES", len(nodes), "Actual master: " + net["actual_master"]),
        ("BASIC CYCLES", net["matrix_cycles"], "Partial views preserve the full matrix"),
        ("BASIC CYCLE", str(net["basic_cycle_ntu"]) + " NTU", str(net["ntu_ns"]) + " ns per NTU"),
        ("VALIDATION", "PASS" if case["report"]["valid"] else "FAIL",
         str(case["report"]["errors"]) + " errors / " + str(case["report"]["warnings"]) + " warnings"),
    ]
    for index, (label, value, hint) in enumerate(values):
        x = 40 + index * 335
        draw.rounded_rectangle((x, 158, x + 317, 270), 12, fill="white", outline=LINE)
        text(draw, (x + 17, 173), label, 12, MUTED, True)
        text(draw, (x + 17, 197), value, 27, INK, True)
        text(draw, (x + 17, 239), hint, 11, MUTED, width=290)
    text(draw, (42, 939), "Rendered from actual JSON exports. Static reservations and candidate windows; no arbitration simulation.", 14, MUTED)
    return image, draw


def windows(row: dict) -> list[dict]:
    merged = []
    for cell in row["cells"]:
        if merged and (merged[-1]["window_id"], merged[-1]["kind"]) == (cell["window_id"], cell["kind"]):
            merged[-1]["end_ntu"] = cell["end_ntu"]
        else:
            merged.append(dict(cell))
    return merged


def matrix_view(case: dict, title: str, focus: str | None = None,
                max_cycles: int = 5) -> Image.Image:
    image, draw = canvas(case, title, "Model-rendered inspection demo / Offline HTML provides the interactive report")
    net = case["config"]["network"]
    colors = {node["node_id"]: [BLUE, TEAL, "#0284c7", "#c2410c", "#7c3aed"][i % 5]
              for i, node in enumerate(case["config"]["nodes"])}
    draw.rounded_rectangle((40, 292, 1360, 824), 12, fill="white", outline=LINE)
    text(draw, (62, 308), "System Matrix", 24, INK, True)
    legend = [("Single", BLUE), ("Merged / Arbitration candidates", AMBER), ("Reference", PURPLE), ("Rx check", PINK)]
    lx = 63
    for name, color in legend:
        draw.rounded_rectangle((lx, 351, lx + 13, 364), 3, fill=color)
        text(draw, (lx + 21, 348), name, 13, MUTED)
        lx += int(draw.textlength(name, font=font(13))) + 65
    x0, x1, y0 = 172, 1322, 418
    horizon = max(net["basic_cycle_ntu"], net["reference"]["watch_mark_ntu"])
    scale = (x1 - x0) / horizon
    for i in range(6):
        tick = round(horizon * i / 5)
        x = int(x0 + tick * scale)
        draw.line((x, y0 - 10, x, 796), fill="#edf1f6")
        text(draw, (x - 20, y0 - 35), tick, 12, MUTED)
    text(draw, (x1 - 30, y0 - 58), "NTU", 12, MUTED)
    rows = case["matrix"]["rows"][:max_cycles]
    step = min(69, int(352 / max(1, len(rows))))
    bar_height = max(22, step - 20)
    for index, row in enumerate(rows):
        y = y0 + index * step
        text(draw, (64, y + 3), "Cycle " + str(row["cycle"]), 15, INK, True)
        draw.rounded_rectangle((x0, y, x1, y + bar_height), 4, fill="#f1f5f9")
        boundary = int(x0 + net["basic_cycle_ntu"] * scale)
        draw.rectangle((boundary, y, x1, y + bar_height), fill="#e7edf4")
        for window in windows(row):
            if window["kind"] == "free":
                continue
            left = int(x0 + window["start_ntu"] * scale)
            right = max(left + 3, int(x0 + window["end_ntu"] * scale))
            kind = window["kind"]
            color = PURPLE if kind == "reference" else AMBER if kind in ("merged", "arbitration") else colors.get(window["nodes"][0], BLUE)
            active = not focus or focus in window["messages"] or focus == window["group_id"]
            if not active:
                color = "#b9c6d8"
            if kind in ("merged", "arbitration"):
                draw.rounded_rectangle((left, y, right, y + bar_height), 4, fill="#fff5e6", outline=color, width=3)
                label_color = color
            else:
                draw.rounded_rectangle((left, y, right, y + bar_height), 4, fill=color)
                label_color = "white"
            label = "Ref" if kind == "reference" else ", ".join(window["messages"])
            text(draw, (left + 5, y + max(3, (bar_height - 17)//2)), label, 13, label_color, True, right-left-8)
        for node, triggers in case["triggers"].items():
            for trigger in triggers:
                if trigger["alias"] == "rx" and row["cycle"] in trigger["active_cycles"]:
                    if focus and focus not in (trigger["message_name"], trigger["group_id"]):
                        continue
                    x = int(x0 + trigger["time_mark_ntu"] * scale)
                    draw.line((x, y - 3, x, y + bar_height + 5), fill=PINK, width=3)
    if focus == "Msg7":
        details = ["Msg7 / CAN ID 0x107", "NodeA: Tx buffer 7", "NodeB: standard Rx filter 0", "Filter mapping and Tx buffer numbers are local resources."]
    elif focus == "Merge1":
        details = ["Merge1 / Cycle 1", "Mark3: Msg2 / Tx_Trigger_Merged / MNR 2", "Mark4: Msg4 / Tx_Trigger_Arbitration / MNR 4", "Candidate reservation only. Time Marks are not guaranteed frame SOFs."]
    else:
        details = ["One schedule model, consistent node exports", "Common time boundaries across cycles", "Reference traffic, data reservations, and reception checks remain distinct."]
    for index, value in enumerate(details):
        text(draw, (62, 845 + index * 21), value, 14, INK if index == 0 else MUTED, index == 0)
    return image


def trigger_view(case: dict) -> Image.Image:
    image, draw = canvas(case, "Node Trigger Lists", "Actual configured memory entries / End_of_List padding omitted from this illustration")
    for index, node in enumerate(case["config"]["nodes"][:2]):
        nid = node["node_id"]
        x = 40 + index * 673
        draw.rounded_rectangle((x, 294, x + 650, 841), 12, fill="white", outline=LINE)
        used = [t for t in case["triggers"][nid] if t["alias"] != "end"]
        text(draw, (x + 20, 314), nid + " / " + node["role"], 22, INK, True)
        text(draw, (x + 20, 350), str(len(used)) + " used entries / " + str(node["trigger_capacity"]) + " slots", 14, MUTED)
        columns = [(18, "Idx"), (65, "Time"), (155, "Cycle Code"), (296, "Trigger"), (470, "MNR"), (530, "Message")]
        for offset, label in columns:
            text(draw, (x + offset, 392), label, 12, MUTED, True)
        for row, trigger in enumerate(used):
            y = 426 + row * 40
            draw.line((x + 15, y + 30, x + 633, y + 30), fill=LINE)
            values = [trigger["trigger_index"], trigger["time_mark_ntu"], trigger["cycle_code"] or "N/A", trigger["alias"], trigger["mnr"] if trigger["mnr"] is not None else "N/A", trigger["message_name"] or "N/A"]
            for (offset, _), value in zip(columns, values):
                text(draw, (x + offset, y), value, 13, PINK if trigger["alias"] == "rx" else INK, width=150 if offset == 296 else None)
    text(draw, (62, 868), "Tx buffers, Rx filters, and trigger-memory indices use separate numbering spaces.", 17, INK, True)
    text(draw, (62, 899), "Watch is a timeout monitor. An End_of_List slot is not a normal bus event.", 15, MUTED)
    return image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = args.output_dir or root / "docs" / "images"
    destination.mkdir(parents=True, exist_ok=True)
    default = load_case(root / "output")
    random_case = load_case(root / "random_cases" / "run_20261029_icw2pf45" / "case_0000")
    frames = [
        matrix_view(default, "Default NodeA / NodeB System Matrix"),
        matrix_view(default, "Inspect Msg7: Tx and Rx Resource Mapping", "Msg7"),
        matrix_view(default, "Inspect the Merged Candidate Reservation", "Merge1"),
        trigger_view(default),
        matrix_view(random_case, "Seeded Random Case / Multiple Nodes", max_cycles=8),
    ]
    for name, image in [("overview.png", frames[0]), ("trigger_list.png", frames[3]), ("random_case.png", frames[4])]:
        image.save(destination / name, optimize=True)
    animation = [image.resize((1000, 700), Image.Resampling.LANCZOS) for image in frames]
    animation[0].save(destination / "demo.gif", save_all=True, append_images=animation[1:],
                      duration=[2800, 2800, 3200, 3400, 3200], loop=0, optimize=True)
    report = {"source": "Actual exported schedule JSON; model-rendered illustrations, not browser screenshots",
              "default_matrix_cycles": default["config"]["network"]["matrix_cycles"],
              "random_nodes": len(random_case["config"]["nodes"]),
              "random_matrix_cycles": random_case["config"]["network"]["matrix_cycles"],
              "gif_frames": len(frames), "gif_duration_ms": 15400,
              "arbitration_simulated": False}
    (destination / "demo_metadata.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
