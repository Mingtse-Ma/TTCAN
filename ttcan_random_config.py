#!/usr/bin/env python3
"""Generate reproducible, constrained random TTCAN configurations and reports."""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import math
import os
import random
import secrets
import shlex
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ttcan_scheduler import (CAPS, CJK, NAME, ROOT, Scheduler, ceil_div,
                             encode_cycle_code, export_schedule, frame_bits, number, write_json)

GENERATOR_VERSION = "1.0.0"
DEFAULT_OPTIONS = {
    "schema_version": 1,
    "resource_mode": "custom",
    "controller_version": "3.3.0",
    "node_count": [2, 5],
    "master_count": [1, 2],
    "messages_per_node": [2, 3],
    "matrix_cycles": [4, 8, 16],
    "basic_cycle_ntu": [2400, 5000],
    "bitrate_choices": [500000, 1000000],
    "tx_enable_ntu": [2, 6],
    "guard_ntu": [10, 18],
    "filter_delay_ntu": [1, 6],
    "min_trigger_spacing_ntu": [1, 2],
    "dlc": [0, 8],
    "receiver_count": [1, 2],
    "periods": [1, 2, 4, 8, 16, 32, 64],
    "frame_formats": ["standard", "extended"],
    "extended_probability": 0.30,
    "rx_check_probability": 0.60,
    "explicit_number_probability": 0.50,
    "repeat_probability": 0.30,
    "shared_group_probability": 0.60,
    "window_extra_ntu": [5, 30],
    "window_start_ntu": [0, 65535],
    "window_length_ntu": [1, 65535],
    "target_bus_load": [0.0, 1.0],
    "window_mix": {"single": 0.5, "arbitration": 0.2, "merged": 0.3},
    "node_capacity": {
        "tx_message_capacity": [4, 16],
        "rx_std_filter_capacity": [4, 12],
        "rx_ext_filter_capacity": [4, 12],
        "trigger_capacity": [24, 48],
    },
    "required_features": [],
    "allow_warnings": False,
    "topology": None,
}
RANGES = {
    "node_count": (1, 128), "master_count": (1, 8),
    "messages_per_node": (0, 128), "basic_cycle_ntu": (100, 65535),
    "tx_enable_ntu": (1, 65535), "guard_ntu": (0, 65535),
    "filter_delay_ntu": (0, 65535), "min_trigger_spacing_ntu": (1, 65535),
    "dlc": (0, 8), "receiver_count": (0, 127), "window_extra_ntu": (0, 65535),
    "window_start_ntu": (0, 65535), "window_length_ntu": (1, 65535),
}
FEATURES = {"single", "arbitration", "merged", "rx", "extended", "multiple_receivers",
            "shared_mnr", "shared_time", "multiple_masters", "multi_node_arbitration", "high_load"}
LIMITS = dict(zip(CAPS, (32, 64, 128, 64)))


class GenerationError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def record(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def source_hash() -> str:
    return hashlib.sha256((ROOT / "ttcan_scheduler.py").read_bytes()).hexdigest()


def derive_seed(*parts: Any) -> int:
    return int.from_bytes(hashlib.sha256(canonical(["ttcan-random-v1", *parts])).digest(), "big")


def interval(value: Any, key: str, minimum: int, maximum: int) -> list[int]:
    pair = [value, value] if isinstance(value, int) and not isinstance(value, bool) else value
    if not isinstance(pair, list) or len(pair) != 2:
        raise GenerationError("INVALID_OPTIONS", f"{key} must be an integer or a two-integer range")
    try:
        low, high = map(number, pair)
    except ValueError as exc:
        raise GenerationError("INVALID_OPTIONS", f"{key}: {exc}") from exc
    if not minimum <= low <= high <= maximum:
        raise GenerationError("INVALID_OPTIONS", f"{key} must be within {minimum}..{maximum} and ordered")
    return [low, high]


def probability(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise GenerationError("INVALID_OPTIONS", f"{key} must be in 0..1")
    return float(value)


def read_json_file(path: Path) -> Any:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError("INPUT_FILE", f"Could not read JSON input: {exc}") from exc
    if CJK.search(json.dumps(result, ensure_ascii=False)):
        raise GenerationError("LANGUAGE", "Input JSON must not contain CJK characters")
    return result


def merge_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise GenerationError("INVALID_OPTIONS", "Options must be a JSON object")
    unknown = set(raw) - set(DEFAULT_OPTIONS)
    if unknown:
        raise GenerationError("INVALID_OPTIONS", f"Unknown option fields: {sorted(unknown)}")
    options = copy.deepcopy(DEFAULT_OPTIONS)
    for key, value in raw.items():
        if key in {"node_capacity", "window_mix"}:
            if not isinstance(value, dict) or set(value) - set(options[key]):
                raise GenerationError("INVALID_OPTIONS", f"Invalid {key} object or unknown fields")
            options[key].update(copy.deepcopy(value))
        else:
            options[key] = copy.deepcopy(value)
    return options


def fixed_override(options: dict[str, Any], key: str, value: int | None, supplied: dict[str, Any]) -> None:
    if value is None:
        return
    if key in supplied:
        old = interval(supplied[key], key, *RANGES[key])
        if old[0] == old[1] and old[0] != value:
            raise GenerationError("CONFLICTING_INPUT", f"CLI {key} conflicts with a fixed options value")
    options[key] = [value, value]


def resolve_options(raw: dict[str, Any] | None = None, *, nodes: int | None = None,
                    masters: int | None = None, messages: int | None = None,
                    topology: dict[str, Any] | None = None, allow_warnings: bool = False,
                    required_features: list[str] | None = None) -> dict[str, Any]:
    raw = {} if raw is None else raw
    o = merge_options(raw)
    if CJK.search(json.dumps(o, ensure_ascii=False)):
        raise GenerationError("LANGUAGE", "Generator options must not contain CJK characters")
    if o["schema_version"] != 1:
        raise GenerationError("INVALID_OPTIONS", "Unsupported options schema_version")
    if topology is not None:
        if o["topology"] is not None and o["topology"] != topology:
            raise GenerationError("CONFLICTING_INPUT", "Options topology and nodes-file disagree")
        o["topology"] = copy.deepcopy(topology)
    fixed_override(o, "node_count", nodes, raw)
    fixed_override(o, "master_count", masters, raw)
    fixed_override(o, "messages_per_node", messages, raw)
    if allow_warnings:
        o["allow_warnings"] = True
    if not isinstance(o["allow_warnings"], bool):
        raise GenerationError("INVALID_OPTIONS", "allow_warnings must be Boolean")
    if required_features:
        if not isinstance(o["required_features"], list):
            raise GenerationError("INVALID_OPTIONS", "required_features must be a list")
        o["required_features"] = sorted(set(o["required_features"]) | set(required_features))
    for key, bounds in RANGES.items():
        o[key] = interval(o[key], key, *bounds)
    if o["resource_mode"] not in {"custom", "m_ttcan"}:
        raise GenerationError("INVALID_OPTIONS", "resource_mode must be custom or m_ttcan")
    if o["resource_mode"] == "m_ttcan" and o["controller_version"] != "3.3.0":
        raise GenerationError("INVALID_OPTIONS", "m_ttcan requires controller_version 3.3.0")
    for key in CAPS:
        maximum = LIMITS[key] if o["resource_mode"] == "m_ttcan" else 65536
        o["node_capacity"][key] = interval(o["node_capacity"][key], key, 0, maximum)
    for key in ("extended_probability", "rx_check_probability", "explicit_number_probability", "repeat_probability", "shared_group_probability"):
        o[key] = probability(o[key], key)
    weights = o["window_mix"]
    for key, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise GenerationError("INVALID_OPTIONS", f"window_mix.{key} must be nonnegative")
    if sum(weights.values()) <= 0:
        raise GenerationError("INVALID_OPTIONS", "At least one window_mix weight must be positive")
    for key in ("matrix_cycles", "periods", "bitrate_choices"):
        if not isinstance(o[key], list) or not o[key]:
            raise GenerationError("INVALID_OPTIONS", f"{key} must be a nonempty list")
        try:
            o[key] = sorted(set(number(x) for x in o[key]))
        except ValueError as exc:
            raise GenerationError("INVALID_OPTIONS", f"Invalid {key}: {exc}") from exc
    if any(x < 1 or x > 64 for x in o["matrix_cycles"]):
        raise GenerationError("INVALID_OPTIONS", "matrix_cycles choices must be in 1..64")
    if any(x < 1 or x > 64 for x in o["periods"]):
        raise GenerationError("INVALID_OPTIONS", "period choices must be in 1..64")
    if o["resource_mode"] == "m_ttcan" and any(x & (x - 1) for x in o["matrix_cycles"]):
        raise GenerationError("INVALID_OPTIONS", "Hardware matrix length must be a power of two")
    if any(b <= 0 or 10**9 % b for b in o["bitrate_choices"]):
        raise GenerationError("INVALID_OPTIONS", "Each bitrate must produce an integer NTU in nanoseconds")
    if any(not any(m % p == 0 and (o["resource_mode"] != "m_ttcan" or encode_cycle_code(p, 0) is not None)
                   for p in o["periods"]) for m in o["matrix_cycles"]):
        raise GenerationError("INVALID_OPTIONS", "Every matrix length needs at least one compatible period")
    if not isinstance(o["frame_formats"], list) or not o["frame_formats"] or set(o["frame_formats"]) - {"standard", "extended"}:
        raise GenerationError("INVALID_OPTIONS", "frame_formats must contain standard and/or extended")
    o["frame_formats"] = sorted(set(o["frame_formats"]))
    if not isinstance(o["required_features"], list) or set(o["required_features"]) - FEATURES:
        raise GenerationError("INVALID_OPTIONS", f"required_features must be selected from {sorted(FEATURES)}")
    o["required_features"] = sorted(set(o["required_features"]))
    required = set(o["required_features"])
    if "extended" in required and "extended" not in o["frame_formats"]:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Extended frames are required but excluded by frame_formats")
    if "rx" in required and o["rx_check_probability"] == 0:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Rx is required but rx_check_probability is zero")
    if "multiple_receivers" in required and (o["receiver_count"][1] < 2 or o["node_count"][1] < 3):
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Multiple receivers require at least three nodes and receiver_count >= 2")
    if "multiple_masters" in required and o["master_count"][1] < 2:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Multiple masters are required but excluded by master_count")
    if "shared_mnr" in required and o["repeat_probability"] == 0:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Shared MNR requires repeat_probability greater than zero")
    if "multi_node_arbitration" in required and (o["shared_group_probability"] == 0 or o["node_count"][1] < 2 or weights["arbitration"] == 0):
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Multi-node arbitration requires at least two nodes, arbitration weight, and shared-group probability")
    for feature in ("single", "arbitration", "merged"):
        if feature in o["required_features"] and weights[feature] == 0:
            raise GenerationError("INFEASIBLE_CONSTRAINT", f"Required feature {feature} has zero window weight")
    load = o["target_bus_load"]
    if not isinstance(load, list) or len(load) != 2:
        raise GenerationError("INVALID_OPTIONS", "target_bus_load must be a two-number range")
    o["target_bus_load"] = [probability(x, "target_bus_load") for x in load]
    if o["target_bus_load"][0] > o["target_bus_load"][1]:
        raise GenerationError("INVALID_OPTIONS", "target_bus_load must be ordered")
    if "high_load" in required and o["target_bus_load"][1] < 0.70:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "The requested load range excludes the high_load threshold")
    topology_value = o["topology"]
    if topology_value is not None:
        validate_topology(topology_value, o, nodes, masters, raw)
    else:
        if o["master_count"][0] > min(8, o["node_count"][1]):
            raise GenerationError("INFEASIBLE_CONSTRAINT", "Minimum master count exceeds maximum node count")
        if o["receiver_count"][0] > o["node_count"][1] - 1:
            raise GenerationError("INFEASIBLE_CONSTRAINT", "Receiver minimum exceeds available peers")
    if "multiple_masters" in required and o["master_count"][1] < 2:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Fixed topology excludes multiple masters")
    if "multiple_receivers" in required and o["node_count"][1] < 3:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Fixed topology excludes multiple receivers")
    if o["messages_per_node"][0] > o["node_capacity"]["tx_message_capacity"][1] and topology_value is None:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Message minimum exceeds the Tx capacity upper bound")
    return o


def validate_topology(topology: Any, options: dict[str, Any], nodes_arg: int | None,
                      masters_arg: int | None, supplied: dict[str, Any]) -> None:
    if not isinstance(topology, dict) or set(topology) - {"nodes", "network", "node_defaults"}:
        raise GenerationError("INVALID_TOPOLOGY", "Topology must contain only nodes, network, and node_defaults")
    nodes = topology.get("nodes")
    if not isinstance(nodes, list) or not nodes or any(not isinstance(n, dict) for n in nodes):
        raise GenerationError("INVALID_TOPOLOGY", "Topology nodes must be a nonempty array of objects")
    network, defaults = topology.get("network", {}), topology.get("node_defaults", {})
    if not isinstance(network, dict) or set(network) - {"actual_master"} or not isinstance(defaults, dict) or set(defaults) - set(CAPS):
        raise GenerationError("INVALID_TOPOLOGY", "Topology network accepts only actual_master; node_defaults accepts capacities")
    if nodes_arg is not None and nodes_arg != len(nodes):
        raise GenerationError("CONFLICTING_INPUT", "--nodes differs from the fixed topology")
    names, priorities = set(), set()
    master_ids = []
    for n in nodes:
        nid = n.get("node_id", "")
        if not isinstance(nid, str) or not NAME.fullmatch(nid) or nid in names:
            raise GenerationError("INVALID_TOPOLOGY", f"Invalid or repeated node_id: {nid!r}")
        names.add(nid)
        if n.get("role") not in ("master", "slave"):
            raise GenerationError("INVALID_TOPOLOGY", f"Invalid role for {nid}")
        if n["role"] == "master":
            master_ids.append(nid)
            if "master_priority" in n:
                p = number(n["master_priority"])
                if not 0 <= p <= 7 or p in priorities:
                    raise GenerationError("INVALID_TOPOLOGY", "Master priorities must be unique in 0..7")
                priorities.add(p)
        for cap in CAPS:
            value = n.get(cap, defaults.get(cap))
            if value is not None:
                upper = LIMITS[cap] if options["resource_mode"] == "m_ttcan" else 65536
                if not 0 <= number(value) <= upper:
                    raise GenerationError("INVALID_TOPOLOGY", f"{nid}: invalid {cap}")
        if number(n.get("tx_message_capacity", defaults.get("tx_message_capacity", 65536))) < options["messages_per_node"][0]:
            raise GenerationError("INFEASIBLE_CONSTRAINT", f"{nid}: fixed Tx capacity is below the message minimum")
    if not 1 <= len(master_ids) <= 8:
        raise GenerationError("INVALID_TOPOLOGY", "Topology needs 1..8 candidate masters")
    if masters_arg is not None and masters_arg != len(master_ids):
        raise GenerationError("CONFLICTING_INPUT", "--masters differs from the fixed topology")
    actual = network.get("actual_master")
    if actual is not None and actual not in master_ids:
        raise GenerationError("INVALID_TOPOLOGY", "actual_master must be a candidate master")
    for key, count in (("node_count", len(nodes)), ("master_count", len(master_ids))):
        if key in supplied:
            bounds = interval(supplied[key], key, *RANGES[key])
            if not bounds[0] <= count <= bounds[1]:
                raise GenerationError("CONFLICTING_INPUT", f"Topology {key} is outside the explicit options range")
        options[key] = [count, count]
    if options["receiver_count"][0] > len(nodes) - 1:
        raise GenerationError("INFEASIBLE_CONSTRAINT", "Receiver minimum exceeds topology peers")


def randrange(rng: random.Random, bounds: list[int]) -> int:
    return rng.randint(bounds[0], bounds[1])


def selected(rng: random.Random, probability_value: float) -> bool:
    return rng.random() < probability_value


def resource_slot(rng: random.Random, used: dict[tuple[str, str], set[int]], node: str,
                  resource: str, capacity: int, explicit_probability: float) -> int | None:
    slots = [x for x in range(capacity) if x not in used[(node, resource)]]
    if not slots:
        raise GenerationError("RESOURCE_EXHAUSTED", f"{node}: {resource} capacity {capacity} is exhausted", node=node, resource=resource, capacity=capacity)
    slot = rng.choice(slots)
    used[(node, resource)].add(slot)
    return slot if selected(rng, explicit_probability) else None


def build_candidate(options: dict[str, Any], rng: random.Random, identity: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Construct legal-by-design resources and traffic; the scheduler remains authoritative."""
    o = options
    fixed = o["topology"]
    if fixed:
        nodes = copy.deepcopy(fixed["nodes"])
        defaults = fixed.get("node_defaults", {})
        for n in nodes:
            for cap, value in defaults.items():
                n.setdefault(cap, value)
        count = len(nodes)
    else:
        possible_counts = [x for x in range(o["node_count"][0], o["node_count"][1] + 1)
                           if x >= o["master_count"][0] and x > o["receiver_count"][0]]
        if not possible_counts:
            raise GenerationError("INFEASIBLE_CONSTRAINT", "No node count satisfies master and receiver minima")
        count = rng.choice(possible_counts)
        num_masters = rng.randint(o["master_count"][0], min(o["master_count"][1], count, 8))
        master_positions = set(rng.sample(range(count), num_masters))
        nodes = [dict(node_id=f"Node{i}", node_name=f"Node{i}", role="master" if i in master_positions else "slave") for i in range(count)]
    for n in nodes:
        n.setdefault("node_name", n["node_id"])
    existing_priorities = {number(n["master_priority"]) for n in nodes if n["role"] == "master" and "master_priority" in n}
    available = [p for p in range(8) if p not in existing_priorities]
    rng.shuffle(available)
    for n in nodes:
        if n["role"] == "master" and "master_priority" not in n:
            n["master_priority"] = available.pop()
    candidates = [n for n in nodes if n["role"] == "master"]
    fixed_actual = fixed.get("network", {}).get("actual_master") if fixed else None
    actual = next((n for n in candidates if n["node_id"] == fixed_actual), None) if fixed_actual else rng.choice(candidates)
    counts = {n["node_id"]: randrange(rng, o["messages_per_node"]) for n in nodes}
    for n in nodes:
        for cap in CAPS:
            if cap not in n:
                low, high = o["node_capacity"][cap]
                if cap == "tx_message_capacity":
                    low = max(low, counts[n["node_id"]])
                if low > high:
                    raise GenerationError("RESOURCE_EXHAUSTED", f"{n['node_id']}: message count exceeds Tx capacity range")
                n[cap] = rng.randint(low, high)
        if counts[n["node_id"]] > number(n["tx_message_capacity"]):
            raise GenerationError("RESOURCE_EXHAUSTED", f"{n['node_id']}: fixed Tx capacity cannot hold requested messages")
    lookup = {n["node_id"]: n for n in nodes}
    bitrate = rng.choice(o["bitrate_choices"])
    ntu_ns = 10**9 // bitrate
    matrix = rng.choice(o["matrix_cycles"])
    periods = [p for p in o["periods"] if matrix % p == 0 and
               (o["resource_mode"] != "m_ttcan" or encode_cycle_code(p, 0) is not None)]
    basic = randrange(rng, o["basic_cycle_ntu"])
    txew, guard = randrange(rng, o["tx_enable_ntu"]), randrange(rng, o["guard_ntu"])
    delay = randrange(rng, o["filter_delay_ntu"])
    network = dict(resource_mode=o["resource_mode"], level=1, bitrate=bitrate, ntu_ns=ntu_ns,
                   basic_cycle_ntu=basic, matrix_cycles=matrix, expand_matrices=1,
                   tx_enable_ntu=txew, guard_ntu=guard, filter_delay_ntu=delay,
                   min_trigger_spacing_ntu=randrange(rng, o["min_trigger_spacing_ntu"]),
                   actual_master=actual["node_id"], gap=dict(enabled=False),
                   reference=dict(can_id=f"0x{0x80 | number(actual['master_priority']):03X}",
                                  frame_format="standard", dlc=1, tx_mark_ntu=basic - 2,
                                  watch_mark_ntu=basic + 300))
    if o["resource_mode"] == "m_ttcan":
        network["controller_version"] = o["controller_version"]
    used: dict[tuple[str, str], set[int]] = defaultdict(set)
    used_ids: set[tuple[str, int]] = set()
    messages, by_sender = [], defaultdict(list)
    allocation_styles = Counter()
    for n in nodes:
        sender = n["node_id"]
        for _ in range(counts[sender]):
            name = f"Msg{len(messages):03d}"
            possible_formats = []
            for fmt in o["frame_formats"]:
                cap = "rx_std_filter_capacity" if fmt == "standard" else "rx_ext_filter_capacity"
                kind = "rx_std_filter" if fmt == "standard" else "rx_ext_filter"
                available_nodes = [x for x in nodes if x["node_id"] != sender and len(used[(x["node_id"], kind)]) < number(x[cap])]
                if len(available_nodes) >= o["receiver_count"][0]:
                    possible_formats.append((fmt, available_nodes, kind, cap))
            if not possible_formats:
                raise GenerationError("RESOURCE_EXHAUSTED", f"{sender}: insufficient receiver filter capacity for {name}")
            preferred = "extended" if selected(rng, o["extended_probability"]) else "standard"
            choices = [x for x in possible_formats if x[0] == preferred] or possible_formats
            fmt, available_nodes, kind, cap = rng.choice(choices)
            max_receivers = min(o["receiver_count"][1], len(available_nodes))
            num_receivers = rng.randint(o["receiver_count"][0], max_receivers)
            receiver_nodes = rng.sample(available_nodes, num_receivers)
            receiver_nodes.sort(key=lambda x: x["node_id"])
            receivers = []
            for receiver in receiver_nodes:
                rid = receiver["node_id"]
                index = resource_slot(rng, used, rid, kind, number(receiver[cap]), o["explicit_number_probability"])
                allocation_styles["explicit_rx" if index is not None else "automatic_rx"] += 1
                r = dict(node_id=rid, check_reception=False)
                if index is not None:
                    r["filter_mnr"] = index
                receivers.append(r)
            tx = resource_slot(rng, used, sender, "tx_buffer", number(n["tx_message_capacity"]), o["explicit_number_probability"])
            allocation_styles["explicit_tx" if tx is not None else "automatic_tx"] += 1
            for _attempt in range(1000):
                can_id = rng.randrange(0x100, 0x800) if fmt == "standard" else rng.randrange(0x10000, 0x20000000)
                if (fmt, can_id) not in used_ids:
                    used_ids.add((fmt, can_id))
                    break
            else:
                raise GenerationError("IDENTIFIER_EXHAUSTED", "Could not choose a unique CAN identifier")
            period = rng.choice(periods)
            phase = rng.randrange(period)
            m = dict(name=name, can_id=f"0x{can_id:X}", frame_format=fmt, dlc=randrange(rng, o["dlc"]),
                     sender=sender, receivers=receivers,
                     transmissions=[dict(trigger_type="single", period_cycles=period, phase=phase)])
            if tx is not None:
                m["tx_mnr"] = tx
            messages.append(m)
            by_sender[sender].append(m)
    groups = []
    weights = o["window_mix"]
    mix_keys = ["single", "arbitration", "merged"]
    for sender in [n["node_id"] for n in nodes]:
        pending = list(by_sender[sender])
        while pending:
            m = pending.pop(0)
            local_weights = [weights["single"], weights["arbitration"], weights["merged"] if pending else 0]
            if not any(local_weights):
                raise GenerationError("INFEASIBLE_CONSTRAINT", f"{sender}: remaining message cannot form a required merged group")
            kind = rng.choices(mix_keys, weights=local_weights, k=1)[0]
            first = m["transmissions"][0]
            if kind == "merged":
                partner = pending.pop(rng.randrange(len(pending)))
                group = f"ZMerge{len(groups):03d}"
                first.update(trigger_type="merged", group_id=group)
                partner["transmissions"][0].update(trigger_type="arbi", group_id=group,
                    period_cycles=first["period_cycles"], phase=first["phase"])
                budget = sum(frame_bits(x["dlc"], x["frame_format"])["reserved_bits"] + guard for x in (m, partner)) + txew
                groups.append(dict(group_id=group, kind="merged", length_ntu=budget + randrange(rng, o["window_extra_ntu"])))
            elif kind == "arbitration":
                group = f"ZArbi{len(groups):03d}"
                first.update(trigger_type="arbi", group_id=group)
                budget = frame_bits(m["dlc"], m["frame_format"])["reserved_bits"] + guard + txew
                groups.append(dict(group_id=group, kind="arbitration", length_ntu=budget + randrange(rng, o["window_extra_ntu"])))
            else:
                budget = frame_bits(m["dlc"], m["frame_format"])["reserved_bits"] + txew + guard
                first["window_length_ntu"] = budget + delay + 2 + randrange(rng, o["window_extra_ntu"])
                if selected(rng, o["repeat_probability"]) and first["period_cycles"] > 1:
                    choices = [p for p in range(first["period_cycles"]) if p != first["phase"]]
                    m["transmissions"].append(dict(trigger_type="single", period_cycles=first["period_cycles"],
                        phase=rng.choice(choices), window_length_ntu=first["window_length_ntu"]))
                for r in m["receivers"]:
                    r["check_reception"] = selected(rng, o["rx_check_probability"])
    for source in list(groups):
        if source not in groups or source["kind"] != "arbitration" or not selected(rng, o["shared_group_probability"]):
            continue
        source_members = [m for m in messages if m["transmissions"][0].get("group_id") == source["group_id"]]
        if len(source_members) != 1:
            continue
        member = source_members[0]
        targets = []
        for target in groups:
            if target is source:
                continue
            target_members = [m for m in messages if m["transmissions"][0].get("group_id") == target["group_id"]]
            if all(m["sender"] != member["sender"] for m in target_members):
                targets.append((target, target_members))
        if targets:
            target, target_members = rng.choice(targets)
            condition = target_members[0]["transmissions"][0]
            member["transmissions"][0].update(group_id=target["group_id"],
                period_cycles=condition["period_cycles"], phase=condition["phase"])
            target["length_ntu"] += frame_bits(member["dlc"], member["frame_format"])["reserved_bits"] + guard
            groups.remove(source)
    messages.sort(key=lambda m: (m["transmissions"][0]["trigger_type"] != "single", m["name"]))
    for index, message in enumerate(messages):
        message["name"] = f"Msg{index:03d}"
    config = dict(schema_version=1,
        metadata=dict(source="Constrained random generation", random_generation=identity,
                      assumptions=["Static scheduling model; arbitration outcomes are not simulated"]),
        network=network, nodes=nodes, messages=messages, arbitration_groups=groups)
    return config, dict(allocation_styles=dict(allocation_styles))


def coverage(s: Scheduler, options: dict[str, Any], build_info: dict[str, Any]) -> dict[str, Any]:
    unique_windows = {(w.kind, w.window_id) for w in s.windows if w.kind in {"exclusive", "arbitration", "merged"}}
    logical = Counter({"single": 0, "arbitration": 0, "merged": 0})
    for kind, _ in unique_windows:
        logical["single" if kind == "exclusive" else kind] += 1
    expanded = Counter(w.kind for w in s.windows if w.kind in {"exclusive", "arbitration", "merged"})
    triggers = [t for ts in s.triggers.values() for t in ts]
    by_resource: Counter[tuple[str, int]] = Counter((t.node_id, t.mnr) for t in triggers if t.alias in {"single", "merged", "arbi"})
    by_time: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for t in triggers:
        if t.alias in {"single", "merged", "arbi", "rx"} and t.time_mark_ntu is not None:
            by_time[(t.node_id, t.time_mark_ntu)].append(t)
    shared_time = any(not set(a.active_cycles) & set(b.active_cycles)
                      for entries in by_time.values() for i, a in enumerate(entries) for b in entries[i + 1:])
    occupied = sum(w.end_ntu - w.start_ntu for w in s.windows if w.kind in {"exclusive", "arbitration", "merged"})
    load = occupied / (s.basic * s.cycles)
    features = []
    flags = {
        "single": logical["single"] > 0,
        "arbitration": logical["arbitration"] > 0,
        "merged": logical["merged"] > 0 and any(t.alias == "merged" for t in triggers),
        "rx": any(t.alias == "rx" for t in triggers),
        "extended": any(m["frame_format"] == "extended" for m in s.messages.values()),
        "multiple_receivers": any(len(m["receivers"]) > 1 for m in s.messages.values()),
        "shared_mnr": any(n > 1 for n in by_resource.values()),
        "shared_time": shared_time,
        "multiple_masters": sum(n["role"] == "master" for n in s.nodes.values()) > 1,
        "multi_node_arbitration": any(w.kind in {"merged", "arbitration"} and len(w.nodes) > 1 for w in s.windows),
        "high_load": load >= 0.70,
    }
    features = sorted(name for name, present in flags.items() if present)
    total = sum(logical.values())
    weights = options["window_mix"]
    weight_total = sum(weights.values())
    mix = {name: {"target_fraction": weights[name] / weight_total,
                  "actual_fraction": logical[name] / total if total else 0.0,
                  "logical_count": logical[name],
                  "difference": logical[name] / total - weights[name] / weight_total if total else -weights[name] / weight_total}
           for name in ("single", "arbitration", "merged")}
    return dict(features=features, node_count=len(s.nodes), master_count=sum(n["role"] == "master" for n in s.nodes.values()),
                message_count=len(s.messages), matrix_cycles=s.cycles, basic_cycle_ntu=s.basic,
                formats=dict(Counter(m["frame_format"] for m in s.messages.values())),
                dlcs=dict(Counter(str(m["dlc"]) for m in s.messages.values())),
                periods=dict(Counter(str(t.period_cycles) for t in triggers if t.alias in {"single", "merged", "arbi"})),
                logical_window_counts=dict(logical), expanded_window_counts=dict(expanded), window_mix=mix,
                data_window_reservation_fraction=load, global_time_shift_ntu=build_info.get("global_time_shift_ntu", 0),
                allocation_styles=build_info["allocation_styles"],
                node_resources=s.resources)


def semantic_hash(config: dict[str, Any]) -> str:
    clean = copy.deepcopy(config)
    clean.pop("metadata", None)
    return digest(clean)


def core_signature(s: Scheduler) -> str:
    """Compare exported semantics rather than volatile presentation or path fields."""
    return digest(dict(matrix=s.matrix, message_maps=dict(s.maps),
                       triggers={n: [t.record() for t in ts] for n, ts in sorted(s.triggers.items())}))


def align_arbitration_candidates(schedule: Scheduler) -> Scheduler:
    """Give distinct-node candidates one shared mark in ordinary arbitration windows."""
    groups = {g["group_id"] for g in schedule.config.get("arbitration_groups", []) if g["kind"] == "arbitration"}
    starts = {w.group_id: w.start_ntu for w in schedule.windows if w.group_id in groups}
    candidate = copy.deepcopy(schedule.config)
    changed = False
    for m in candidate["messages"]:
        for transmission in m["transmissions"]:
            group = transmission.get("group_id")
            if group in starts and transmission["time_mark"] != starts[group]:
                transmission["time_mark"] = starts[group]
                changed = True
    return Scheduler(candidate).compile() if changed else schedule


def apply_window_bounds(schedule: Scheduler, options: dict[str, Any], rng: random.Random) -> tuple[Scheduler, int]:
    """Apply a random common offset while preserving all relative trigger timings."""
    windows = [w for w in schedule.windows if w.kind in {"exclusive", "arbitration", "merged"}]
    if not windows:
        return schedule, 0
    length_min, length_max = options["window_length_ntu"]
    for w in windows:
        length = w.end_ntu - w.start_ntu
        if not length_min <= length <= length_max:
            raise GenerationError("WINDOW_LENGTH_UNMET", "A reserved window length is outside the requested range",
                                  window_id=w.window_id, cycle=w.cycle, length_ntu=length,
                                  target=options["window_length_ntu"])
    start_min, start_max = options["window_start_ntu"]
    min_start = min(w.start_ntu for w in windows)
    max_start = max(w.start_ntu for w in windows)
    max_end = max(w.end_ntu for w in windows)
    lower = max(0, start_min - min_start)
    upper = min(start_max - max_start, schedule.net["reference"]["tx_mark_ntu"] - max_end)
    if lower > upper:
        raise GenerationError("WINDOW_START_UNMET", "No shared offset satisfies all window start bounds and the reference boundary",
                              target=options["window_start_ntu"], minimum_shift=lower, maximum_shift=upper)
    shift = rng.randint(lower, upper)
    if not shift:
        return schedule, 0
    candidate = copy.deepcopy(schedule.config)
    for m in candidate["messages"]:
        for tx in m["transmissions"]:
            tx["time_mark"] = number(tx["time_mark"]) + shift
        for receiver in m["receivers"]:
            for rx in receiver.get("rx_checks", []):
                rx["time_mark"] = number(rx["time_mark"]) + shift
    for group in candidate.get("arbitration_groups", []):
        for key in ("start_ntu", "end_ntu"):
            if key in group:
                group[key] = number(group[key]) + shift
    return Scheduler(candidate).compile(), shift


def make_case(options: dict[str, Any], master_seed: int, case_index: int,
              max_attempts: int, scheduler_sha256: str | None = None) -> dict[str, Any]:
    """Return an independent case result with all failed-attempt evidence."""
    case_seed = derive_seed(master_seed, case_index)
    option_sha = digest(options)
    scheduler_sha256 = scheduler_sha256 or source_hash()
    attempts = []
    for attempt in range(max_attempts):
        attempt_seed = derive_seed(case_seed, attempt)
        identity = dict(master_seed=str(master_seed), case_index=case_index, case_seed=str(case_seed),
                        attempt=attempt, attempt_seed=str(attempt_seed), generator_version=GENERATOR_VERSION,
                        python_version=".".join(map(str, sys.version_info[:3])), scheduler_sha256=scheduler_sha256,
                        options_sha256=option_sha)
        try:
            rng = random.Random(attempt_seed)
            candidate, build_info = build_candidate(options, rng, identity)
            schedule = Scheduler(candidate).compile()
            if schedule.valid:
                schedule = align_arbitration_candidates(schedule)
            if schedule.valid:
                schedule, build_info["global_time_shift_ntu"] = apply_window_bounds(schedule, options, rng)
            report = schedule.report()
            if not schedule.valid or (report["warnings"] and not options["allow_warnings"]):
                attempts.append(dict(attempt=attempt, attempt_seed=str(attempt_seed),
                                     issues=report["issues"] or [dict(code="SCHEDULER_REJECTED", message="Scheduler rejected candidate")]))
                continue
            stats = coverage(schedule, options, build_info)
            missing = sorted(set(options["required_features"]) - set(stats["features"]))
            load = stats["data_window_reservation_fraction"]
            if missing or not options["target_bus_load"][0] <= load <= options["target_bus_load"][1]:
                issues = []
                if missing:
                    issues.append(dict(code="FEATURES_MISSING", message="Required case features were not produced", details=dict(features=missing)))
                if not options["target_bus_load"][0] <= load <= options["target_bus_load"][1]:
                    issues.append(dict(code="TARGET_LOAD_UNMET", message="Data-window reservation fraction is outside the requested range", details=dict(actual=load, target=options["target_bus_load"])))
                attempts.append(dict(attempt=attempt, attempt_seed=str(attempt_seed), issues=issues))
                continue
            identity["features"] = stats["features"]
            identity["constraints"] = dict(node_count=options["node_count"], master_count=options["master_count"],
                messages_per_node=options["messages_per_node"], matrix_cycles=options["matrix_cycles"],
                basic_cycle_ntu=options["basic_cycle_ntu"], periods=options["periods"], target_bus_load=options["target_bus_load"],
                window_start_ntu=options["window_start_ntu"], window_length_ntu=options["window_length_ntu"])
            schedule.config["metadata"]["random_generation"] = copy.deepcopy(identity)
            replay = Scheduler(schedule.config).compile()
            if not replay.valid or core_signature(schedule) != core_signature(replay):
                attempts.append(dict(attempt=attempt, attempt_seed=str(attempt_seed),
                    issues=[dict(code="REPLAY_MISMATCH", message="Resolved configuration did not reproduce matrix, maps, and trigger lists", details=dict(replay_report=replay.report()))]))
                continue
            return dict(success=True, schedule=schedule, identity=identity, stats=stats, attempts=attempts,
                        config_semantic_sha256=semantic_hash(schedule.config), signature=core_signature(schedule))
        except GenerationError as exc:
            attempts.append(dict(attempt=attempt, attempt_seed=str(attempt_seed), issues=[exc.record()]))
    return dict(success=False, identity=dict(master_seed=str(master_seed), case_index=case_index,
                case_seed=str(case_seed), generator_version=GENERATOR_VERSION, scheduler_sha256=scheduler_sha256,
                options_sha256=option_sha), attempts=attempts,
                failure=dict(code="MAX_ATTEMPTS", message=f"No acceptable case after {max_attempts} attempts"))


def reproduce_command(master_seed: int, case_index: int, options_path: Path,
                      max_attempts: int, output_root: Path) -> str:
    opts = os.path.relpath(options_path.resolve(), ROOT)
    out = os.path.relpath(output_root.resolve(), ROOT)
    return " ".join(shlex.quote(str(x)) for x in (
        "python", "ttcan_random_config.py", "--seed", master_seed, "--case-start", case_index,
        "--count", 1, "--max-attempts", max_attempts, "--options", opts, "--output-dir", out))


def inject_generation_html(path: Path, metadata: dict[str, Any]) -> None:
    source = path.read_text(encoding="utf-8")
    marker = '<script id="generation-data" type="application/json">null</script>'
    if source.count(marker) != 1:
        raise GenerationError("HTML_TEMPLATE", "The scheduler HTML template lacks its generation metadata marker")
    payload = json.dumps(metadata, ensure_ascii=True).replace("<", "\\u003c")
    path.write_text(source.replace(marker, f'<script id="generation-data" type="application/json">{payload}</script>'), encoding="utf-8")


def promote_case(result: dict[str, Any], options: dict[str, Any], run_dir: Path,
                 master_seed: int, case_index: int, max_attempts: int, output_root: Path) -> dict[str, Any]:
    """Publish a case atomically only after config, replay, and HTML succeed."""
    name = f"case_{case_index:04d}"
    final = run_dir / name
    if final.exists():
        raise GenerationError("OUTPUT_COLLISION", f"Case output already exists: {final}")
    temp = Path(tempfile.mkdtemp(prefix=f".staging_{name}_", dir=run_dir))
    try:
        schedule = result["schedule"]
        write_json(temp / "config.json", schedule.config)
        write_json(temp / "generator_options.json", options)
        export_schedule(schedule, temp)
        config_sha = hashlib.sha256((temp / "config.json").read_bytes()).hexdigest()
        reproduction = reproduce_command(master_seed, case_index, final / "generator_options.json", max_attempts, output_root)
        identity = {**result["identity"], "reproduce_command": reproduction}
        report = dict(**identity, success=True, retry_count=len(result["attempts"]),
                      attempt_failures=result["attempts"], resolved_options=options,
                      config_sha256=config_sha, config_semantic_sha256=result["config_semantic_sha256"],
                      core_signature=result["signature"], coverage=result["stats"],
                      scheduler_validation=schedule.report(),
                      model_limitations=["Static Level 1 scheduling model", "Arbitration outcomes are not simulated",
                                         "Merged and Arbitration messages do not receive Rx checks in this scheduler"],
                      protocol_conformance_claim=False)
        write_json(temp / "generation_report.json", report)
        inject_generation_html(temp / "schedule.html", identity)
        expected = ["config.json", "generator_options.json", "generation_report.json", "validation_report.json",
                    "effective_config.json", "schedule.html", "global_timeline.csv", "system_matrix.csv", "system_matrix.json"]
        for name_required in expected:
            if not (temp / name_required).is_file():
                raise GenerationError("MISSING_ARTIFACT", f"Missing case artifact {name_required}")
        for nid in schedule.nodes:
            for suffix in ("message_map.csv", "message_map.json", "trigger_list.csv", "trigger_list.json"):
                if not (temp / "nodes" / f"{nid}_{suffix}").is_file():
                    raise GenerationError("MISSING_ARTIFACT", f"Missing node artifact for {nid}: {suffix}")
        os.rename(temp, final)
        return dict(case_index=case_index, status="success", case_seed=result["identity"]["case_seed"],
                    attempt=result["identity"]["attempt"], attempt_seed=result["identity"]["attempt_seed"],
                    retry_count=len(result["attempts"]), relative_path=name, config_sha256=config_sha,
                    config_semantic_sha256=result["config_semantic_sha256"],
                    core_signature=result["signature"], coverage=result["stats"],
                    warnings=schedule.report()["warnings"], reproduce_command=reproduction)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def batch_index(manifest: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)
    rows = []
    for case in manifest["cases"]:
        if case["status"] == "success":
            d, stats = case["relative_path"], case["coverage"]
            mix = stats["logical_window_counts"]
            max_trigger = max((r["trigger_memory"]["used"] / max(1, r["trigger_memory"]["capacity"]) for r in stats["node_resources"].values()), default=0)
            rows.append(f'<tr><td>{case["case_index"]}</td><td class="seed">{esc(case["case_seed"])}</td><td><span class="ok">PASS</span><br>Warnings: {case["warnings"]}</td><td>{stats["node_count"]}</td><td>{stats["message_count"]}</td><td>{esc(mix)}</td><td>{stats["data_window_reservation_fraction"]:.1%}</td><td>{max_trigger:.1%}</td><td>{esc(", ".join(stats["features"]))}</td><td><a href="{esc(d)}/schedule.html">View</a> <a href="{esc(d)}/config.json">Config</a> <a href="{esc(d)}/generation_report.json">Report</a> <a href="{esc(d)}/validation_report.json">Validation</a></td></tr>')
        else:
            rows.append(f'<tr><td>{case["case_index"]}</td><td class="seed">{esc(case.get("case_seed", "N/A"))}</td><td><span class="bad">FAIL</span></td><td colspan="6">{esc(case.get("failure", {}).get("message", "Case failed"))}</td><td><a href="{esc(case["failure_path"])}">Failure</a></td></tr>')
    counts = manifest["summary"]
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TTCAN Random Cases</title><style>
body{{margin:0;background:#f2f5fa;color:#18243d;font:14px/1.5 system-ui,sans-serif}}header{{background:#12213c;color:white;padding:32px}}main{{max-width:1500px;margin:auto;padding:24px}}h1{{margin:4px 0}}.eyebrow{{letter-spacing:2px;color:#adbef9;font-size:11px}}.cards{{display:flex;flex-wrap:wrap;gap:12px}}.card{{background:white;border:1px solid #dce3ee;border-radius:9px;padding:16px;min-width:0;max-width:100%;overflow-wrap:anywhere}}.card b{{font-size:23px;display:block}}.table{{overflow:auto;background:white;border:1px solid #dce3ee;border-radius:9px;margin-top:20px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:12px;border-bottom:1px solid #e7ecf4;text-align:left}}th{{background:#f8fafc;white-space:nowrap}}a{{color:#315cdd;margin-right:8px}}.seed{{font:10px ui-monospace,monospace;overflow-wrap:anywhere;max-width:170px}}.ok{{color:#047857;font-weight:bold}}.bad{{color:#b91c1c;font-weight:bold}}p{{color:#64748b}}code{{overflow-wrap:anywhere}}
</style></head><body><header><div class="eyebrow">ENGINEERING TOOLS / RANDOM TTCAN</div><h1>Generated cases</h1><div>Constraint-based scheduling with deterministic seed replay</div></header><main><div class="cards"><div class="card">Master seed<b>{esc(manifest["master_seed"])}</b></div><div class="card">Requested<b>{counts["requested"]}</b></div><div class="card">Successful<b>{counts["succeeded"]}</b></div><div class="card">Failed<b>{counts["failed"]}</b></div></div><p>Window counts are logical groups before cycle expansion. Load is reserved data-window time, not measured CAN utilization. All links are relative to this folder.</p><div class="table"><table><thead><tr><th>Case</th><th>Case seed</th><th>Status</th><th>Nodes</th><th>Messages</th><th>Logical windows</th><th>Reserved load</th><th>Max trigger usage</th><th>Features</th><th>Files</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p><a href="manifest.json">Open manifest.json</a></p></main></body></html>'''


def run_batch(options: dict[str, Any], master_seed: int, count: int, case_start: int,
              max_attempts: int, output_root: Path) -> tuple[Path, dict[str, Any]]:
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f"run_{master_seed}_", dir=output_root))
    scheduler_sha = source_hash()
    cases, seen = [], {}
    issue_counts: Counter[str] = Counter()
    failures = run_dir / "failures"
    print(f"Master seed: {master_seed}", flush=True)
    print(f"Run directory: {run_dir}", flush=True)
    for index in range(case_start, case_start + count):
        result = make_case(options, master_seed, index, max_attempts, scheduler_sha)
        issue_counts.update(issue.get("code", "UNKNOWN") for a in result["attempts"] for issue in a["issues"])
        if result["success"] and result["config_semantic_sha256"] in seen:
            result["success"] = False
            result["failure"] = dict(code="DUPLICATE_CONFIG", message="Configuration duplicates an earlier case after removing metadata",
                                     duplicate_of=seen[result["config_semantic_sha256"]])
        if result["success"]:
            try:
                record = promote_case(result, options, run_dir, master_seed, index, max_attempts, output_root)
                seen[result["config_semantic_sha256"]] = index
                print(f"Case {index}: PASS; retries={record['retry_count']}; warnings={record['warnings']}; report={run_dir / record['relative_path'] / 'schedule.html'}", flush=True)
            except (GenerationError, OSError, ValueError) as exc:
                result["success"] = False
                result["failure"] = exc.record() if isinstance(exc, GenerationError) else dict(code="EXPORT_FAILURE", message=str(exc))
        if not result["success"]:
            failures.mkdir(exist_ok=True)
            path = failures / f"case_{index:04d}.json"
            failure_report = dict(identity=result["identity"], failure=result["failure"], attempts=result["attempts"], options=options)
            write_json(path, failure_report)
            record = dict(case_index=index, status="failed", case_seed=result["identity"]["case_seed"],
                          failure=result["failure"], failure_path=f"failures/{path.name}")
            print(f"Case {index}: FAIL; {result['failure']['code']}: {result['failure']['message']}", flush=True)
        cases.append(record)
    success = [x for x in cases if x["status"] == "success"]
    feature_count = Counter(f for case in success for f in case["coverage"]["features"])
    logical_counts = Counter()
    expanded_counts = Counter()
    format_counts = Counter()
    period_counts = Counter()
    dlc_counts = Counter()
    for case in success:
        c = case["coverage"]
        logical_counts.update(c["logical_window_counts"])
        expanded_counts.update(c["expanded_window_counts"])
        format_counts.update(c["formats"])
        period_counts.update(c["periods"])
        dlc_counts.update(c["dlcs"])
    logical_total = sum(logical_counts.values())
    weight_total = sum(options["window_mix"].values())
    batch_mix = {k: dict(target_fraction=options["window_mix"][k]/weight_total,
                         actual_fraction=logical_counts[k]/logical_total if logical_total else 0.0,
                         logical_count=logical_counts[k]) for k in ("single", "arbitration", "merged")}
    manifest = dict(generator_version=GENERATOR_VERSION, master_seed=str(master_seed),
        python_version=".".join(map(str, sys.version_info[:3])), scheduler_sha256=scheduler_sha,
        options_sha256=digest(options), options=options, case_start=case_start, max_attempts=max_attempts,
        summary=dict(requested=count, succeeded=len(success), failed=len(cases)-len(success),
                     retries=sum(x["retry_count"] for x in success), feature_counts=dict(sorted(feature_count.items())),
                     cases_with_warnings=sum(x["warnings"] > 0 for x in success),
                     warning_count=sum(x["warnings"] for x in success),
                     attempt_issue_counts=dict(sorted(issue_counts.items())), batch_window_mix=batch_mix,
                     expanded_window_counts=dict(sorted(expanded_counts.items())), format_counts=dict(sorted(format_counts.items())),
                     period_counts=dict(sorted(period_counts.items())), dlc_counts=dict(sorted(dlc_counts.items())),
                     node_count_distribution=dict(sorted(Counter(str(x["coverage"]["node_count"]) for x in success).items()))),
        cases=cases, protocol_conformance_claim=False)
    write_json(run_dir / "manifest.json", manifest)
    (run_dir / "index.html").write_text(batch_index(manifest), encoding="utf-8")
    print(f"Batch: {len(success)}/{count} successful; index={run_dir / 'index.html'}", flush=True)
    for case in success:
        print(f"Replay case {case['case_index']}: {case['reproduce_command']}", flush=True)
    return run_dir, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate constrained, reproducible TTCAN configurations and offline reports")
    parser.add_argument("--seed", type=int, help="Master seed; a random 64-bit seed is generated if omitted")
    parser.add_argument("--count", type=int, default=1, help="Number of distinct successful cases to generate")
    parser.add_argument("--case-start", type=int, default=0, help="First independent case index")
    parser.add_argument("--nodes", type=int, help="Fixed node count")
    parser.add_argument("--masters", type=int, help="Fixed candidate-master count")
    parser.add_argument("--messages-per-node", type=int, help="Fixed logical Tx message count per node")
    parser.add_argument("--options", type=Path, help="JSON randomization constraints")
    parser.add_argument("--nodes-file", type=Path, help="JSON fixed topology and per-node capacities")
    parser.add_argument("--max-attempts", type=int, default=100, help="Maximum candidate attempts per case")
    parser.add_argument("--output-dir", type=Path, default=Path("random_cases"), help="Root for newly created run directories")
    parser.add_argument("--allow-warnings", action="store_true", help="Accept scheduler warnings and record them")
    parser.add_argument("--require-features", help="Comma-separated required features for every case")
    args = parser.parse_args(argv)
    try:
        if CJK.search(str(args.output_dir)):
            raise GenerationError("LANGUAGE", "Output directory must not contain CJK characters")
        if args.count < 1 or args.count > 10000 or args.case_start < 0 or not 1 <= args.max_attempts <= 10000:
            raise GenerationError("INVALID_OPTIONS", "count must be 1..10000, case-start nonnegative, and max-attempts 1..10000")
        if args.seed is not None and not 0 <= args.seed < 2**256:
            raise GenerationError("INVALID_OPTIONS", "seed must be an integer in 0..2^256-1")
        master_seed = args.seed if args.seed is not None else secrets.randbits(64)
        raw = read_json_file(args.options) if args.options else {}
        topology = read_json_file(args.nodes_file) if args.nodes_file else None
        features = args.require_features.split(",") if args.require_features else None
        options = resolve_options(raw, nodes=args.nodes, masters=args.masters, messages=args.messages_per_node,
                                  topology=topology, allow_warnings=args.allow_warnings, required_features=features)
        run_dir, manifest = run_batch(options, master_seed, args.count, args.case_start, args.max_attempts, args.output_dir)
        return 0 if manifest["summary"]["failed"] == 0 else 1
    except (GenerationError, OSError, ValueError, TypeError, KeyError) as exc:
        code = exc.code if isinstance(exc, GenerationError) else "INPUT_ERROR"
        safe = str(exc).encode("ascii", "backslashreplace").decode("ascii")
        print(f"ERROR {code}: {safe}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
