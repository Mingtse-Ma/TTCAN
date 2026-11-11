"""Behavioral tests for schedule semantics, resource allocation, and exports."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ttcan_scheduler import (ROOT, Scheduler, decode_cycle_code, encode_cycle_code,
                             export_schedule, frame_bits, scan_english, trigger_type)


def demo():
    return json.loads((ROOT / "examples" / "nodea_nodeb_demo.json").read_text())


def automatic():
    return json.loads((ROOT / "examples" / "automatic_demo.json").read_text())


class ScheduleTests(unittest.TestCase):
    def codes(self, s):
        return {i.code for i in s.issues if i.severity == "error"}

    def test_default_matrix_and_node_local_maps(self):
        s = Scheduler(demo()).compile()
        self.assertTrue(s.valid, s.report())
        self.assertEqual(len(s.matrix["rows"]), 64)
        self.assertEqual(s.matrix_duration, 128000)
        self.assertEqual(s.resources["NodeA"]["tx_buffer"]["used"], 3)
        self.assertEqual(s.resources["NodeB"]["tx_buffer"]["used"], 3)
        self.assertEqual(s.resources["NodeA"]["trigger_memory"]["used"], 9)
        self.assertEqual(s.resources["NodeB"]["trigger_memory"]["used"], 6)
        self.assertEqual(s.resources["NodeA"]["expected_data_tx_triggers_per_matrix"], 19)
        self.assertEqual(s.resources["NodeB"]["expected_data_tx_triggers_per_matrix"], 3)
        self.assertEqual(len(s.triggers["NodeA"]), 16)
        self.assertFalse(any(t.alias == "ref" for t in s.triggers["NodeB"]))
        self.assertEqual([r["mnr"] for r in s.maps["NodeB"] if r["message_name"] == "Msg7"], [0])
        self.assertEqual([r["mnr"] for r in s.maps["NodeA"] if r["message_name"] == "Msg7"], [7])

    def test_partial_and_complete_cycle_semantics(self):
        s = Scheduler(demo()).compile()
        singles = [t for t in s.triggers["NodeA"] if t.alias == "single"]
        self.assertEqual(singles[0].active_cycles, list(range(0,64,4)))
        self.assertEqual(singles[1].active_cycles, [3])
        self.assertEqual(len([w for w in s.windows if w.kind == "merged"]), 1)
        self.assertEqual(decode_cycle_code("0b0010011"), (16,3))
        for p in (1,2,4,8,16,32,64):
            for phase in range(p):
                self.assertEqual(decode_cycle_code(encode_cycle_code(p,phase)), (p,phase))

    def test_raw_conflict_is_preserved_and_rejected(self):
        raw = json.loads((ROOT / "examples" / "nodea_reference_raw.json").read_text())
        original = copy.deepcopy(raw)
        s = Scheduler(raw).compile()
        self.assertIn("TRIGGER_COLLISION", self.codes(s))
        self.assertIn("RX_TOO_EARLY", self.codes(s))
        self.assertEqual(raw, original)

    def test_mutually_exclusive_marks_are_allowed(self):
        s = Scheduler(demo()).compile()
        shared = [t for t in s.triggers["NodeB"] if t.time_mark_ntu == 600]
        self.assertEqual(len(shared), 2)
        self.assertFalse(set(shared[0].active_cycles) & set(shared[1].active_cycles))
        self.assertNotIn("TRIGGER_COLLISION", self.codes(s))

    def test_number_spaces_and_capacity_failures(self):
        c = demo()
        c["nodes"][0]["tx_message_capacity"] = 7
        s = Scheduler(c).compile()
        self.assertFalse(s.valid)
        self.assertIn("MNR 7", s.issues[0].message)
        c = demo()
        c["nodes"][0]["trigger_capacity"] = 8
        self.assertIn("TRIGGER_CAPACITY", self.codes(Scheduler(c).compile()))
        c = demo()
        c["messages"][2]["tx_mnr"] = 7
        self.assertFalse(Scheduler(c).compile().valid)

    def test_unsupported_type_codes_are_not_aliased(self):
        for code in (3,9,"0x3","0x9"):
            with self.assertRaises(ValueError):
                trigger_type(code)
            c = demo()
            c["messages"][0]["transmissions"][0]["trigger_type"] = code
            self.assertFalse(Scheduler(c).compile().valid)

    def test_merged_closure_is_checked_per_effective_cycle(self):
        c = demo()
        c["messages"][3]["transmissions"][0]["cycle_code"] = "0b1000010"
        s = Scheduler(c).compile()
        self.assertIn("MERGED_NOT_CLOSED", self.codes(s))
        c = demo()
        c["messages"][3]["transmissions"][0]["trigger_type"] = "merged"
        self.assertIn("MERGED_NOT_CLOSED", self.codes(Scheduler(c).compile()))

    def test_early_rx_and_last_filter_overwrite(self):
        c = demo()
        c["messages"][0]["receivers"][0]["rx_checks"][0]["time_mark"] = 500
        self.assertIn("RX_TOO_EARLY", self.codes(Scheduler(c).compile()))
        c = automatic()
        c["messages"] = [dict(name="First", can_id="0x210", sender="Node0", time_mark=200,
                              receivers=[dict(node_id="Node2", rx_checks=[dict(time_mark=800)])]),
                         dict(name="Second", can_id="0x211", sender="Node1", time_mark=450,
                              receivers=[dict(node_id="Node2")])]
        self.assertIn("RX_LAST_FILTER", self.codes(Scheduler(c).compile()))

    def test_failed_windows_are_not_silently_moved(self):
        c = demo()
        c["messages"][1]["transmissions"][0]["time_mark"] = "Mark3"
        s = Scheduler(c).compile()
        self.assertIn("WINDOW_OVERLAP", self.codes(s))
        self.assertEqual(s.requests[2]["mark"], 1000)
        c = automatic()
        c["messages"][0]["window_length_ntu"] = 4000
        self.assertIn("UNSCHEDULABLE", self.codes(Scheduler(c).compile()))
        c = demo()
        c["messages"][0]["transmissions"][0]["window_length_ntu"] = 50
        self.assertIn("WINDOW_BUDGET", self.codes(Scheduler(c).compile()))

    def test_automatic_placement_is_deterministic_and_replayable(self):
        s = Scheduler(automatic()).compile()
        self.assertTrue(s.valid, s.report())
        other = Scheduler(automatic()).compile()
        self.assertEqual(s.payload(), other.payload())
        replay = Scheduler(s.config).compile()
        self.assertTrue(replay.valid, replay.report())
        self.assertEqual([t.record() for t in s.triggers["Node0"]], [t.record() for t in replay.triggers["Node0"]])

    def test_frame_bounds_and_standard_extended_resources(self):
        self.assertEqual(frame_bits(8,"standard")["unstuffed_bits"], 111)
        self.assertGreater(frame_bits(8,"extended")["reserved_bits"], frame_bits(8,"standard")["reserved_bits"])
        c = automatic()
        c["nodes"][1]["rx_ext_filter_capacity"] = 1
        c["messages"][0].update(frame_format="extended",can_id="0x12345")
        s = Scheduler(c).compile()
        self.assertTrue(s.valid, s.report())
        self.assertEqual(s.maps["Node1"][0]["resource_type"],"rx_ext_filter")

    def test_hardware_profile_limits_and_version(self):
        c = demo()
        c["network"].update(resource_mode="m_ttcan",controller_version="3.3.0")
        self.assertTrue(Scheduler(c).compile().valid)
        c["nodes"][0]["tx_message_capacity"] = 33
        self.assertFalse(Scheduler(c).compile().valid)
        c = demo()
        c["network"]["resource_mode"] = "m_ttcan"
        self.assertFalse(Scheduler(c).compile().valid)

    def test_static_gap_and_expanded_matrix_origins(self):
        c = demo()
        c["network"].update(expand_matrices=2,gap=dict(enabled=True,duration_ntu=500,after_cycles=[3,63]))
        s = Scheduler(c).compile()
        self.assertTrue(s.valid, s.report())
        self.assertEqual(s.matrix_duration,129000)
        self.assertEqual(s.origins[4],8500)
        self.assertEqual(s.origins[64],129000)
        gaprefs = [t for t in s.triggers["NodeA"] if t.alias=="ref_gap"]
        self.assertEqual(gaprefs[0].active_cycles,[3,63])
        watches=[e for e in s.events if e["status"]=="inactive_timeout_monitor"]
        self.assertEqual(len(watches),256)

    def test_slave_ref_and_early_end_are_rejected(self):
        c = demo()
        c["node_triggers"] = {"NodeB":[dict(trigger_type="ref",time_mark=1998)]}
        self.assertIn("SLAVE_REF",self.codes(Scheduler(c).compile()))
        c = demo()
        c["node_triggers"] = {"NodeA":[dict(trigger_type="end",time_mark=1800)]}
        self.assertIn("END_BEFORE_WATCH",self.codes(Scheduler(c).compile()))

    def test_exports_and_cli_return_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            s = Scheduler(demo()).compile()
            paths = export_schedule(s,dest)
            self.assertEqual(len(paths),14)
            self.assertTrue((dest/"schedule.html").exists())
            html=(dest/"schedule.html").read_text()
            self.assertNotIn("__SCHEDULE_DATA__",html)
            self.assertNotIn('src="https://',html)
            cells=json.loads((dest/"system_matrix.json").read_text())
            for row in cells["rows"]:
                self.assertEqual(sum(cell["end_ntu"]-cell["start_ntu"] for cell in row["cells"]),2000)
            cmd=[sys.executable,str(ROOT/"ttcan_scheduler.py"),"--config",str(ROOT/"examples"/"nodea_reference_raw.json"),"--validate-only","--output-dir",str(dest/"raw")]
            proc=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(proc.returncode,1)
            self.assertIn("TRIGGER_COLLISION",proc.stdout)
            self.assertFalse((dest/"raw"/"schedule.html").exists())

    def test_language_guard_detects_decoded_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            (path/"bad.json").write_text(json.dumps(dict(label=chr(0x4E2D))))
            (path/"manual_zh.md").write_text(chr(0x4E2D))
            self.assertEqual(len(scan_english(path)),1)
            c=automatic()
            c["metadata"]["source"]=chr(0x4E2D)
            self.assertFalse(Scheduler(c).compile().valid)

    def test_malformed_config_has_a_validation_report(self):
        for c in ([],dict(network=[]),dict(nodes=[None]),dict(nodes=[])):
            self.assertFalse(Scheduler(c).compile().valid)

    def test_message_level_conditions_are_inherited(self):
        c = automatic()
        c["messages"] = [c["messages"][0]]
        c["messages"][0].update(period_cycles=4,phase=2,transmissions=[dict(time_mark=300)])
        s = Scheduler(c).compile()
        self.assertTrue(s.valid, s.report())
        self.assertEqual(next(t for t in s.triggers["Node0"] if t.alias=="single").active_cycles,[2])

    def test_custom_periods_and_hardware_period_rejection(self):
        c = automatic()
        c["network"]["matrix_cycles"] = 3
        c["messages"] = [c["messages"][0]]
        c["messages"][0].update(period_cycles=3,phase=1)
        s=Scheduler(c).compile()
        self.assertTrue(s.valid,s.report())
        self.assertIsNone(next(t.record()["cycle_code"] for t in s.triggers["Node0"] if t.alias=="single"))
        c["network"].update(resource_mode="m_ttcan",controller_version="3.3.0")
        self.assertFalse(Scheduler(c).compile().valid)

    def test_group_boundaries_and_deadline_are_not_ignored(self):
        c=demo()
        c["arbitration_groups"][0]["length_ntu"]=500
        self.assertFalse(Scheduler(c).compile().valid)
        c=demo()
        c["messages"][2]["transmissions"][0]["deadline_ntu"]=1500
        self.assertIn("DEADLINE",self.codes(Scheduler(c).compile()))

    def test_possible_early_competing_frame_is_not_missed(self):
        c=automatic()
        c["messages"]=[dict(name="First",can_id="0x220",sender="Node0",time_mark=100,
                            receivers=[dict(node_id="Node2",rx_checks=[dict(time_mark=430)])]),
                       dict(name="Second",can_id="0x221",sender="Node1",time_mark=300,
                            receivers=[dict(node_id="Node2")])]
        self.assertIn("RX_FILTER_ORDER_UNCERTAIN",self.codes(Scheduler(c).compile()))

    def test_explicit_numbers_are_reserved_before_automatic_numbers(self):
        c=automatic()
        c["messages"]=[dict(name="Automatic",can_id="0x230",sender="Node0",
                            receivers=[dict(node_id="Node1")]),
                       dict(name="Explicit",can_id="0x231",sender="Node0",tx_mnr=0,
                            receivers=[dict(node_id="Node1",filter_mnr=0)])]
        s=Scheduler(c).compile()
        self.assertTrue(s.valid,s.report())
        self.assertEqual(s.messages["Automatic"]["tx_mnr"],1)
        self.assertEqual(s.messages["Automatic"]["receivers"][0]["filter_mnr"],1)

    def test_arbitration_window_candidates_follow_each_cycle(self):
        c=automatic()
        c["messages"]=[dict(name="Even",can_id="0x240",sender="Node0",period_cycles=2,phase=0,
                            trigger_type="arbi",time_mark=500,group_id="Shared"),
                       dict(name="Odd",can_id="0x241",sender="Node1",period_cycles=2,phase=1,
                            trigger_type="arbi",time_mark=500,group_id="Shared")]
        c["arbitration_groups"]=[dict(group_id="Shared",kind="arbitration",start_ntu=500,end_ntu=900)]
        s=Scheduler(c).compile()
        self.assertTrue(s.valid,s.report())
        shared=[w for w in s.windows if w.group_id=="Shared"]
        self.assertEqual([w.messages for w in shared],[["Even"],["Odd"],["Even"],["Odd"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
