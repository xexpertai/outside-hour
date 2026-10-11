"""Measure the real local model on a few fixed scenarios (cached open data, so runs are comparable).

Usage: python scripts/bench.py [--runs 2]   (llama-server must be running; see scripts/start-model.sh)
Writes docs/bench-results.json and prints a summary table.
"""
import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from outside_hour import llm  # noqa: E402
from outside_hour.cli import PRESETS  # noqa: E402
from outside_hour.planner import make_plan  # noqa: E402
from outside_hour.sources import Sources  # noqa: E402

SCENARIOS = [
    ("mg-road", 30, "07:00"), ("mg-road", 60, "07:00"), ("mg-road", 90, "16:00"),
    ("lalbagh", 45, "06:30"), ("lalbagh", 60, "17:30"), ("pune-empress", 45, "07:00"),
    ("pune-empress", 60, "18:30"), ("mg-road", 45, "12:30"), ("lalbagh", 90, "07:00"), ("pune-empress", 30, "16:30"),
]


def cpu_name():
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1)  # >1 repeats identical prompts (prompt cache hits)
    a = ap.parse_args()
    src = Sources(offline=os.environ.get("BENCH_ONLINE") != "1")
    client = llm.LlamaServer()
    rows = []
    for r in range(a.runs):
        for preset, minutes, start in SCENARIOS:
            label, lat, lon = PRESETS[preset]
            t = time.perf_counter()
            p = make_plan(src, lat, lon, minutes, label, llm_client=client, start_at=start)
            rows.append({
                "run": r, "preset": preset, "minutes": minutes, "start": start,
                "seconds": round(time.perf_counter() - t, 2), "writer": p.writer, "place": p.place["name"],
                "rejected": p.rejected, "stats": p.model_stats, "headline": p.headline,
                "notice": p.notice, "phone_down_line": p.phone_down_line,
            })
            print(f"{preset:13} {minutes:3} {start}  {rows[-1]['seconds']:6.1f}s  "
                  f"{'MODEL' if p.writer != 'template' else 'TEMPLATE'}  rejected={len(p.rejected)}  {p.place['name']}",
                  flush=True)
    secs = [x["seconds"] for x in rows]
    gen = [x["stats"]["output_tok_per_s"] for x in rows if x["stats"].get("output_tok_per_s")]
    summary = {
        "cpu": cpu_name(), "threads": os.cpu_count(), "model": client.model_name(),
        "model_file_bytes": (ROOT / "models" / "gemma-4-E2B-it-Q4_0.gguf").stat().st_size
        if (ROOT / "models" / "gemma-4-E2B-it-Q4_0.gguf").exists() else None,
        "plans": len(rows),
        "plans_written_by_model": sum(x["writer"] != "template" for x in rows),
        "plans_fell_back_to_template": sum(x["writer"] == "template" for x in rows),
        "drafts_rejected_total": sum(len(x["rejected"]) for x in rows),
        "seconds_median": statistics.median(secs), "seconds_min": min(secs), "seconds_max": max(secs),
        "output_tok_per_s_median": statistics.median(gen) if gen else None,
        "prompt_tokens_median": statistics.median([x["stats"]["prompt_tokens"] for x in rows if x["stats"]]),
        "prompt_tok_per_s_median": statistics.median([x["stats"]["prompt_tok_per_s"] for x in rows if x["stats"]]),
        "output_tokens_median": statistics.median([x["stats"]["output_tokens"] for x in rows if x["stats"]]),
        "model_seconds_median": statistics.median([x["stats"]["wall_s"] for x in rows if x["stats"]]),
    }
    out = {"summary": summary, "rows": rows}
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "bench-results.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
