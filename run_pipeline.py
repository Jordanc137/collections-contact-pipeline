# =============================================================================
# run_pipeline.py - Pipeline Orchestrator
#
# Execution phases:
#   SEQUENTIAL (each reads the previous step's output):
#     1. step1_consolidate.py  — CDR ingestion → cdr_master.parquet
#     2. step2_enrich.py       — campaign enrichment → cdr_enriched.parquet
#     3. step3_aggregate.py    — KPI aggregation → dashboard_data.json
#
#   PARALLEL (all read cdr_enriched.parquet independently):
#     Lane A: stepE_strategy.py
#     Lane B: step7_excluded.py
#     Lane C: step4_payments.py → step4b_payment_dist.py  (sequential within lane)
#     Lane D: step8_best_hour.py
#     Lane E: step9_payment_cutoff.py  (if present)
#
#   FINAL (waits for all parallel lanes):
#     5. step5_dashboard.py    — assembles HTML dashboard from all JSON outputs
#
# Error handling:
#   Parallel steps exit 0 on missing optional inputs (never blocks the pipeline).
#   Non-zero exit from any parallel step is reported after the group finishes,
#   then the pipeline stops before building the dashboard.
# =============================================================================
import subprocess
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

SEQUENTIAL_STEPS = [
    "step1_consolidate.py",
    "step2_enrich.py",
    "step3_aggregate.py",
]

PARALLEL_LANES = [
    ["stepE_strategy.py"],
    ["step7_excluded.py"],
    ["step4_payments.py", "step4b_payment_dist.py"],
    ["step8_best_hour.py"],
    ["step9_payment_cutoff.py"],
]

FINAL_STEP = "step5_dashboard.py"


def run_script(script_name):
    path = os.path.join(BASE, script_name)
    if not os.path.exists(path):
        print(f"  [SKIP] {script_name} not found — skipping")
        return script_name, 0, 0.0

    t0 = time.time()
    result = subprocess.run(
        [PYTHON, path],
        capture_output=False,
    )
    elapsed = time.time() - t0
    return script_name, result.returncode, elapsed


def run_lane(scripts):
    """Run a list of scripts sequentially (one lane in the parallel group)."""
    for script in scripts:
        name, code, elapsed = run_script(script)
        if code != 0:
            return name, code, elapsed
    return scripts[-1], 0, 0.0


def main():
    total_start = time.time()
    print("=" * 60)
    print("COLLECTIONS CONTACT PIPELINE")
    print("=" * 60)

    # --- Sequential phase ---
    print("\n[Phase 1] Sequential steps")
    for script in SEQUENTIAL_STEPS:
        print(f"\n→ {script}")
        name, code, elapsed = run_script(script)
        if code != 0:
            print(f"\n[ERROR] {name} failed (exit {code}) — pipeline stopped.")
            sys.exit(1)
        print(f"  ✓ {name} ({elapsed:.1f}s)")

    # --- Parallel phase ---
    print("\n[Phase 2] Parallel steps")
    failed = []
    with ThreadPoolExecutor(max_workers=len(PARALLEL_LANES)) as executor:
        futures = {executor.submit(run_lane, lane): lane for lane in PARALLEL_LANES}
        for future in as_completed(futures):
            name, code, elapsed = future.result()
            if code != 0:
                failed.append((name, code))
                print(f"  ✗ {name} failed (exit {code})")
            else:
                print(f"  ✓ {name} ({elapsed:.1f}s)")

    if failed:
        print(f"\n[ERROR] {len(failed)} parallel step(s) failed: "
              f"{[n for n, _ in failed]} — pipeline stopped before dashboard.")
        sys.exit(1)

    # --- Final step ---
    print(f"\n[Phase 3] {FINAL_STEP}")
    name, code, elapsed = run_script(FINAL_STEP)
    if code != 0:
        print(f"\n[ERROR] {name} failed (exit {code})")
        sys.exit(1)

    total = time.time() - total_start
    print(f"\n{'=' * 60}")
    print(f"Pipeline complete in {total:.1f}s")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
