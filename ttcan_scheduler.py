#!/usr/bin/env python3
"""Generate a static TTCAN schedule, resource maps, and an offline report.

Python 3.10 or newer. No third-party runtime dependencies.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
TYPES = {
    "ref": (0, "Tx_Ref_Trigger"), "ref_gap": (1, "Tx_Ref_Trigger_Gap"),
    "single": (2, "Tx_Trigger_Single"), "arbi": (4, "Tx_Trigger_Arbitration"),
    "merged": (5, "Tx_Trigger_Merged"), "watch": (6, "Watch_Trigger"),
    "watch_gap": (7, "Watch_Trigger_Gap"), "rx": (8, "Rx_Trigger"),
    "end": (10, "End_of_List"),
}
TX_TYPES = {"single", "arbi", "merged"}
CONTROL_TYPES = {"ref", "ref_gap", "watch", "watch_gap"}
CAPS = ("tx_message_capacity", "rx_std_filter_capacity", "rx_ext_filter_capacity", "trigger_capacity")
DEFAULT_CAPS = dict(zip(CAPS, (8, 8, 0, 16)))
CJK = re.compile("[" + "".join(chr(a) + "-" + chr(b) for a, b in
    ((0x2E80, 0xA4CF), (0xAC00, 0xD7FF), (0xF900, 0xFAFF), (0x20000, 0x323AF))) + "]")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def number(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Boolean values are not integer parameters")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0) if value.lower().startswith(("0x", "0b", "0o")) else int(value)
    raise ValueError(f"Expected an integer, got {value!r}")


def decode_cycle_code(code: Any) -> tuple[int, int]:
    cc = number(code)
    if not 0 <= cc <= 127:
        raise ValueError("Cycle Code must be in 0..127")
    if cc < 2:
        return 1, 0
    period = 1 << (cc.bit_length() - 1)
    return period, cc - period


def encode_cycle_code(period: int, phase: int) -> str | None:
    if period < 1 or not 0 <= phase < period:
        raise ValueError("Invalid period or phase")
    if period > 64 or period & (period - 1):
        return None
    return f"0b{(0 if period == 1 else period | phase):07b}"


def trigger_type(value: Any) -> str:
    if isinstance(value, str) and value in TYPES:
        return value
    for alias, (code, full) in TYPES.items():
        if value == full:
            return alias
    try:
        code = number(value)
    except ValueError:
        code = -1
    if 10 <= code <= 15:
        return "end"
    for alias, (n, _) in TYPES.items():
        if code == n:
            return alias
    raise ValueError(f"Unsupported trigger type: {value!r}")


def frame_bits(dlc: int, frame_format: str) -> dict[str, int]:
    """Conservative Classical CAN data-frame bound, including intermission.

    The stuffable SOF-through-CRC sequence is bounded by ceil(length / 4)
    additional bits. The 13-bit trailer includes delimiter, ACK, EOF, and IFS.
    This is a safe reservation estimate, not payload-dependent bit encoding.
    """
    body = (34 if frame_format == "standard" else 54) + 8 * dlc
    return {"unstuffed_bits": body + 13, "stuffing_bound_bits": (body + 3) // 4,
            "reserved_bits": body + (body + 3) // 4 + 13}


def ceil_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator - 1) // denominator


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trigger:
    node_id: str
    alias: str
    time_mark_ntu: int | None
    period_cycles: int | None = None
    phase: int | None = None
    mnr: int | None = None
    resource_type: str | None = None
    message_name: str | None = None
    can_id: str | None = None
    window_id: str | None = None
    group_id: str | None = None
    time_label: str | None = None
    state: str = "both"
    trigger_index: int = -1
    active_cycles: list[int] = field(default_factory=list)

    def record(self) -> dict[str, Any]:
        r = asdict(self)
        r.update(type_code=f"0x{TYPES[self.alias][0]:X}", type_name=TYPES[self.alias][1],
                 cycle_code=encode_cycle_code(self.period_cycles, self.phase)
                 if self.period_cycles is not None else None)
        return r


@dataclass
class Window:
    window_id: str
    cycle: int
    start_ntu: int
    end_ntu: int
    kind: str
    messages: list[str] = field(default_factory=list)
    nodes: list[str] = field(default_factory=list)
    group_id: str | None = None


class Scheduler:
    """Compile one configuration into a shared schedule representation."""

    def __init__(self, config: dict[str, Any]):
        self.config = copy.deepcopy(config)
        self.issues: list[Issue] = []
        self.nodes: dict[str, dict[str, Any]] = {}
        self.messages: dict[str, dict[str, Any]] = {}
        self.triggers: dict[str, list[Trigger]] = defaultdict(list)
        self.windows: list[Window] = []
        self.requests: list[dict[str, Any]] = []
        self.maps: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.events: list[dict[str, Any]] = []
        self.resources: dict[str, Any] = {}
        self.matrix: dict[str, Any] = {}
        self.origins: list[int] = []

    def issue(self, code: str, message: str, severity: str = "error", **context: Any) -> None:
        self.issues.append(Issue(severity, code, message, context))

    @property
    def valid(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def integer(self, value: Any, name: str, minimum: int = 0, maximum: int = 10**9) -> int:
        n = number(value)
        if not minimum <= n <= maximum:
            raise ValueError(f"{name} must be in {minimum}..{maximum}")
        return n

    def mark(self, value: Any) -> tuple[int, str | None]:
        labels = self.net.get("time_marks", {})
        if isinstance(value, str) and value in labels:
            return self.integer(labels[value], value), value
        return self.integer(value, "time_mark"), None

    def condition(self, spec: dict[str, Any]) -> tuple[int, int, list[int]]:
        if spec.get("cycle_code") is not None:
            period, phase = decode_cycle_code(spec["cycle_code"])
            if "period_cycles" in spec and number(spec["period_cycles"]) != period:
                raise ValueError("period_cycles disagrees with cycle_code")
            if "phase" in spec and number(spec["phase"]) != phase:
                raise ValueError("phase disagrees with cycle_code")
        else:
            period = self.integer(spec.get("period_cycles", 1), "period_cycles", 1)
            phase = self.integer(spec.get("phase", 0), "phase", 0, period - 1)
        if self.cycles % period:
            raise ValueError(f"Period {period} does not divide matrix length {self.cycles}")
        if self.mode == "m_ttcan" and encode_cycle_code(period, phase) is None:
            raise ValueError("Period is not representable by the hardware Cycle Code")
        return period, phase, [c for c in range(self.cycles) if c % period == phase]

    def duration(self, message: dict[str, Any]) -> int:
        bits = frame_bits(message["dlc"], message["frame_format"])
        message.update(bits)
        bound = ceil_div(bits["reserved_bits"] * 10**9, self.bitrate * self.ntu_ns)
        explicit = message.get("worst_frame_ntu")
        if explicit is not None and number(explicit) < bound:
            raise ValueError(f"worst_frame_ntu is below the conservative bound {bound}")
        message["frame_time_ntu"] = number(explicit) if explicit is not None else bound
        return message["frame_time_ntu"]

    def normalize(self) -> None:
        if not isinstance(self.config, dict):
            raise ValueError("The configuration root must be an object")
        if self.config.get("schema_version", 1) != 1:
            raise ValueError("Unsupported schema_version")
        if CJK.search(json.dumps(self.config, ensure_ascii=False)):
            raise ValueError("Configuration contains non-English CJK text")
        self.net = self.config.setdefault("network", {})
        if not isinstance(self.net, dict):
            raise ValueError("network must be an object")
        for key in ("nodes", "messages", "arbitration_groups"):
            if not isinstance(self.config.get(key, []), list) or any(not isinstance(x, dict) for x in self.config.get(key, [])):
                raise ValueError(f"{key} must be a list of objects")
        for key in ("node_defaults", "metadata", "node_triggers"):
            if not isinstance(self.config.get(key, {}), dict):
                raise ValueError(f"{key} must be an object")
        self.mode = self.net.setdefault("resource_mode", "custom")
        if self.mode not in {"custom", "m_ttcan"}:
            raise ValueError("resource_mode must be custom or m_ttcan")
        if self.mode == "m_ttcan" and self.net.get("controller_version") != "3.3.0":
            raise ValueError("The m_ttcan profile requires controller_version 3.3.0")
        for key, default, lo in (("bitrate", 1000000, 1), ("ntu_ns", 1000, 1),
                                 ("basic_cycle_ntu", 2000, 1), ("matrix_cycles", 64, 1),
                                 ("expand_matrices", 1, 1), ("tx_enable_ntu", 4, 1),
                                 ("guard_ntu", 10, 0), ("filter_delay_ntu", 5, 0),
                                 ("min_trigger_spacing_ntu", 1, 1)):
            self.net[key] = self.integer(self.net.get(key, default), key, lo)
        self.bitrate, self.ntu_ns = self.net["bitrate"], self.net["ntu_ns"]
        self.basic, self.cycles = self.net["basic_cycle_ntu"], self.net["matrix_cycles"]
        if self.cycles > 4096 or self.net["expand_matrices"] * self.cycles > 16384:
            raise ValueError("Expanded schedule exceeds the 16384-cycle tool limit")
        if self.mode == "m_ttcan" and self.cycles not in (1, 2, 4, 8, 16, 32, 64):
            raise ValueError("Hardware matrix length must be a power of two in 1..64")
        if self.net.get("level", 1) != 1:
            raise ValueError("This release implements the Level 1 static timing model only")
        if self.ntu_ns * self.bitrate != 10**9:
            raise ValueError("Level 1 requires one NTU to equal one CAN bit time")
        self.txew, self.guard = self.net["tx_enable_ntu"], self.net["guard_ntu"]
        self.delay = self.net["filter_delay_ntu"]
        self.ref_delay = 1 + ceil_div(10**9, self.bitrate * self.ntu_ns)
        ref = self.net.setdefault("reference", {})
        if not isinstance(ref, dict):
            raise ValueError("reference must be an object")
        ref["can_id"] = f"0x{number(ref.get('can_id', '0x080')):03X}"
        ref["frame_format"] = ref.get("frame_format", "standard")
        ref["dlc"] = self.integer(ref.get("dlc", 1), "reference DLC", 1, 8)
        ref["tx_mark_ntu"] = self.integer(ref.get("tx_mark_ntu", self.basic - self.ref_delay), "reference mark")
        ref["watch_mark_ntu"] = self.integer(ref.get("watch_mark_ntu", self.basic + 300), "watch mark")
        self.check_can(ref)
        self.ref_duration = self.duration(ref)
        if ref["tx_mark_ntu"] + self.ref_delay != self.basic:
            raise ValueError("Basic Cycle must equal reference Tx mark + 1 NTU + 1 CAN bit time")
        if ref["watch_mark_ntu"] <= self.basic + self.ref_duration:
            raise ValueError("Watch mark must allow the next reference frame to finish")
        gap = self.net.setdefault("gap", {"enabled": False})
        if not isinstance(gap, dict):
            raise ValueError("gap must be an object")
        gap.setdefault("enabled", False)
        if not isinstance(gap["enabled"], bool):
            raise ValueError("gap.enabled must be a Boolean")
        self.gap_cycles: set[int] = set()
        if gap["enabled"]:
            gap["duration_ntu"] = self.integer(gap.get("duration_ntu", 500), "gap duration", 1)
            self.gap_cycles = {self.integer(c, "gap cycle", 0, self.cycles - 1)
                               for c in gap.get("after_cycles", [self.cycles - 1])}
            gap["after_cycles"] = sorted(self.gap_cycles)
            gap["ref_gap_mark_ntu"] = self.basic + gap["duration_ntu"] - self.ref_delay
            gap["watch_gap_mark_ntu"] = ref["watch_mark_ntu"] + gap["duration_ntu"]
            self.issue("STATIC_GAP_MODEL", "Gap is a configured fixed boundary extension, not an external-event state-machine simulation", "warning")
        defaults = {**DEFAULT_CAPS, **self.config.get("node_defaults", {})}
        for raw in self.config.get("nodes", []):
            node = copy.deepcopy(raw)
            nid = node.get("node_id", "")
            if not NAME.fullmatch(nid) or nid in self.nodes:
                raise ValueError(f"Invalid or duplicate node_id: {nid!r}")
            node.setdefault("node_name", nid)
            if node.get("role") not in {"master", "slave"}:
                raise ValueError(f"Invalid role for {nid}")
            for cap in CAPS:
                node[cap] = self.integer(node.get(cap, defaults[cap]), cap, 0, 65536)
            if self.mode == "m_ttcan":
                for cap, maximum in zip(CAPS, (32, 64, 128, 64)):
                    if node[cap] > maximum:
                        raise ValueError(f"{nid}: {cap} exceeds hardware limit {maximum}")
            self.nodes[nid] = node
        if not self.nodes:
            raise ValueError("At least one node is required")
        masters = [n for n in self.nodes.values() if n["role"] == "master"]
        if not masters or len(masters) > 8:
            raise ValueError("One to eight candidate masters are required")
        priorities = set()
        for index, node in enumerate(masters):
            p = self.integer(node.get("master_priority", index), "master_priority", 0, 7)
            if p in priorities:
                raise ValueError("Candidate master priorities must be unique")
            node["master_priority"] = p
            priorities.add(p)
        actual = self.net.get("actual_master", min(masters, key=lambda n: n["master_priority"])["node_id"])
        if actual not in self.nodes or self.nodes[actual]["role"] != "master":
            raise ValueError("actual_master must reference a candidate master")
        self.net["actual_master"] = actual
        ref_id = number(ref["can_id"])
        if ref_id & 7 != self.nodes[actual]["master_priority"]:
            raise ValueError("Reference identifier low three bits must match the actual master priority")
        self.config["nodes"] = list(self.nodes.values())
        self.used: dict[tuple[str, str], dict[int, str]] = defaultdict(dict)
        for raw in self.config.get("messages", []):
            sender, name = raw.get("sender"), raw.get("name", "")
            if sender not in self.nodes:
                raise ValueError(f"Unknown sender for {name}")
            if raw.get("tx_mnr") is not None:
                self.allocate(sender, "tx_buffer", raw["tx_mnr"], name)
            recipients = raw.get("receivers", [])
            if not isinstance(recipients, list):
                raise ValueError("receivers must be a list")
            for recipient in recipients:
                if isinstance(recipient, dict) and recipient.get("filter_mnr") is not None:
                    nid = recipient.get("node_id")
                    if nid not in self.nodes:
                        raise ValueError(f"Unknown receiver for {name}: {nid}")
                    resource = "rx_std_filter" if raw.get("frame_format", "standard") == "standard" else "rx_ext_filter"
                    self.allocate(nid, resource, recipient["filter_mnr"], name)
        seen_ids = set()
        for raw in self.config.get("messages", []):
            m = copy.deepcopy(raw)
            name = m.get("name", "")
            if not NAME.fullmatch(name) or name in self.messages:
                raise ValueError(f"Invalid or duplicate message name: {name!r}")
            if m.get("sender") not in self.nodes:
                raise ValueError(f"Unknown sender for {name}")
            m.setdefault("frame_format", "standard")
            m["dlc"] = self.integer(m.get("dlc", 8), "DLC", 0, 8)
            m["can_id"] = f"0x{number(m['can_id']):X}"
            self.check_can(m)
            identity = (m["frame_format"], number(m["can_id"]))
            if identity in seen_ids:
                raise ValueError(f"Duplicate CAN identifier for {name}")
            if m["frame_format"] == ref["frame_format"] and number(m["can_id"]) >> 3 == ref_id >> 3:
                raise ValueError(f"{name} collides with the reference identifier mask")
            seen_ids.add(identity)
            self.duration(m)
            m["tx_mnr"] = self.allocate(m["sender"], "tx_buffer", m.get("tx_mnr"), name)
            receivers = []
            if not isinstance(m.get("receivers", []), list):
                raise ValueError("receivers must be a list")
            for recipient in m.get("receivers", []):
                if not isinstance(recipient, (str, dict)):
                    raise ValueError("Each receiver must be a node ID or an object")
                r = {"node_id": recipient} if isinstance(recipient, str) else copy.deepcopy(recipient)
                nid = r.get("node_id")
                if nid not in self.nodes or nid in {x["node_id"] for x in receivers} or nid == m["sender"]:
                    raise ValueError(f"Invalid or duplicate receiver for {name}: {nid}")
                resource = "rx_std_filter" if m["frame_format"] == "standard" else "rx_ext_filter"
                r["filter_mnr"] = self.allocate(nid, resource, r.get("filter_mnr"), name)
                self.maps[nid].append(dict(message_name=name, can_id=m["can_id"], direction="rx",
                                           resource_type=resource, mnr=r["filter_mnr"], peer=m["sender"]))
                receivers.append(r)
            m["receivers"] = receivers
            self.maps[m["sender"]].append(dict(message_name=name, can_id=m["can_id"], direction="tx",
                                              resource_type="tx_buffer", mnr=m["tx_mnr"],
                                              peer=",".join(r["node_id"] for r in receivers)))
            self.messages[name] = m
        self.config["messages"] = list(self.messages.values())

    def check_can(self, m: dict[str, Any]) -> None:
        fmt = m["frame_format"]
        if fmt not in {"standard", "extended"}:
            raise ValueError("frame_format must be standard or extended")
        self.integer(m["can_id"], "CAN ID", 0, 0x7FF if fmt == "standard" else 0x1FFFFFFF)

    def allocate(self, nid: str, resource: str, requested: Any, name: str) -> int:
        capkey = {"tx_buffer": CAPS[0], "rx_std_filter": CAPS[1], "rx_ext_filter": CAPS[2]}[resource]
        capacity, used = self.nodes[nid][capkey], self.used[(nid, resource)]
        mnr = next((x for x in range(capacity) if x not in used), capacity) if requested is None else number(requested)
        if not 0 <= mnr < capacity:
            raise ValueError(f"{nid}: {resource} capacity {capacity} cannot allocate {name} at MNR {mnr}; used {len(used)}, required at least {max(len(used)+1,mnr+1)}")
        if mnr in used and used[mnr] != name:
            raise ValueError(f"{nid}: {resource} MNR {mnr} is already occupied by {used[mnr]}")
        used[mnr] = name
        return mnr

    def overlaps(self, start: int, end: int, cycles: list[int], ignore: str | None = None) -> list[Window]:
        return [w for w in self.windows if w.cycle in cycles and w.window_id != ignore
                and start < w.end_ntu and w.start_ntu < end]

    def first_fit(self, length: int, cycles: list[int], deadline: int) -> int:
        candidates = sorted({self.ref_duration + self.guard} |
                            {w.end_ntu for w in self.windows if w.cycle in cycles})
        for start in candidates:
            if start + length <= deadline and not self.overlaps(start, start + length, cycles):
                return start
        raise ValueError(f"No {length}-NTU interval fits before {deadline} in cycles {cycles}")

    def reserve(self, wid: str, start: int, end: int, cycles: list[int], kind: str,
                messages: list[str], group: str | None = None) -> None:
        limit = self.net["reference"]["tx_mark_ntu"]
        if start < self.ref_duration + self.guard or end > limit or end <= start:
            self.issue("WINDOW_BOUNDS", "Window is outside the permitted data region", window_id=wid,
                       start_ntu=start, end_ntu=end, data_end_ntu=limit, cycles=cycles)
        conflicts = self.overlaps(start, end, cycles)
        if conflicts:
            self.issue("WINDOW_OVERLAP", "Reserved bus windows overlap", window_id=wid,
                       start_ntu=start, end_ntu=end,
                       conflicts=[dict(window_id=w.window_id, cycle=w.cycle, start_ntu=w.start_ntu, end_ntu=w.end_ntu) for w in conflicts])
        for c in cycles:
            active_messages = [name for name in messages if not group or any(
                r["message"]["name"] == name and r["group_id"] == group and c in r["cycles"]
                for r in self.requests)]
            nodes = sorted({self.messages[n]["sender"] for n in active_messages})
            self.windows.append(Window(wid, c, start, end, kind, active_messages, nodes, group))

    def prepare_requests(self) -> None:
        for m in self.messages.values():
            specs = m.get("transmissions")
            if specs is None:
                specs = [{k: m[k] for k in ("trigger_type", "time_mark", "period_cycles", "phase",
                                           "cycle_code", "window_length_ntu", "deadline_ntu", "group_id") if k in m}]
            if not isinstance(specs, list) or not specs or any(not isinstance(x, dict) for x in specs):
                raise ValueError("transmissions must be a nonempty list of objects")
            for index, raw in enumerate(specs):
                inherited = {k: m[k] for k in ("trigger_type", "period_cycles", "phase", "cycle_code",
                                               "window_length_ntu", "deadline_ntu", "group_id") if k in m}
                if "cycle_code" in raw:
                    inherited.pop("period_cycles", None)
                    inherited.pop("phase", None)
                if "period_cycles" in raw or "phase" in raw:
                    inherited.pop("cycle_code", None)
                s = copy.deepcopy({**inherited, **raw})
                alias = trigger_type(s.get("trigger_type", "single"))
                if alias not in TX_TYPES:
                    raise ValueError(f"{m['name']}: transmission requires single, arbi, or merged")
                p, phase, cycles = self.condition(s)
                fixed = s.get("time_mark") is not None
                tm, label = self.mark(s["time_mark"]) if fixed else (None, None)
                self.requests.append(dict(message=m, spec=s, alias=alias, period=p, phase=phase,
                                          cycles=cycles, mark=tm, label=label, fixed=fixed,
                                          group_id=s.get("group_id"), window_id=f"{m['name']}_{index}"))
        for nid, specs in self.config.get("node_triggers", {}).items():
            if nid not in self.nodes:
                raise ValueError(f"Unknown node_triggers node {nid}")
            for s in specs:
                alias = trigger_type(s["trigger_type"])
                if alias not in CONTROL_TYPES | {"end"}:
                    raise ValueError("node_triggers only accepts control or padding entries")
                if alias.startswith("ref") and self.nodes[nid]["role"] == "slave":
                    self.issue("SLAVE_REF", "A slave cannot configure reference transmission", node_id=nid)
                if alias.endswith("_gap") and not self.net["gap"]["enabled"]:
                    self.issue("GAP_DISABLED", "Gap trigger configured while Gap is disabled", node_id=nid)
                if alias == "end":
                    tm, label = self.mark(s["time_mark"]) if "time_mark" in s else (0, None)
                    self.triggers[nid].append(Trigger(nid, alias, tm, time_label=label, state="padding"))
                else:
                    raise ValueError("Reference and Watch entries are generated from network.reference; do not duplicate them")

    def place_requests(self) -> None:
        groups = {g["group_id"]: copy.deepcopy(g) for g in self.config.get("arbitration_groups", [])}
        if len(groups) != len(self.config.get("arbitration_groups", [])):
            raise ValueError("Duplicate arbitration group_id")
        for r in self.requests:
            if r["alias"] in {"merged", "arbi"} and not r["group_id"]:
                if r["alias"] == "merged":
                    raise ValueError("Merged triggers require a group_id")
                gid = r["window_id"] + "_arbi"
                r["group_id"] = gid
                groups[gid] = dict(group_id=gid, kind="arbitration", start_ntu=r["mark"],
                                   length_ntu=r["spec"].get("window_length_ntu", r["message"]["frame_time_ntu"] + self.txew + self.guard))
        members = defaultdict(list)
        for r in self.requests:
            if r["group_id"]:
                if r["group_id"] not in groups or r["alias"] == "single":
                    raise ValueError(f"Invalid arbitration group for {r['message']['name']}")
                members[r["group_id"]].append(r)
        tasks = []
        for gid, rs in members.items():
            g = groups[gid]
            kind = g.get("kind", "merged")
            if kind not in {"merged", "arbitration"}:
                raise ValueError("Group kind must be merged or arbitration")
            if kind == "arbitration" and any(r["alias"] == "merged" for r in rs):
                raise ValueError("A merged trigger requires a merged group")
            cycles = sorted({c for r in rs for c in r["cycles"]})
            budget = max(sum(r["message"]["frame_time_ntu"] + self.guard for r in rs if c in r["cycles"])
                         + self.txew for c in cycles)
            start_value = g.get("start_ntu")
            start = self.mark(start_value)[0] if start_value is not None else None
            if start is None and any(r["fixed"] for r in rs):
                start = min(r["mark"] for r in rs if r["fixed"])
            end_value = g.get("end_ntu")
            end = self.mark(end_value)[0] if end_value is not None else None
            length = self.integer(g.get("length_ntu", (end - start) if end is not None and start is not None else budget), "group length", 1)
            if start is None and end is not None:
                start = end - length
            if end is not None and end - start != length:
                raise ValueError(f"{gid}: end_ntu disagrees with start_ntu + length_ntu")
            deadline = min([self.net["reference"]["tx_mark_ntu"]] +
                           [self.integer(r["spec"]["deadline_ntu"], "deadline") for r in rs if "deadline_ntu" in r["spec"]])
            tasks.append(dict(group=g, requests=rs, cycles=cycles, budget=budget, start=start,
                              length=length, kind=kind, deadline=deadline))
        for r in self.requests:
            if not r["group_id"]:
                minimum = r["message"]["frame_time_ntu"] + self.txew + self.guard
                length = self.integer(r["spec"].get("window_length_ntu", minimum), "window length", 1)
                deadline = self.integer(r["spec"].get("deadline_ntu", self.net["reference"]["tx_mark_ntu"]), "deadline")
                tasks.append(dict(requests=[r], cycles=r["cycles"], start=r["mark"], length=length,
                                  budget=minimum, kind="exclusive", deadline=deadline, group=None))
        tasks.sort(key=lambda t: (t["start"] is None, t["deadline"], t["start"] or 0,
                                 t["requests"][0]["window_id"]))
        for task in tasks:
            rs, g = task["requests"], task["group"]
            wid = g["group_id"] if g else rs[0]["window_id"]
            try:
                start = task["start"]
                if start is None:
                    start = self.first_fit(task["length"], task["cycles"], task["deadline"])
                end = start + task["length"]
                if task["length"] < task["budget"]:
                    self.issue("WINDOW_BUDGET", "Window is smaller than its conservative reservation budget", window_id=wid,
                               required_ntu=task["budget"], allocated_ntu=task["length"])
                if end > task["deadline"]:
                    self.issue("DEADLINE", "Window exceeds its deadline", window_id=wid, end_ntu=end, deadline_ntu=task["deadline"])
                self.reserve(wid, start, end, task["cycles"], task["kind"],
                             sorted({r["message"]["name"] for r in rs}), g["group_id"] if g else None)
                for rank, r in enumerate(rs):
                    if r["mark"] is None:
                        r["mark"] = start + rank * (self.txew + self.guard)
                    if not start <= r["mark"] < end or r["mark"] + self.txew > end:
                        self.issue("GROUP_MARK", "Transmission trigger is outside its group window", message_name=r["message"]["name"], time_mark_ntu=r["mark"], window_id=wid)
                    r.update(window_start=start, window_end=end)
                    m = r["message"]
                    self.triggers[m["sender"]].append(Trigger(m["sender"], r["alias"], r["mark"],
                        r["period"], r["phase"], m["tx_mnr"], "tx_buffer", m["name"], m["can_id"],
                        wid, r["group_id"], r["label"]))
                    r["spec"].update(time_mark=r["mark"], period_cycles=r["period"], phase=r["phase"])
            except ValueError as exc:
                self.issue("UNSCHEDULABLE", str(exc), window_id=wid, cycles=task["cycles"])
        for m in self.messages.values():
            m["transmissions"] = [r["spec"] for r in self.requests if r["message"] is m]

    def generate_rx(self) -> None:
        for m in self.messages.values():
            rs = [r for r in self.requests if r["message"] is m and "window_start" in r]
            for recipient in m["receivers"]:
                nid = recipient["node_id"]
                if not isinstance(recipient.get("check_reception", False), bool):
                    raise ValueError("check_reception must be a Boolean")
                checks = recipient.get("rx_checks", [])
                if not isinstance(checks, list) or any(not isinstance(x, dict) for x in checks):
                    raise ValueError("rx_checks must be a list of objects")
                if recipient.get("check_reception", False) and not checks:
                    for r in rs:
                        if r["alias"] != "single":
                            self.issue("RX_ARBITRATION", "Automatic Rx checks require exclusive transmissions", node_id=nid, message_name=m["name"])
                            continue
                        tm = r["mark"] + self.txew + m["frame_time_ntu"] + self.delay + 1
                        occupied = {t.time_mark_ntu for t in self.triggers[nid]
                                    if t.period_cycles is None or any(c % t.period_cycles == t.phase for c in r["cycles"])}
                        while tm in occupied:
                            tm += self.net["min_trigger_spacing_ntu"]
                        checks.append(dict(time_mark=tm, period_cycles=r["period"], phase=r["phase"]))
                    recipient["rx_checks"] = checks
                for check in checks:
                    p, phase, cycles = self.condition(check)
                    tm, label = self.mark(check["time_mark"])
                    resource = "rx_std_filter" if m["frame_format"] == "standard" else "rx_ext_filter"
                    self.triggers[nid].append(Trigger(nid, "rx", tm, p, phase, recipient["filter_mnr"],
                                                     resource, m["name"], m["can_id"], time_label=label))
                    for c in cycles:
                        txs = [r for r in rs if c in r["cycles"] and r["alias"] == "single"]
                        completions = [r["mark"] + self.txew + m["frame_time_ntu"] + self.delay for r in txs]
                        if not any(end < tm for end in completions):
                            self.issue("RX_TOO_EARLY", "Rx check has no completed exclusive target transmission", node_id=nid,
                                       message_name=m["name"], cycle=c, time_mark_ntu=tm,
                                       earliest_safe_mark_ntu=min(completions) + 1 if completions else None)

    def controls(self) -> None:
        ref, gap = self.net["reference"], self.net["gap"]
        labels = self.net.get("time_marks", {})
        ref_label = next((k for k,v in labels.items() if number(v) == ref["tx_mark_ntu"]), None)
        watch_label = next((k for k,v in labels.items() if number(v) == ref["watch_mark_ntu"]), None)
        for nid, node in self.nodes.items():
            if node["role"] == "master":
                self.triggers[nid].append(Trigger(nid, "ref", ref["tx_mark_ntu"], time_label=ref_label, state="normal"))
                if gap["enabled"]:
                    self.triggers[nid].append(Trigger(nid, "ref_gap", gap["ref_gap_mark_ntu"], state="gap"))
            self.triggers[nid].append(Trigger(nid, "watch", ref["watch_mark_ntu"], time_label=watch_label, state="normal"))
            if gap["enabled"]:
                self.triggers[nid].append(Trigger(nid, "watch_gap", gap["watch_gap_mark_ntu"], state="gap"))

    def applicable(self, t: Trigger, cycle: int) -> bool:
        if t.alias == "end":
            return False
        if t.state == "gap" and cycle not in self.gap_cycles:
            return False
        if t.state == "normal" and cycle in self.gap_cycles:
            return False
        return t.period_cycles is None or cycle % t.period_cycles == t.phase

    def validate_triggers(self) -> None:
        for nid, ts in self.triggers.items():
            ts.sort(key=lambda t: (t.time_mark_ntu is None, t.time_mark_ntu or 0, TYPES[t.alias][0], t.message_name or "", t.phase or 0))
            for index, t in enumerate(ts):
                t.trigger_index = index
                t.active_cycles = [c for c in range(self.cycles) if self.applicable(t, c)]
                if self.mode == "m_ttcan" and t.time_mark_ntu is not None and t.time_mark_ntu > 65535:
                    self.issue("TIME_MARK_WIDTH", "Time Mark exceeds the 16-bit hardware field", node_id=nid, time_mark_ntu=t.time_mark_ntu)
                if t.alias in TX_TYPES | {"rx"} and not self.ref_duration < t.time_mark_ntu < self.net["reference"]["tx_mark_ntu"]:
                    self.issue("TRIGGER_BOUNDS", "Data trigger must follow the reference frame and precede the next reference request", node_id=nid, time_mark_ntu=t.time_mark_ntu)
            for c in range(self.cycles):
                active = [t for t in ts if self.applicable(t, c)]
                times = defaultdict(list)
                for t in active:
                    times[t.time_mark_ntu].append(t)
                for mark, same in times.items():
                    if len(same) > 1:
                        self.issue("TRIGGER_COLLISION", "Multiple triggers are active at the same node, cycle, and Time Mark", node_id=nid,
                                   cycle=c, time_mark_ntu=mark, triggers=[t.record() for t in same])
                for before, after in zip(active, active[1:]):
                    distance = after.time_mark_ntu - before.time_mark_ntu
                    if 0 < distance < self.net["min_trigger_spacing_ntu"]:
                        self.issue("TRIGGER_SPACING", "Active triggers violate the configured memory-access spacing", node_id=nid, cycle=c, time_mark_ntu=after.time_mark_ntu)
                for t in ts:
                    if t.alias == "end" and t.time_mark_ntu is not None:
                        watch = self.net["gap"]["watch_gap_mark_ntu"] if c in self.gap_cycles else self.net["reference"]["watch_mark_ntu"]
                        if t.time_mark_ntu <= watch:
                            self.issue("END_BEFORE_WATCH", "End_of_List can be scanned before Watch", node_id=nid, cycle=c, time_mark_ntu=t.time_mark_ntu)
                            break
                open_group = None
                previous_rx = 0
                for t in active:
                    if t.alias in {"single", "arbi"}:
                        for other in active:
                            if other is not t and t.time_mark_ntu < other.time_mark_ntu < t.time_mark_ntu + self.txew:
                                self.issue("TX_ENABLE_INTRUSION", "An active trigger falls inside a Tx Enable Window", node_id=nid, cycle=c, time_mark_ntu=other.time_mark_ntu)
                    if t.alias in TX_TYPES:
                        if open_group and (t.alias not in {"merged", "arbi"} or t.group_id != open_group):
                            self.issue("MERGED_NOT_CLOSED", "Merged sequence is interrupted by an incompatible transmission", node_id=nid, cycle=c, group_id=open_group)
                        if t.alias == "merged":
                            open_group = t.group_id
                        elif t.alias == "arbi":
                            open_group = None
                    if t.alias == "rx":
                        self.validate_last_filter(nid, c, previous_rx, t)
                        previous_rx = t.time_mark_ntu
                if open_group:
                    self.issue("MERGED_NOT_CLOSED", "Merged sequence has no final Arbitration trigger", node_id=nid, cycle=c, group_id=open_group)
            capacity = self.nodes[nid]["trigger_capacity"]
            used = len(ts)
            if used > capacity:
                self.issue("TRIGGER_CAPACITY", "Trigger Memory capacity is insufficient", node_id=nid, required=used, capacity=capacity)
            for i in range(used, capacity):
                ts.append(Trigger(nid, "end", None, trigger_index=i, state="padding"))
            resource = {}
            for kind, cap in zip(("tx_buffer", "rx_std_filter", "rx_ext_filter"), CAPS[:3]):
                n = len(self.used[(nid, kind)])
                resource[kind] = dict(used=n, capacity=self.nodes[nid][cap], remaining=self.nodes[nid][cap] - n)
            resource["trigger_memory"] = dict(used=used, capacity=capacity, remaining=capacity-used)
            resource["expected_data_tx_triggers_per_matrix"] = sum(len(t.active_cycles) for t in ts if t.alias in TX_TYPES)
            self.resources[nid] = resource

    def validate_last_filter(self, nid: str, cycle: int, previous_rx: int, t: Trigger) -> None:
        completions = []
        for r in self.requests:
            m = r["message"]
            if cycle not in r["cycles"] or "window_start" not in r or not any(x["node_id"] == nid for x in m["receivers"]):
                continue
            if r["alias"] != "single":
                if r["window_start"] < t.time_mark_ntu and r["window_end"] > previous_rx:
                    self.issue("RX_ARBITRATION_UNCERTAIN", "Accepted arbitration traffic may overwrite the last-filter result before Rx check", "warning", node_id=nid, cycle=cycle, message_name=t.message_name)
                continue
            earliest = r["mark"] + ceil_div(m["unstuffed_bits"] * 10**9, self.bitrate * self.ntu_ns)
            latest = r["mark"] + m["frame_time_ntu"] + self.delay + self.txew
            if latest > previous_rx and earliest < t.time_mark_ntu:
                completions.append((earliest, latest, m["name"]))
        if completions:
            target = [x for x in completions if x[2] == t.message_name and previous_rx < x[0] and x[1] < t.time_mark_ntu]
            if not target:
                self.issue("RX_LAST_FILTER", "No target is guaranteed to complete after the preceding check and before this check", node_id=nid, cycle=cycle, message_name=t.message_name, time_mark_ntu=t.time_mark_ntu)
            else:
                guaranteed = max(target)
                competitors = [x for x in completions if x[2] != t.message_name and x[1] >= guaranteed[0]]
                if competitors:
                    certain = any(x[0] > guaranteed[1] and x[1] < t.time_mark_ntu for x in competitors)
                    self.issue("RX_LAST_FILTER" if certain else "RX_FILTER_ORDER_UNCERTAIN",
                        "Another accepted message can replace the target's last-filter result before this check",
                        node_id=nid, cycle=cycle, message_name=t.message_name,
                        interfering_messages=[x[2] for x in competitors], time_mark_ntu=t.time_mark_ntu)
        else:
            self.issue("RX_FILTER_CLEARED", "No accepted target has completed since the preceding Rx check or cycle start", node_id=nid, cycle=cycle, message_name=t.message_name)

    def finish_model(self) -> None:
        refid = self.net["reference"]["can_id"]
        for c in range(self.cycles):
            self.windows.append(Window("reference", c, 0, self.ref_duration, "reference", ["Reference"], [self.net["actual_master"]]))
            occupied = sorted((w for w in self.windows if w.cycle == c), key=lambda w: w.start_ntu)
            end = 0
            for w in occupied:
                if w.start_ntu > end:
                    self.windows.append(Window(f"free_{c}_{end}", c, end, w.start_ntu, "free"))
                end = max(end, w.end_ntu)
            if end < self.basic:
                self.windows.append(Window(f"free_{c}_{end}", c, end, self.basic, "free"))
        self.windows.sort(key=lambda w: (w.cycle, w.start_ntu, w.window_id))
        boundaries = sorted({0, self.basic} | {x for w in self.windows for x in (w.start_ntu, w.end_ntu)} |
                            {t.time_mark_ntu for ts in self.triggers.values() for t in ts
                             if t.time_mark_ntu is not None and 0 <= t.time_mark_ntu <= self.basic})
        self.matrix = dict(columns=[dict(column_index=i, start_ntu=a, end_ntu=b)
                                   for i, (a, b) in enumerate(zip(boundaries, boundaries[1:]))], rows=[])
        for c in range(self.cycles):
            cells = []
            ws = [w for w in self.windows if w.cycle == c]
            for col in self.matrix["columns"]:
                w = next(w for w in ws if w.start_ntu <= col["start_ntu"] and w.end_ntu >= col["end_ntu"])
                cells.append(dict(**col, window_id=w.window_id, kind=w.kind, messages=w.messages, nodes=w.nodes, group_id=w.group_id))
            self.matrix["rows"].append(dict(cycle=c, cells=cells))
        origin = 0
        for matrix_index in range(self.net["expand_matrices"]):
            for c in range(self.cycles):
                self.origins.append(origin)
                for w in (w for w in self.windows if w.cycle == c):
                    self.events.append(dict(matrix_index=matrix_index, cycle=c, node_id=",".join(w.nodes),
                        event_kind=w.kind, status="candidate_window" if w.kind in {"merged", "arbitration"} else "planned_window",
                        start_ntu=origin+w.start_ntu, end_ntu=origin+w.end_ntu, offset_ntu=w.start_ntu,
                        window_id=w.window_id, message_name=",".join(w.messages), trigger_index=None, type_name=None,
                        mnr=None, can_id=refid if w.kind == "reference" else None))
                for nid, ts in self.triggers.items():
                    for t in ts:
                        if c not in t.active_cycles:
                            continue
                        status = "planned_check" if t.alias == "rx" else "request_opportunity"
                        if t.alias.startswith("watch"):
                            status = "inactive_timeout_monitor"
                        elif t.alias.startswith("ref"):
                            status = "planned_reference_request" if nid == self.net["actual_master"] else "standby_reference_configuration"
                        elif t.alias in {"arbi", "merged"}:
                            status = "arbitration_candidate"
                        self.events.append(dict(matrix_index=matrix_index, cycle=c, node_id=nid,
                            event_kind="trigger", status=status, start_ntu=origin+t.time_mark_ntu,
                            end_ntu=origin+t.time_mark_ntu, offset_ntu=t.time_mark_ntu,
                            window_id=t.window_id, message_name=t.message_name, trigger_index=t.trigger_index,
                            type_name=TYPES[t.alias][1], mnr=t.mnr, can_id=t.can_id))
                origin += self.basic + (self.net["gap"]["duration_ntu"] if c in self.gap_cycles else 0)
        self.events.sort(key=lambda e: (e["start_ntu"], e["event_kind"], e["node_id"], e["message_name"] or ""))
        self.matrix_duration = self.basic*self.cycles + len(self.gap_cycles)*self.net["gap"].get("duration_ntu", 0)

    def compile(self) -> "Scheduler":
        try:
            self.normalize()
            self.prepare_requests()
            self.place_requests()
            self.generate_rx()
            self.controls()
            self.validate_triggers()
            if self.valid:
                self.finish_model()
        except (ValueError, KeyError, TypeError) as exc:
            self.issue("CONFIGURATION", str(exc))
        return self

    def report(self) -> dict[str, Any]:
        return dict(valid=self.valid, errors=sum(i.severity == "error" for i in self.issues),
                    warnings=sum(i.severity == "warning" for i in self.issues),
                    issues=[asdict(i) for i in self.issues], resources=self.resources,
                    timing_model="Static Level 1 reservation model; no bus arbitration or clock synchronization simulation",
                    hardware_export=False, units=dict(time="NTU", ntu_ns=getattr(self, "ntu_ns", None)))

    def payload(self) -> dict[str, Any]:
        return dict(config=self.config, windows=[asdict(w) for w in self.windows], matrix=self.matrix,
                    triggers={n:[t.record() for t in ts] for n, ts in self.triggers.items()},
                    message_maps=dict(self.maps), events=self.events, report=self.report(),
                    matrix_duration_ntu=self.matrix_duration, cycle_origins_ntu=self.origins)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(k for r in records for k in r))
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        for r in records:
            writer.writerow({k: json.dumps(v, ensure_ascii=True) if isinstance(v, (dict, list)) else v for k,v in r.items()})


def export_schedule(s: Scheduler, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "validation_report.json", s.report())
    if not s.valid:
        return [directory / "validation_report.json"]
    data = s.payload()
    write_json(directory / "effective_config.json", s.config)
    write_csv(directory / "global_timeline.csv", s.events)
    write_json(directory / "system_matrix.json", s.matrix)
    write_csv(directory / "system_matrix.csv", [dict(cycle=r["cycle"], **cell) for r in s.matrix["rows"] for cell in r["cells"]])
    for nid in s.nodes:
        for label, records in (("message_map", s.maps[nid]), ("trigger_list", data["triggers"][nid])):
            write_json(directory / "nodes" / f"{nid}_{label}.json", records)
            write_csv(directory / "nodes" / f"{nid}_{label}.csv", records)
    template = (ROOT / "assets" / "report_template.html").read_text(encoding="utf-8")
    embedded = json.dumps(data, ensure_ascii=True).replace("<", "\\u003c")
    (directory / "schedule.html").write_text(template.replace("__SCHEDULE_DATA__", embedded), encoding="utf-8")
    return sorted(p for p in directory.rglob("*") if p.is_file())


def scan_english(root: Path) -> list[str]:
    """Report CJK content in deliverables; designated Chinese manuals are exempt."""
    failures = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.name.endswith("_zh.md"):
            continue
        if CJK.search(path.name):
            failures.append(str(path.relative_to(root)) + ": non-English filename")
        if path.suffix not in {".py", ".json", ".csv", ".html", ".md", ".js", ".cjs", ".css", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8")
        if CJK.search(text):
            failures.append(str(path.relative_to(root)) + ": literal CJK text")
        if path.suffix == ".json":
            try:
                decoded = json.dumps(json.loads(text), ensure_ascii=False)
                if CJK.search(decoded) and not CJK.search(text):
                    failures.append(str(path.relative_to(root)) + ": escaped CJK text")
            except json.JSONDecodeError:
                failures.append(str(path.relative_to(root)) + ": invalid JSON")
        elif re.search(r"\\u(?:[0-9a-fA-F]{4})", text):
            for token in re.findall(r"\\u([0-9a-fA-F]{4})", text):
                if CJK.search(chr(int(token,16))):
                    failures.append(str(path.relative_to(root)) + ": escaped CJK text")
                    break
    return failures


def interactive_config() -> dict[str, Any]:
    count = number(input("Node count: "))
    if count < 1:
        raise ValueError("Node count must be positive")
    nodes = []
    for i in range(count):
        nid = input(f"Node {i} ID [Node{i}]: ").strip() or f"Node{i}"
        role = input(f"{nid} role (master/slave): ").strip()
        n = dict(node_id=nid, role=role)
        for cap, default in DEFAULT_CAPS.items():
            n[cap] = number(input(f"{nid} {cap} [{default}]: ").strip() or str(default))
        if role == "master":
            n["master_priority"] = number(input(f"{nid} master priority [0]: ").strip() or "0")
        nodes.append(n)
    masters = [n for n in nodes if n["role"] == "master"]
    if not masters:
        raise ValueError("At least one master is required")
    actual = input(f"Actual master [{masters[0]['node_id']}]: ").strip() or masters[0]["node_id"]
    messages = []
    for i, node in enumerate(nodes):
        messages.append(dict(name=f"Message{i}", can_id=hex(0x100+i), sender=node["node_id"],
                             dlc=8, period_cycles=(1,2,4)[i%3], phase=0,
                             receivers=[dict(node_id=n["node_id"], check_reception=True) for n in nodes if n is not node]))
    basic = max(2000, (count+1)*300)
    priority = next(n["master_priority"] for n in masters if n["node_id"] == actual)
    return dict(schema_version=1, metadata=dict(source="Generated interactive example", assumptions=["Message identities, periods, and timing budgets are generated examples"]),
                network=dict(actual_master=actual, matrix_cycles=4, basic_cycle_ntu=basic,
                             reference=dict(can_id=hex(0x80|priority))), nodes=nodes, messages=messages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile a static TTCAN schedule and offline HTML report")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--config", type=Path, help="Load an English JSON configuration")
    choice.add_argument("--interactive", action="store_true", help="Generate a configuration from interactive node inputs")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Output directory (default: output)")
    parser.add_argument("--validate-only", action="store_true", help="Write only validation_report.json and return nonzero for errors")
    parser.add_argument("--check-english", action="store_true", help="Scan project deliverables for CJK text")
    args = parser.parse_args(argv)
    try:
        if CJK.search(str(args.output_dir)):
            raise ValueError("Output directory names must not contain CJK characters")
        if args.check_english:
            failures = scan_english(ROOT)
            for failure in failures:
                print("LANGUAGE ERROR: " + failure)
            print(f"English-only scan: {'FAIL' if failures else 'PASS'}")
            return 1 if failures else 0
        config = interactive_config() if args.interactive else json.loads((args.config or ROOT / "examples" / "nodea_nodeb_demo.json").read_text(encoding="utf-8"))
        s = Scheduler(config).compile()
        if args.validate_only:
            write_json(args.output_dir / "validation_report.json", s.report())
        else:
            export_schedule(s, args.output_dir)
        for issue in s.issues:
            print(f"{issue.severity.upper()} {issue.code}: {issue.message} {json.dumps(issue.context, ensure_ascii=True)}")
        print(f"Validation: {'PASS' if s.valid else 'FAIL'}; errors={s.report()['errors']}; warnings={s.report()['warnings']}")
        if s.valid and not args.validate_only:
            print(f"Schedule: {len(s.nodes)} nodes, {s.cycles} Basic Cycles, {s.matrix_duration} NTU per matrix")
            print(f"Offline report: {args.output_dir / 'schedule.html'}")
        return 0 if s.valid else 1
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR: " + str(exc).encode("ascii", "backslashreplace").decode("ascii"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
