"""M2 Adversarial Concurrency & Dynamic Stress Verification Harness.

Challenger: teamwork_preview_challenger_m2_1
Focus:
- Repeatability & Flake-resistance under high-iteration test execution
- Unblocking latency distribution (< 0.5s requirement)
- Zero deadlocks under heavy thread contention
- Clean thread lifecycle and absence of zombie threads
- Strict mutual exclusion and slot uniqueness
- Invariant bounds: min_interval <= cadence <= max_cadence
"""

import concurrent.futures
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from typing import List, Tuple

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.coordinator import ClaimCoordinator


def stress_test_1_unblock_latency_and_no_zombies():
    """Stress 1: High-frequency unblock latency and thread cleanup.

    Repeats 30 full cycles of spawning 20 threads blocking in acquire_next_turn,
    triggering stop_event, measuring latency, and confirming all threads terminate.
    """
    print("\n[M2 STRESS 1] 30-Cycle Rapid Unblock Latency & Zero Zombie Threads...")
    latencies_ms = []

    for cycle in range(30):
        with tempfile.TemporaryDirectory() as tmpdir:
            coord = ClaimCoordinator(
                ["FD-1", "FD-2"],
                os.path.join(tmpdir, "success.txt"),
                os.path.join(tmpdir, "status.json"),
                cadence_seconds=30.0,
                min_interval_seconds=14.0,
                max_cadence=45.0,
            )
            coord.broadcast_rate_limit("Attacker", cooldown_seconds=60.0)

            num_threads = 20
            results = [None] * num_threads
            barrier = threading.Barrier(num_threads + 1)
            threads = []

            def worker(idx):
                barrier.wait()
                results[idx] = coord.acquire_next_turn(f"Worker-{idx}")

            for i in range(num_threads):
                t = threading.Thread(target=worker, args=(i,), daemon=True)
                threads.append(t)
                t.start()

            barrier.wait()
            time.sleep(0.01)  # allow workers to enter wait

            t0 = time.perf_counter()
            coord.stop_event.set()

            for t in threads:
                t.join(timeout=1.0)
                assert not t.is_alive(), f"Cycle {cycle}: Thread {t.name} hung!"

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            for idx, res in enumerate(results):
                assert res == (0, None), f"Cycle {cycle}: Worker {idx} got {res}, expected (0, None)"
            assert coord.total_attempts == 0

    p50 = statistics.median(latencies_ms)
    p95 = sorted(latencies_ms)[int(len(latencies_ms) * 0.95)]
    max_lat = max(latencies_ms)
    print(f"  -> 30 Cycles Completed (600 total thread wakeups)")
    print(f"  -> Latency: median={p50:.2f}ms, p95={p95:.2f}ms, max={max_lat:.2f}ms")
    assert max_lat < 500.0, f"Max latency {max_lat:.2f}ms exceeded 500ms threshold!"
    print("  -> PASSED: All 30 cycles unblocked in < 500ms with zero zombie threads.")


def stress_test_2_continuous_contention_deadlock_freedom():
    """Stress 2: Continuous multi-threaded contention under asynchronous rate-limits and stops.

    Spawns 40 worker threads in a free-for-all acquiring turns for 0.5s while 10 threads
    fire rate-limit broadcasts and clean checks, followed by asynchronous shutdown.
    Ensures zero deadlocks and total attempt integrity.
    """
    print("\n[M2 STRESS 2] High-Contention Deadlock Freedom (40 Workers, 10 Spammers)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        coord = ClaimCoordinator(
            ["FD-1", "FD-2", "FD-3"],
            os.path.join(tmpdir, "success.txt"),
            os.path.join(tmpdir, "status.json"),
            cadence_seconds=0.002,
            min_interval_seconds=0.001,
            max_cadence=40.0,
        )

        turns_acquired = []
        lock = threading.Lock()
        active = True

        def claimer(wid):
            while not coord.is_stopped():
                att, fd = coord.acquire_next_turn(f"Claimer-{wid}")
                if att > 0:
                    with lock:
                        turns_acquired.append((att, fd, wid))
                else:
                    break

        def spammer(sid):
            while not coord.is_stopped():
                if sid % 2 == 0:
                    coord.broadcast_rate_limit(f"Spammer-{sid}", cooldown_seconds=0.005)
                else:
                    coord.record_capacity_check()
                time.sleep(0.002)

        threads = []
        for i in range(40):
            threads.append(threading.Thread(target=claimer, args=(i,), daemon=True))
        for j in range(10):
            threads.append(threading.Thread(target=spammer, args=(j,), daemon=True))

        for t in threads:
            t.start()

        time.sleep(0.3)
        coord.stop_event.set()

        for t in threads:
            t.join(timeout=2.0)
            assert not t.is_alive(), f"Thread {t.name} deadlocked!"

        attempts = [item[0] for item in turns_acquired]
        assert len(attempts) == len(set(attempts)), "Duplicate attempt numbers allocated during contention!"
        if attempts:
            assert sorted(attempts) == list(range(1, len(attempts) + 1)), "Attempt numbers non-sequential!"
        assert coord.is_stopped()
        print(f"  -> Contention Completed: {len(attempts)} turns allocated safely with zero deadlocks.")


def stress_test_3_cadence_invariant_bounds():
    """Stress 3: Strict invariant testing: min_interval <= cadence <= max_cadence.

    Fires 1,000 mixed interleaved operations (AIMD acceleration vs 429 rate limit boosts)
    and verifies at every step that cadence never violates bounds.
    """
    print("\n[M2 STRESS 3] Cadence Invariant Bounds Verification (1,000 Operations)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        min_int = 14.0
        max_cad = 45.0
        coord = ClaimCoordinator(
            ["FD-1"],
            os.path.join(tmpdir, "success.txt"),
            os.path.join(tmpdir, "status.json"),
            cadence_seconds=20.0,
            min_interval_seconds=min_int,
            max_cadence=max_cad,
        )

        lock = threading.Lock()
        violations = []

        def worker(wid):
            for op in range(50):
                if op % 3 == 0:
                    coord.broadcast_rate_limit(f"W-{wid}", cooldown_seconds=1.0)
                else:
                    coord.record_capacity_check()

                with lock:
                    c = coord.cadence
                    if c < min_int or c > max_cad:
                        violations.append((c, op, wid))

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(worker, i) for i in range(20)]
            concurrent.futures.wait(futures)

        assert len(violations) == 0, f"Cadence invariant violated: {violations}"
        print(f"  -> 1,000 mixed operations completed. Final cadence: {coord.cadence:.1f}s. Zero boundary violations.")


def stress_test_4_trigger_success_cascade_unblock():
    """Stress 4: Verify trigger_success() cascades to multiple workers blocked at different stages.

    Ensures file is written, coordinator.is_stopped() is True, and all workers unblock immediately.
    """
    print("\n[M2 STRESS 4] Trigger Success Immediate Cascade Unblock...")
    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        coord = ClaimCoordinator(
            ["FD-1", "FD-2", "FD-3"],
            success_file,
            status_file,
            cadence_seconds=30.0,
            min_interval_seconds=14.0,
            max_cadence=45.0,
        )
        coord.broadcast_rate_limit("Init", cooldown_seconds=120.0)

        num_workers = 30
        results = [None] * num_workers
        barrier = threading.Barrier(num_workers + 1)
        threads = []

        def worker_loop(idx):
            barrier.wait()
            results[idx] = coord.acquire_next_turn(f"Worker-{idx}")

        for i in range(num_workers):
            t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
            threads.append(t)
            t.start()

        barrier.wait()
        time.sleep(0.05)

        t_start = time.perf_counter()
        coord.trigger_success(
            "Worker-99", "ocid1.instance.oc1..test12345", "ARM-Node-Captured", "FD-2"
        )

        for t in threads:
            t.join(timeout=1.0)
            assert not t.is_alive(), f"Thread {t.name} did not terminate after trigger_success!"

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        assert elapsed_ms < 500.0, f"Trigger success unblock latency {elapsed_ms:.2f}ms exceeded 500ms!"
        for idx, res in enumerate(results):
            assert res == (0, None), f"Worker {idx} got {res}, expected (0, None)"

        assert os.path.exists(success_file)
        with open(success_file, "r") as f:
            content = f.read()
            assert "ocid1.instance.oc1..test12345" in content
            assert "Worker-99" in content

        print(f"  -> 30 threads unblocked in {elapsed_ms:.2f}ms. Success file verified.")


def run_all():
    print("=" * 75)
    print("M2 EMPIRICAL CONCURRENCY & DYNAMIC STRESS HARNESS")
    print("Challenger: teamwork_preview_challenger_m2_1")
    print("=" * 75)

    tests = [
        stress_test_1_unblock_latency_and_no_zombies,
        stress_test_2_continuous_contention_deadlock_freedom,
        stress_test_3_cadence_invariant_bounds,
        stress_test_4_trigger_success_cascade_unblock,
    ]

    passed = 0
    failed = 0
    t0 = time.time()

    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n[X] TEST FAILED: {t.__name__}: {e}")
            import traceback
            traceback.print_exc()

    elapsed = time.time() - t0
    print("\n" + "=" * 75)
    print(f"M2 STRESS HARNESS VERDICT: {passed} PASSED, {failed} FAILED in {elapsed:.2f}s")
    print("=" * 75)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
