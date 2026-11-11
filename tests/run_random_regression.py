#!/usr/bin/env python3
"""Run a reproducible in-memory random-seed regression and save its summary."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ttcan_random_config import (GENERATOR_VERSION, GenerationError, digest, make_case,
                                 read_json_file, resolve_options, source_hash)
from ttcan_scheduler import CJK, ROOT, write_json


def main():
    parser=argparse.ArgumentParser(description="Run in-memory TTCAN random seed regression")
    parser.add_argument("--start-seed",type=int,default=0)
    parser.add_argument("--count",type=int,default=100)
    parser.add_argument("--max-attempts",type=int,default=100)
    parser.add_argument("--options",type=Path)
    parser.add_argument("--output",type=Path,default=Path("random_regression_report.json"))
    args=parser.parse_args()
    try:
        if CJK.search(str(args.output)):
            raise GenerationError("LANGUAGE","Output path must not contain CJK characters")
        if args.count<1 or args.count>10000 or args.start_seed<0 or args.start_seed+args.count>2**256 or args.max_attempts<1:
            raise GenerationError("INVALID_OPTIONS","Invalid seed range, count, or attempt limit")
        options=resolve_options(read_json_file(args.options) if args.options else {})
        scheduler_sha=source_hash()
        feature_counts, issues, formats, periods, windows, dlcs, nodes=([Counter() for _ in range(7)])
        entries=[]
        for seed in range(args.start_seed,args.start_seed+args.count):
            result=make_case(options,seed,0,args.max_attempts,scheduler_sha)
            issue_counts=Counter(i.get("code","UNKNOWN") for a in result["attempts"] for i in a["issues"])
            issues.update(issue_counts)
            entry=dict(seed=str(seed),case_index=0,case_seed=result["identity"]["case_seed"],
                       success=result["success"],failed_attempts=len(result["attempts"]),issue_counts=dict(issue_counts))
            if result["success"]:
                c=result["stats"]
                feature_counts.update(c["features"]);formats.update(c["formats"]);periods.update(c["periods"])
                windows.update(c["logical_window_counts"]);dlcs.update(c["dlcs"]);nodes.update([str(c["node_count"])])
                entry.update(attempt=result["identity"]["attempt"],features=c["features"],
                             logical_window_counts=c["logical_window_counts"],reserved_load=c["data_window_reservation_fraction"],
                             config_semantic_sha256=result["config_semantic_sha256"],core_signature=result["signature"])
            else:
                entry["failure"]=result["failure"]
            entries.append(entry)
        passed=sum(x["success"] for x in entries)
        report=dict(generator_version=GENERATOR_VERSION, python_version=".".join(map(str,sys.version_info[:3])),
                    scheduler_sha256=scheduler_sha, generator_sha256=hashlib.sha256((ROOT/"ttcan_random_config.py").read_bytes()).hexdigest(),
                    options_sha256=digest(options),options=options,start_seed=str(args.start_seed),requested=args.count,
                    succeeded=passed,failed=args.count-passed,max_attempts=args.max_attempts,
                    total_failed_attempts=sum(x["failed_attempts"] for x in entries),
                    issue_counts=dict(sorted(issues.items())),feature_counts=dict(sorted(feature_counts.items())),
                    logical_window_counts=dict(sorted(windows.items())),format_counts=dict(sorted(formats.items())),
                    period_counts=dict(sorted(periods.items())),dlc_counts=dict(sorted(dlcs.items())),
                    node_count_distribution=dict(sorted(nodes.items())),seed_results=entries,
                    protocol_conformance_claim=False,
                    scope="In-memory candidate compilation and replay; no sample configs are published by this regression")
        write_json(args.output,report)
        print(f"Regression: {passed}/{args.count} passed; failed attempts={report['total_failed_attempts']}; report={args.output}")
        print("Feature coverage: "+json.dumps(report["feature_counts"],sort_keys=True))
        return 0 if passed==args.count else 1
    except (GenerationError,OSError,ValueError,TypeError,KeyError) as exc:
        safe=str(exc).encode("ascii","backslashreplace").decode("ascii")
        print("ERROR: "+safe,file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
