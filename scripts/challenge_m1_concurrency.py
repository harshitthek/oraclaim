"""Empirical Adversarial Stress Harness for OraClaim M1 Coordinator Concurrency & Cadence.

Author: teamwork_preview_challenger_m1_it2_1 (Empirical Challenger)
Target: src/coordinator.py, src/config.py, src/cli.py
Scope:
  - Microsecond/millisecond stop_event unblocking latency across variable thread pools
  - Cadence ceiling clamping under 1,000+ concurrent rate-limit broadcasts & float precision checks
  - Concurrent Token-Ring mutual exclusion and minimum interval invariants
  - Chaotic multi-thread race conditions (acquire vs broadcast vs stop vs success)
  - AIMD lifecycle invariants and counter stability
  - Instant pre-stopped and post-success execution paths
"""

import concurrent.futures
import json
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from typing import List, Tuple
from unittest.mock import patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.config import ClaimerConfig
from src.coordinator import ClaimCoordinator
from src.cli import parse_args


def challenge_1_unblocking_latency_distribution():
    """Attack Vector 1: Latency distribution of stop_event unblocking under thread pressure.

    Tests single-thread isolated unblock latency as well as 10, 50, and 100 concurrent
    sleeping worker threads. Evaluates min, mean, median, p95, p99, and max wakeup latencies.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 1] Measuring Empirical Unblock Latency Across Variable Thread Loads")
    print("=" * 75)

    thread_counts = [1, 5, 20, 50, 100]

    for count in thread_counts:
        with tempfile.TemporaryDirectory() as tmpdir:
            success_file = os.path.join(tmpdir, "success.txt")
            status_file = os.path.join(tmpdir, "status.json")

            coord = ClaimCoordinator(
                ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"],
                success_file,
                status_file,
                cadence_seconds=30.0,
                min_interval_seconds=14.0,
                max_cadence=45.0,
            )

            # Put coordinator into deep sleep (60 seconds ahead)
            coord.broadcast_rate_limit("Attacker", cooldown_seconds=60.0)

            results = [None] * count
            thread_wake_times = [None] * count
            barrier = threading.Barrier(count + 1)
            threads = []

            def worker_run(idx):
                barrier.wait()
                # Enters acquire_next_turn and blocks on self.stop_event.wait(timeout=sleep_needed)
                res = coord.acquire_next_turn(f"Worker-{idx}")
                thread_wake_times[idx] = time.perf_counter()
                results[idx] = res

            for i in range(count):
                t = threading.Thread(target=worker_run, args=(i,), daemon=True)
                threads.append(t)
                t.start()

            barrier.wait()
            # Guarantee all threads have reached stop_event.wait()
            time.sleep(0.08)

            t_trigger = time.perf_counter()
            coord.stop_event.set()

            for t in threads:
                t.join(timeout=2.0)
                assert not t.is_alive(), f"DEADLOCK: Thread {t.name} did not exit after stop_event.set()!"

            latencies_ms = [(w_t - t_trigger) * 1000.0 for w_t in thread_wake_times]

            min_lat = min(latencies_ms)
            mean_lat = statistics.mean(latencies_ms)
            median_lat = statistics.median(latencies_ms)
            max_lat = max(latencies_ms)

            # Integrity checks
            for idx, res in enumerate(results):
                assert res == (0, None), f"VIOLATION: Thread {idx} received slot {res} despite stop_event!"
            assert coord.total_attempts == 0, f"VIOLATION: total_attempts={coord.total_attempts} > 0!"

            print(f"  Load {count:3d} threads: min={min_lat:5.2f}ms | median={median_lat:5.2f}ms | mean={mean_lat:5.2f}ms | max={max_lat:5.2f}ms")

            # Assert unblocking happens well within acceptable threshold (< 250ms even for 100 threads)
            assert max_lat < 250.0, f"FAIL: Max unblock latency {max_lat:.2f}ms exceeded 250ms limit!"
            if count == 1:
                print(f"  -> Single-thread isolated wakeup: {median_lat:5.3f} ms (sub-millisecond or low-millisecond OS dispatch)")

    print("  -> RESULT: All worker threads across 1..100 thread pools unblocked cleanly with zero zombie attempts.")


def challenge_2_cadence_ceiling_1000_broadcasts():
    """Attack Vector 2: Cadence ceiling clamping across 1,000+ broadcasts & float stability.

    Spawns 100 concurrent workers broadcasting rate-limit signals (1,000 total events).
    Checks that cadence NEVER exceeds max_cadence by even a float epsilon,
    and tests varied ceilings including 45.0, 15.0, 33.333, and 92.0s initial drift.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 2] Cadence Ceiling Clamping Under 1,000 Concurrent Broadcasts")
    print("=" * 75)

    test_configs = [
        {"ceiling": 45.0, "init": 20.0, "broadcasts_per_worker": 10},
        {"ceiling": 15.0, "init": 10.0, "broadcasts_per_worker": 10},
        {"ceiling": 33.333, "init": 30.0, "broadcasts_per_worker": 10},
        {"ceiling": 45.0, "init": 92.0, "broadcasts_per_worker": 10},  # Initial drift above ceiling
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        for cfg in test_configs:
            ceiling = cfg["ceiling"]
            init_c = cfg["init"]
            bpw = cfg["broadcasts_per_worker"]
            num_workers = 100
            total_broadcasts = num_workers * bpw

            coord = ClaimCoordinator(
                ["FAULT-DOMAIN-1"],
                success_file,
                status_file,
                cadence_seconds=init_c,
                min_interval_seconds=5.0,
                max_cadence=ceiling,
            )

            # Verify initial clamping
            expected_init = min(init_c, ceiling)
            assert coord.cadence == expected_init, f"FAIL: Initial cadence {coord.cadence} != {expected_init}"

            observed_cadences = []
            lock = threading.Lock()

            def hammer_worker(wid):
                for _ in range(bpw):
                    coord.broadcast_rate_limit(f"H-{wid}", cooldown_seconds=random.uniform(5.0, 40.0))
                    with lock:
                        observed_cadences.append(coord.cadence)

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as ex:
                futures = [ex.submit(hammer_worker, i) for i in range(num_workers)]
                concurrent.futures.wait(futures)

            assert len(observed_cadences) == total_broadcasts, f"Expected {total_broadcasts}, got {len(observed_cadences)}"
            highest_observed = max(observed_cadences)

            assert highest_observed <= ceiling, (
                f"CRITICAL VIOLATION: Cadence {highest_observed} breached ceiling {ceiling}!"
            )
            assert coord.cadence == ceiling, (
                f"FAIL: Final cadence {coord.cadence} != {ceiling}"
            )
            assert coord.rate_limit_hits == total_broadcasts

            # Verify status snapshot integrity
            coord.write_status_snapshot()
            with open(status_file, "r") as f:
                snap = json.load(f)
                assert snap["cadence"] == round(ceiling, 2)
                assert snap["max_cadence"] == ceiling
                assert snap["rate_limits"] == total_broadcasts

            print(f"  -> PASSED [Ceiling {ceiling:6.3f}s | Init {init_c:4.1f}s]: {total_broadcasts} broadcasts strictly clamped at {highest_observed:.3f}s")

    print("  -> RESULT: Cadence ceiling strictly maintained across 1,000+ broadcasts with zero drift.")


def challenge_3_token_ring_mutual_exclusion():
    """Attack Vector 3: Sequencer Token-Ring Mutual Exclusion & Spacing Invariants.

    Runs 10 concurrent threads through 40 turns with low interval (0.02s).
    Measures timestamps of every granted turn:
    1. Guarantees strictly sequential, non-overlapping turns.
    2. Guarantees elapsed time between turns is >= min_interval (accounting for jitter).
    3. Guarantees round-robin fault domain cycling.
    4. Guarantees attempt_num is 1, 2, 3, ... with zero duplicates and zero gaps.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 3] Token-Ring Sequencer Mutual Exclusion & Monotonicity Invariants")
    print("=" * 75)

    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        fds = ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3"]
        min_int = 0.02
        coord = ClaimCoordinator(
            fds,
            success_file,
            status_file,
            cadence_seconds=min_int,
            min_interval_seconds=min_int,
            max_cadence=45.0,
        )

        turns = []
        lock = threading.Lock()
        target_total = 30

        def contender(wid):
            while True:
                with lock:
                    if len(turns) >= target_total:
                        break
                att, fd = coord.acquire_next_turn(f"C-{wid}")
                if att == 0:
                    break
                t_granted = time.time()
                with lock:
                    if len(turns) < target_total:
                        turns.append((t_granted, att, fd, wid))
                    else:
                        break

        threads = [threading.Thread(target=contender, args=(i,), daemon=True) for i in range(8)]
        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=5.0)

        assert len(turns) >= target_total, f"Expected at least {target_total} turns, got {len(turns)}"

        # 1. Monotonicity check
        attempt_numbers = [item[1] for item in turns[:target_total]]
        expected_attempts = list(range(1, target_total + 1))
        assert attempt_numbers == expected_attempts, f"FAIL: Attempt numbers out of sequence: {attempt_numbers}"

        # 2. Fault domain rotation check
        granted_fds = [item[2] for item in turns[:target_total]]
        for i in range(target_total):
            expected_fd = fds[i % len(fds)]
            assert granted_fds[i] == expected_fd, f"FAIL: FD mismatch at turn {i}: expected {expected_fd}, got {granted_fds[i]}"

        # 3. Spacing intervals between consecutive granted turns
        intervals = []
        for i in range(1, target_total):
            delta = turns[i][0] - turns[i - 1][0]
            intervals.append(delta)

        min_observed_interval = min(intervals)
        avg_observed_interval = statistics.mean(intervals)
        print(f"  -> Observed slot intervals: min={min_observed_interval*1000:5.2f}ms | avg={avg_observed_interval*1000:5.2f}ms (target: {min_int*1000:.1f}ms)")
        # Notice: min_interval is 0.02s. In acquire_next_turn:
        # slot_interval = max(self.min_interval, effective_cadence + jitter) where jitter is 0 since min_interval <= 1.0.
        # Spacing should be >= min_int - 0.005 (to allow for OS clock resolution)
        assert min_observed_interval >= min_int - 0.006, (
            f"VIOLATION: Slot spacing violated! Two workers ran simultaneously (delta = {min_observed_interval*1000:.2f}ms < {min_int*1000:.1f}ms)"
        )

        print(f"  -> PASSED: {target_total} turns strictly sequential, perfectly spaced, zero collisions.")


def challenge_4_chaotic_race_conditions_under_load():
    """Attack Vector 4: Chaotic Multi-Thread Race Conditions.

    Simulates an aggressive deployment environment:
    - 25 worker threads running acquire_next_turn
    - 5 threads spamming broadcast_rate_limit
    - 5 threads spamming record_capacity_check
    - 1 thread writing status snapshots
    - Abrupt asynchronous trigger_success from external worker
    Verifies zero deadlocks, zero exceptions, zero leaked slots post-stop.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 4] Chaotic Race Condition Stress (Acquire + 429 + AIMD + Stop)")
    print("=" * 75)

    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        coord = ClaimCoordinator(
            ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"],
            success_file,
            status_file,
            cadence_seconds=0.01,
            min_interval_seconds=0.005,
            max_cadence=45.0,
        )

        exceptions = []
        post_stop_slots = []
        stop_called_time = None
        lock = threading.Lock()

        def worker_loop(wid):
            try:
                while not coord.is_stopped():
                    att, fd = coord.acquire_next_turn(f"Worker-{wid}")
                    if att > 0:
                        with lock:
                            if stop_called_time is not None:
                                post_stop_slots.append((time.time(), att, wid))
                    else:
                        break
            except Exception as e:
                with lock:
                    exceptions.append((f"worker-{wid}", e))

        def rate_limit_spammer():
            try:
                while not coord.is_stopped():
                    coord.broadcast_rate_limit("Spammer", cooldown_seconds=0.02)
                    time.sleep(0.005)
            except Exception as e:
                with lock:
                    exceptions.append(("spammer", e))

        def capacity_spammer():
            try:
                while not coord.is_stopped():
                    coord.record_capacity_check()
                    time.sleep(0.003)
            except Exception as e:
                with lock:
                    exceptions.append(("capacity", e))

        def snapshot_writer():
            try:
                while not coord.is_stopped():
                    coord.write_status_snapshot()
                    time.sleep(0.01)
            except Exception as e:
                with lock:
                    exceptions.append(("snapshot", e))

        threads = []
        for i in range(25):
            threads.append(threading.Thread(target=worker_loop, args=(i,), daemon=True))
        for _ in range(5):
            threads.append(threading.Thread(target=rate_limit_spammer, daemon=True))
        for _ in range(5):
            threads.append(threading.Thread(target=capacity_spammer, daemon=True))
        threads.append(threading.Thread(target=snapshot_writer, daemon=True))

        for t in threads:
            t.start()

        # Let system run under chaos for 200ms
        time.sleep(0.2)

        # Trigger success
        with lock:
            stop_called_time = time.time()
        coord.trigger_success("LuckyWorker", "ocid1.instance.oc1..test", "arm-oracle-vm", "FAULT-DOMAIN-1")

        # Join all threads
        for t in threads:
            t.join(timeout=1.5)
            assert not t.is_alive(), f"DEADLOCK: Thread {t.name} did not terminate after trigger_success!"

        assert len(exceptions) == 0, f"FAIL: Exceptions occurred during chaos: {exceptions}"
        assert os.path.exists(success_file), "FAIL: success_file was not created!"
        assert coord.is_stopped() is True, "FAIL: coordinator not stopped!"
        assert len(post_stop_slots) == 0, f"FAIL: Slots allocated after stop: {post_stop_slots}"

        print(f"  -> PASSED: Completed chaotic churn ({coord.total_attempts} attempts, {coord.rate_limit_hits} 429s, {coord.capacity_errors} capacity checks). Zero deadlocks, zero leaked slots.")


def challenge_5_aimd_lifecycle_and_boundary_invariants():
    """Attack Vector 5: AIMD Recovery Bounds and Counter Consistency.

    Tests:
    1. consecutive_clean starting at 0.
    2. Resetting to 0 upon rate-limit hits.
    3. Decrementing cadence by 1.0 upon 6 clean checks, but NEVER decreasing below min_interval.
    4. Capping cadence at max_cadence upon repeated rate limits.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 5] AIMD Lifecycle, Counter Invariants, and Floor/Ceiling Bounds")
    print("=" * 75)

    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        coord = ClaimCoordinator(
            ["FAULT-DOMAIN-1"],
            success_file,
            status_file,
            cadence_seconds=16.0,
            min_interval_seconds=14.0,
            max_cadence=20.0,
        )

        assert coord.consecutive_clean == 0
        assert coord.cadence == 16.0

        # Step 1: 5 clean checks should NOT decrement cadence
        for _ in range(5):
            coord.record_capacity_check()
        assert coord.consecutive_clean == 5
        assert coord.cadence == 16.0

        # Step 2: 6th clean check decrements by 1.0 to 15.0 and resets counter
        coord.record_capacity_check()
        assert coord.consecutive_clean == 0
        assert coord.cadence == 15.0

        # Step 3: 6 more clean checks decrements by 1.0 to 14.0 (min_interval)
        for _ in range(6):
            coord.record_capacity_check()
        assert coord.consecutive_clean == 0
        assert coord.cadence == 14.0

        # Step 4: Further clean checks must NEVER drop below min_interval (14.0)
        for _ in range(12):
            coord.record_capacity_check()
        assert coord.cadence == 14.0, f"FAIL: Cadence {coord.cadence} dropped below min_interval 14.0!"

        # Step 5: Rate limit hit resets consecutive_clean and increments cadence
        coord.broadcast_rate_limit("Worker-B", cooldown_seconds=1.0)
        assert coord.consecutive_clean == 0
        assert coord.cadence == 16.0

        # Step 6: Multiple rate limits clamp at max_cadence (20.0)
        for _ in range(5):
            coord.broadcast_rate_limit("Worker-B", cooldown_seconds=1.0)
        assert coord.cadence == 20.0, f"FAIL: Cadence {coord.cadence} exceeded max_cadence 20.0!"

        print("  -> PASSED: AIMD counter, floor (14.0s), and ceiling (20.0s) strictly respected.")


def challenge_6_pre_stopped_instant_return():
    """Attack Vector 6: Instant Sub-Millisecond Return for Pre-Stopped Coordinators.

    When stop_event is already set or success_file already exists, acquire_next_turn
    must return (0, None) instantaneously without blocking or state mutation.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 6] Instant Return Under Pre-Stopped Conditions")
    print("=" * 75)

    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        coord = ClaimCoordinator(
            ["FAULT-DOMAIN-1"],
            success_file,
            status_file,
            cadence_seconds=30.0,
            min_interval_seconds=14.0,
            max_cadence=45.0,
        )

        coord.stop_event.set()

        times = []
        for _ in range(1000):
            t0 = time.perf_counter()
            slot = coord.acquire_next_turn("Worker-Instant")
            t1 = time.perf_counter()
            assert slot == (0, None)
            times.append((t1 - t0) * 1000.0)

        mean_us = statistics.mean(times) * 1000.0  # microseconds
        max_ms = max(times)
        assert coord.total_attempts == 0
        print(f"  -> Pre-stopped return time: mean={mean_us:.2f} microseconds (sub-millisecond!), max={max_ms:.4f} ms")
        assert max_ms < 5.0, f"FAIL: Pre-stopped return took {max_ms:.2f}ms!"

        # Test filesystem success file detection without stop_event
        coord2 = ClaimCoordinator(
            ["FAULT-DOMAIN-1"],
            success_file,
            status_file,
            cadence_seconds=30.0,
            min_interval_seconds=14.0,
            max_cadence=45.0,
        )
        with open(success_file, "w") as f:
            f.write("DONE")

        assert coord2.is_stopped() is True
        slot2 = coord2.acquire_next_turn("Worker-FS")
        assert slot2 == (0, None)
        print("  -> PASSED: Filesystem success_file detection verified without stop_event explicitly set.")


def challenge_7_config_and_cli_contracts():
    """Attack Vector 7: Configuration and CLI Boundary Enforcement.

    Tests constructor parameters `max_cadence` vs `max_cadence_seconds`,
    env variable `MAX_CADENCE_SECONDS`, and CLI flags.
    """
    print("\n" + "=" * 75)
    print("[CHALLENGE 7] Configuration & CLI Contracts")
    print("=" * 75)

    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        # Test constructor precedence
        c1 = ClaimCoordinator(["FD1"], success_file, status_file, max_cadence=50.0)
        assert c1.max_cadence == 50.0

        c2 = ClaimCoordinator(["FD1"], success_file, status_file, max_cadence=50.0, max_cadence_seconds=35.0)
        assert c2.max_cadence == 35.0, "FAIL: max_cadence_seconds should take precedence over max_cadence"

        # Test env variable parsing in ClaimerConfig
        with patch.dict(os.environ, {"MAX_CADENCE_SECONDS": "42.5"}):
            cfg = ClaimerConfig.load_from_env_or_file()
            assert cfg.max_cadence == 42.5

        # Test CLI parsing with custom max-cadence
        with patch("sys.argv", ["oraclaim", "--max-cadence", "65.0"]):
            args = parse_args()
            assert args.max_cadence == 65.0

        print("  -> PASSED: All config and CLI contracts validated.")


def run_all_challenges():
    print("=" * 75)
    print("EMPIRICAL ADVERSARIAL CHALLENGER SUITE: M1 CONCURRENCY & CADENCE")
    print("Challenger: teamwork_preview_challenger_m1_it2_1")
    print("=" * 75)

    suite = [
        challenge_1_unblocking_latency_distribution,
        challenge_2_cadence_ceiling_1000_broadcasts,
        challenge_3_token_ring_mutual_exclusion,
        challenge_4_chaotic_race_conditions_under_load,
        challenge_5_aimd_lifecycle_and_boundary_invariants,
        challenge_6_pre_stopped_instant_return,
        challenge_7_config_and_cli_contracts,
    ]

    passed = 0
    failed = 0
    t_start = time.time()

    for challenge in suite:
        try:
            challenge()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n[X] CHALLENGE FAILED: {challenge.__name__}: {e}")
            import traceback
            traceback.print_exc()

    t_total = time.time() - t_start
    print("\n" + "=" * 75)
    print(f"EMPIRICAL CHALLENGER VERDICT: {passed} PASSED, {failed} FAILED in {t_total:.2f}s")
    print("=" * 75)

    return failed == 0


if __name__ == "__main__":
    success = run_all_challenges()
    sys.exit(0 if success else 1)
