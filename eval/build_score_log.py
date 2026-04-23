#!/usr/bin/env python3
"""
Convert tau2-bench results.json → score_log.json + trace_log.jsonl
for Act I baseline deliverable.

Usage:
    python eval/build_score_log.py \
        /home/yosef/Desktop/intensive/tau2-bench/data/simulations/act1_baseline_dev/results.json \
        --out-dir eval/
"""

import argparse
import json
import math
import statistics
from pathlib import Path


def bootstrap_ci(values: list[float], n_boot: int = 2000, alpha: float = 0.05) -> tuple[float, float]:
    """Return (lower, upper) 95% bootstrap CI for the mean."""
    import random
    n = len(values)
    if n == 0:
        return (0.0, 0.0)
    boot_means = []
    for _ in range(n_boot):
        sample = [random.choice(values) for _ in range(n)]
        boot_means.append(statistics.mean(sample))
    boot_means.sort()
    lo_idx = int(math.floor((alpha / 2) * n_boot))
    hi_idx = int(math.ceil((1 - alpha / 2) * n_boot)) - 1
    return (boot_means[lo_idx], boot_means[hi_idx])


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = (p / 100) * (len(sorted_v) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (sorted_v[hi] - sorted_v[lo]) * (idx - lo)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results_json", help="Path to tau2 results.json")
    parser.add_argument("--out-dir", default="eval/", help="Output directory")
    args = parser.parse_args()

    results_path = Path(args.results_json)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(results_path) as f:
        data = json.load(f)

    sims = data.get("simulations", [])
    if not sims:
        print("ERROR: no simulations found in results.json")
        return

    run_info = data.get("info", {})
    run_model = run_info.get("agent_llm", "openrouter/qwen/qwen3-235b-a22b") if run_info else "openrouter/qwen/qwen3-235b-a22b"

    # Group by task_id
    by_task: dict[str, list[dict]] = {}
    for s in sims:
        tid = str(s.get("task_id", "unknown"))
        by_task.setdefault(tid, []).append(s)

    # Compute per-task pass@1 = successes / trials
    task_pass_rates = []
    task_latencies = []
    for task_id, trials in sorted(by_task.items()):
        successes = sum(
            1 for t in trials
            if (t.get("reward_info") or {}).get("reward", 0) == 1.0
        )
        rate = successes / len(trials)
        task_pass_rates.append(rate)

        # Latency: tau2 uses 'duration' field
        for t in trials:
            wt = t.get("duration") or t.get("wall_time_seconds") or t.get("duration_seconds")
            if wt is not None:
                task_latencies.append(float(wt))

    mean_pass1 = statistics.mean(task_pass_rates) if task_pass_rates else 0.0
    ci_lo, ci_hi = bootstrap_ci(task_pass_rates)

    # Cost: tau2 uses separate agent_cost + user_cost fields
    total_cost_usd = 0.0
    for s in sims:
        agent_cost = float(s.get("agent_cost") or 0.0)
        user_cost = float(s.get("user_cost") or 0.0)
        total_cost_usd += agent_cost + user_cost
    cost_per_run = total_cost_usd / len(sims) if sims else 0.0

    p50 = percentile(task_latencies, 50) if task_latencies else None
    p95 = percentile(task_latencies, 95) if task_latencies else None

    num_tasks = len(by_task)
    num_trials = max(len(v) for v in by_task.values()) if by_task else 0

    score_log = {
        "run_name": args.results_json.split("/simulations/")[-1].split("/")[0],
        "model": run_model,
        "domain": "retail",
        "seed": 42,
        "num_tasks": num_tasks,
        "num_trials": num_trials,
        "pass_at_1_mean": round(mean_pass1, 4),
        "pass_at_1_ci_95_lower": round(ci_lo, 4),
        "pass_at_1_ci_95_upper": round(ci_hi, 4),
        "total_cost_usd": round(total_cost_usd, 4),
        "cost_per_run_usd": round(cost_per_run, 6),
        "latency_p50_seconds": round(p50, 2) if p50 is not None else None,
        "latency_p95_seconds": round(p95, 2) if p95 is not None else None,
        "per_task": [
            {
                "task_id": tid,
                "pass_at_1": round(statistics.mean([
                    1.0 if (t.get("reward_info") or {}).get("reward", 0) == 1.0 else 0.0
                    for t in trials
                ]), 4),
                "trials": len(trials),
            }
            for tid, trials in sorted(by_task.items())
        ],
    }

    score_log_path = out_dir / "score_log.json"
    with open(score_log_path, "w") as f:
        json.dump(score_log, f, indent=2)
    print(f"score_log.json written → {score_log_path}")
    print(f"  pass@1 mean:  {mean_pass1:.1%}  (95% CI: {ci_lo:.1%} – {ci_hi:.1%})")
    print(f"  tasks/trials: {num_tasks} tasks × {num_trials} trials")
    print(f"  total cost:   ${total_cost_usd:.4f}  (${cost_per_run:.6f}/run)")
    if p50:
        print(f"  latency:      p50={p50:.1f}s  p95={p95:.1f}s")
    else:
        print(f"  latency:      not recorded (model cost mapping failed)")

    # Write trace_log.jsonl — one line per simulation
    trace_path = out_dir / "trace_log.jsonl"
    with open(trace_path, "w") as f:
        for s in sims:
            ri = s.get("reward_info") or {}
            trace = {
                "sim_id": s.get("id"),
                "task_id": s.get("task_id"),
                "trial": s.get("trial"),
                "reward": ri.get("reward"),
                "db_match": (ri.get("db_check") or {}).get("db_match"),
                "termination_reason": s.get("termination_reason"),
                "duration_seconds": s.get("duration"),
                "agent_cost_usd": s.get("agent_cost"),
                "user_cost_usd": s.get("user_cost"),
                "num_messages": len(s.get("messages") or []),
                "messages": s.get("messages"),
            }
            f.write(json.dumps(trace) + "\n")
    print(f"trace_log.jsonl written → {trace_path}  ({len(sims)} entries)")


if __name__ == "__main__":
    main()
