"""Run the full synthetic caller suite with one resident real model group."""
import argparse
import asyncio
import json
import os
import sys
import re
from datetime import datetime, timezone
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.runner import run_suite
from server.runtime import Runtime


async def main(args):
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    profile_dir=ROOT/'benchmarks/phase3/logs'/stamp
    profile_dir.mkdir(parents=True,exist_ok=True)
    os.environ['EYWA_PROFILE_DIR']=str(profile_dir)
    runtime = Runtime(args.profile)
    report = None
    try:
        await runtime.start()
        report = await run_suite(runtime, ROOT / "runtime/eval", seed=args.seed, limit=args.limit,
            reference_date=args.reference_date, scenario_ids=args.only,
            progress=lambda i,n,name: print(f"REAL {i}/{n}: {name}", flush=True))
    finally:
        await runtime.close()
    if report:
        peaks={}
        for path in profile_dir.glob('*.txt'):
            match=re.search(r'(\d+)\s+maximum resident set size',path.read_text())
            if match:
                name='piper' if path.stem.startswith('piper-') else path.stem
                peaks[name]=max(peaks.get(name,0),int(match[1]))
        self_peak=profile_dir/'gateway-self.json'
        if self_peak.exists():peaks['runner']=json.loads(self_peak.read_text())['max_rss_bytes']
        report['memory']={'method':'native/Piper time -l maxima + runner RUSAGE_SELF; replaced Piper workers do not overlap',
            'process_max_rss_bytes':peaks,'sum_max_rss_bytes':sum(peaks.values()) if len(peaks)==4 else None,
            'raw_time_output':{p.stem:p.read_text() for p in profile_dir.glob('*.txt')}}
        directory = ROOT / "benchmarks/phase3/results"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / (report["id"] + ".json")
        target.write_text(json.dumps(report, indent=2) + "\n")
        print(target.relative_to(ROOT))
        print(json.dumps(report["summary"], indent=2))
        (ROOT/'runtime/eval'/report['id']/'report.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["compact", "balanced"], default=os.environ.get("EYWA_MODEL_PROFILE", "compact"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=None)
    parser.add_argument("--only", nargs='+', default=None, help="Debug selected scenario IDs, preserving their original perturbation seeds")
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= 30:
        parser.error("limit must be between 1 and 30")
    asyncio.run(main(args))
