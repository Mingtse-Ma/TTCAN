"""Integration and property tests for the constrained random generator."""
import contextlib
import copy
import io
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ttcan_random_config as randomizer
from ttcan_scheduler import ROOT, Scheduler, decode_cycle_code, scan_english


class RandomGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.options = randomizer.resolve_options()
        self.sha = randomizer.source_hash()

    def generate(self, seed=20261029, index=0, options=None, attempts=100):
        result = randomizer.make_case(options or self.options, seed, index, attempts, self.sha)
        self.assertTrue(result["success"], result.get("failure"))
        return result

    def test_same_seed_is_byte_reproducible(self):
        first = self.generate()
        second = self.generate()
        self.assertEqual(randomizer.canonical(first["schedule"].config), randomizer.canonical(second["schedule"].config))
        self.assertEqual(first["identity"], second["identity"])
        self.assertEqual(first["signature"], second["signature"])
        self.assertEqual(first["attempts"], second["attempts"])

    def test_seeds_vary_structure_and_timing(self):
        samples = [self.generate(seed) for seed in (1,2,3,4,5)]
        shapes = {(r["stats"]["node_count"], r["stats"]["matrix_cycles"], r["stats"]["basic_cycle_ntu"],
                   tuple(sorted(r["stats"]["logical_window_counts"].items()))) for r in samples}
        self.assertGreaterEqual(len(shapes), 3)

    def test_fixed_topology_and_resource_capacities(self):
        topology=json.loads((ROOT / "examples" / "random_nodes.json").read_text())
        o=randomizer.resolve_options(topology=topology, messages=3)
        s=self.generate(options=o)["schedule"]
        self.assertEqual(s.net["actual_master"],"NodeA")
        self.assertEqual([n["role"] for n in s.nodes.values()],["master","slave","slave"])
        self.assertTrue(all(n["tx_message_capacity"]==8 and n["trigger_capacity"]==32 for n in s.nodes.values()))
        self.assertEqual(Counter(m["sender"] for m in s.messages.values()), {"NodeA":3,"NodeB":3,"NodeC":3})
        with self.assertRaises(randomizer.GenerationError):
            randomizer.resolve_options(topology=topology,nodes=4)
        with self.assertRaises(randomizer.GenerationError):
            randomizer.resolve_options(topology=topology,messages=9)

    def test_fixed_options_and_hardware_profile(self):
        options=randomizer.resolve_options({"resource_mode":"m_ttcan", "controller_version":"3.3.0",
             "matrix_cycles":[8], "periods":[1,2,4,8], "basic_cycle_ntu":3000, "frame_formats":["standard"]},nodes=3,masters=1,messages=2)
        s=self.generate(seed=5,options=options)["schedule"]
        self.assertEqual((len(s.nodes),s.cycles,s.basic),(3,8,3000))
        self.assertEqual({m["frame_format"] for m in s.messages.values()},{"standard"})
        self.assertEqual(s.mode,"m_ttcan")
        for ts in s.triggers.values():
            for t in ts:
                if t.period_cycles:
                    self.assertEqual(decode_cycle_code(t.record()["cycle_code"]),(t.period_cycles,t.phase))

    def test_random_window_start_and_length_bounds(self):
        o=randomizer.resolve_options({"node_count":[2,2],"master_count":[1,1],"messages_per_node":[1,1],
            "matrix_cycles":[4],"periods":[1],"dlc":[8,8],"basic_cycle_ntu":[3000,3000],
            "window_mix":{"single":1,"arbitration":0,"merged":0},
            "window_start_ntu":[500,1200],"window_length_ntu":[100,350]})
        r=self.generate(seed=11,options=o)
        self.assertGreater(r["stats"]["global_time_shift_ntu"],0)
        for w in r["schedule"].windows:
            if w.kind in ("exclusive","arbitration","merged"):
                self.assertGreaterEqual(w.start_ntu,500)
                self.assertLessEqual(w.start_ntu,1200)
                self.assertGreaterEqual(w.end_ntu-w.start_ntu,100)
                self.assertLessEqual(w.end_ntu-w.start_ntu,350)
        self.assertEqual(randomizer.core_signature(r["schedule"]),
                         randomizer.core_signature(Scheduler(r["schedule"].config).compile()))

    def test_explicit_and_automatic_mnr_and_resource_limits(self):
        for p, tx_key, rx_key in ((1.0,"explicit_tx","explicit_rx"),(0.0,"automatic_tx","automatic_rx")):
            o=randomizer.resolve_options({"explicit_number_probability":p})
            r=self.generate(seed=31,options=o)
            styles=r["stats"]["allocation_styles"]
            self.assertEqual(styles[tx_key],r["stats"]["message_count"])
            self.assertGreater(styles[rx_key],0)
            for n,resources in r["schedule"].resources.items():
                for resource in ("tx_buffer","rx_std_filter","rx_ext_filter","trigger_memory"):
                    self.assertLessEqual(resources[resource]["used"],resources[resource]["capacity"],n)

    def test_merged_closure_and_no_arbitration_rx(self):
        o=randomizer.resolve_options({"required_features":["merged","rx","multi_node_arbitration"]})
        r=self.generate(seed=17,options=o,attempts=300)
        s=r["schedule"]
        self.assertTrue({"merged","rx","multi_node_arbitration"}.issubset(r["stats"]["features"]))
        for m in s.messages.values():
            if any(t["trigger_type"] in ("arbi","merged") for t in m["transmissions"]):
                for receiver in m["receivers"]:
                    self.assertFalse(receiver.get("check_reception",False))
                    self.assertFalse(receiver.get("rx_checks",[]))
        for node, triggers in s.triggers.items():
            self.assertTrue(all(t.alias not in ("0x3","0x9") for t in triggers))
            for cycle in range(s.cycles):
                opened=None
                for t in triggers:
                    if cycle not in t.active_cycles or t.alias not in ("single","arbi","merged"):
                        continue
                    if t.alias=="merged":
                        self.assertIn(opened,(None,t.group_id))
                        opened=t.group_id
                    elif opened:
                        self.assertEqual((t.alias,t.group_id),("arbi",opened))
                        opened=None
                self.assertIsNone(opened,(node,cycle))

    def test_ordinary_shared_arbitration_uses_common_mark(self):
        found=False
        for seed in range(60):
            s=self.generate(seed=seed)["schedule"]
            groups={w.group_id for w in s.windows if w.kind=="arbitration" and len(w.nodes)>1}
            if groups:
                found=True
                for group in groups:
                    times={t.time_mark_ntu for ts in s.triggers.values() for t in ts if t.group_id==group}
                    self.assertEqual(len(times),1)
                break
        self.assertTrue(found)

    def test_rx_worst_case_and_filter_result(self):
        o=randomizer.resolve_options({"required_features":["rx"]})
        s=self.generate(seed=8,options=o)["schedule"]
        for nid, ts in s.triggers.items():
            for t in (x for x in ts if x.alias=="rx"):
                m=s.messages[t.message_name]
                for cycle in t.active_cycles:
                    matching=[r for r in s.requests if r["message"]["name"]==m["name"] and r["alias"]=="single" and cycle in r["cycles"]]
                    self.assertTrue(any(r["mark"]+s.txew+m["frame_time_ntu"]+s.delay<t.time_mark_ntu for r in matching),(nid,cycle))
        self.assertFalse(any(i.code.startswith("RX_") for i in s.issues))

    def test_retry_bound_failure_and_no_partial_case(self):
        o=randomizer.resolve_options({"required_features":["high_load"], "target_bus_load":[0.70,1.0],
            "node_count":[2,2], "master_count":[1,1], "messages_per_node":[1,1],
            "matrix_cycles":[16], "periods":[16], "basic_cycle_ntu":[5000,5000],
            "window_mix":{"single":1,"arbitration":0,"merged":0}})
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory,manifest=randomizer.run_batch(o,123,1,0,3,Path(temp))
            self.assertEqual(manifest["summary"]["failed"],1)
            self.assertFalse((directory/"case_0000").exists())
            report=json.loads((directory/"failures"/"case_0000.json").read_text())
            self.assertEqual(len(report["attempts"]),3)
            self.assertTrue((directory/"index.html").exists())

    def test_warning_policy_is_explicit(self):
        strict=self.generate(seed=63)
        permissive=self.generate(seed=63,options=randomizer.resolve_options(allow_warnings=True))
        self.assertEqual(strict["schedule"].report()["warnings"],0)
        self.assertGreater(len(strict["attempts"]),0)
        self.assertEqual(permissive["identity"]["attempt"],0)
        self.assertGreater(permissive["schedule"].report()["warnings"],0)
        self.assertTrue(any(i.code=="RX_ARBITRATION_UNCERTAIN" for i in permissive["schedule"].issues))
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            directory, manifest = randomizer.run_batch(randomizer.resolve_options(allow_warnings=True),63,1,0,100,Path(temp))
            self.assertEqual(manifest["summary"]["cases_with_warnings"],1)
            self.assertIn(f"Warnings: {permissive['schedule'].report()['warnings']}",(directory/"index.html").read_text())

    def test_retry_in_one_case_does_not_change_another(self):
        expected=self.generate(seed=20261029,index=1)
        real=randomizer.build_candidate
        def fail_case_zero(options,rng,identity):
            if identity["case_index"]==0:
                raise randomizer.GenerationError("TEST_FAILURE","Injected bounded failure")
            return real(options,rng,identity)
        with tempfile.TemporaryDirectory() as temp, patch.object(randomizer,"build_candidate",side_effect=fail_case_zero), contextlib.redirect_stdout(io.StringIO()):
            directory,manifest=randomizer.run_batch(self.options,20261029,2,0,100,Path(temp))
            self.assertEqual([x["status"] for x in manifest["cases"]],["failed","success"])
            actual=json.loads((directory/"case_0001"/"config.json").read_text())
            self.assertEqual(randomizer.canonical(actual),randomizer.canonical(expected["schedule"].config))
            failures=json.loads((directory/"failures"/"case_0000.json").read_text())
            self.assertEqual(len(failures["attempts"]),100)

    def test_case_replay_complete_files_links_and_hashes(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root=Path(temp)
            batch,manifest=randomizer.run_batch(self.options,20261029,3,0,100,root)
            solo,one=randomizer.run_batch(self.options,20261029,1,2,100,root)
            first=json.loads((batch/"case_0002"/"config.json").read_text())
            second=json.loads((solo/"case_0002"/"config.json").read_text())
            self.assertEqual(first,second)
            index=(batch/"index.html").read_text()
            self.assertIn('href="case_0002/schedule.html"',index)
            report=json.loads((batch/"case_0002"/"generation_report.json").read_text())
            self.assertEqual(report["case_index"],2)
            self.assertEqual(report["core_signature"],manifest["cases"][2]["core_signature"])
            command=shlex.split(report["reproduce_command"])
            proc=subprocess.run(command,cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
            replay=subprocess.run([sys.executable,str(ROOT/"ttcan_scheduler.py"),"--config",str(batch/"case_0002"/"config.json"),"--output-dir",str(root/"replay")],capture_output=True,text=True)
            self.assertEqual(replay.returncode,0,replay.stderr+replay.stdout)
            for path in ("system_matrix.json","nodes"):
                if path=="nodes":
                    for original in (batch/"case_0002"/"nodes").glob('*.json'):
                        self.assertEqual(json.loads(original.read_text()),json.loads((root/"replay"/"nodes"/original.name).read_text()))
                else:
                    self.assertEqual(json.loads((batch/"case_0002"/path).read_text()),json.loads((root/"replay"/path).read_text()))
            html=(batch/"case_0002"/"schedule.html").read_text()
            self.assertIn('id="generation-data"',html)
            self.assertIn('reproduce_command',html)
            self.assertFalse(scan_english(batch))
            self.assertEqual(one["summary"]["succeeded"],1)

    def test_impossible_options_and_unsupported_fields(self):
        for opts in ({"periods":[64],"matrix_cycles":[4]}, {"window_mix":{"single":0,"merged":0,"arbitration":0}},
                     {"node_count":[2,1]}, {"unknown_option":1}, {"required_features":["unknown"]},
                     {"required_features":["extended"],"frame_formats":["standard"]},
                     {"required_features":["high_load"],"target_bus_load":[0,0.5]}):
            with self.assertRaises(randomizer.GenerationError):
                randomizer.resolve_options(opts)
        with self.assertRaises(randomizer.GenerationError):
            randomizer.resolve_options(nodes=1)
        with self.assertRaises(randomizer.GenerationError):
            randomizer.resolve_options(messages=17)

    def test_duplicate_is_reported_not_counted(self):
        original=self.generate(seed=3,index=0)
        def same_case(*args,**kwargs):
            return copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as temp, patch.object(randomizer,"make_case",side_effect=same_case), contextlib.redirect_stdout(io.StringIO()):
            directory,manifest=randomizer.run_batch(self.options,3,2,0,100,Path(temp))
            self.assertEqual(manifest["summary"]["succeeded"],1)
            self.assertEqual(manifest["cases"][1]["failure"]["code"],"DUPLICATE_CONFIG")
            self.assertFalse((directory/"case_0001").exists())

    def test_automatic_seed_and_expected_cli_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            p=subprocess.run([sys.executable,str(ROOT/"ttcan_random_config.py"),"--output-dir",temp],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr+p.stdout)
            self.assertIn("Master seed:",p.stdout)
            run=next(Path(temp).glob('run_*'))
            report=json.loads((run/"manifest.json").read_text())
            self.assertTrue(report["master_seed"].isdigit())
            bad=subprocess.run([sys.executable,str(ROOT/"ttcan_random_config.py"),"--nodes","1","--output-dir",temp],cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(bad.returncode,2)
            self.assertIn("INFEASIBLE_CONSTRAINT",bad.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
