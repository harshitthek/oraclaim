"""Adversarial Stress & Concurrency Verification Harness for OraClaim M1.

Challenger: teamwork_preview_challenger_m1_1
Target Files: src/coordinator.py, src/config.py, src/cli.py
Scope: R1 Concurrency & Sequencer Hardening, R2 Cadence Drift & Ceiling Enforce
"""

import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import time
from typing import List, Tuple
from unittest.mock import MagicMock, patch

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.config import ClaimerConfig
from src.coordinator import ClaimCoordinator
from src.cli import parse_args


def test_1_instant_unblocking_latency():
    """Test 1: Instant Unblocking Latency Under High Cooldown

    Worker threads sleeping with high cooldowns (30s, 60s, 120s) must unblock within < 0.5s
    upon stop_event.set(), returning (0, None) with zero zombie attempts.
    """
    print("\n[TEST 1] Testing Instant Unblocking Latency Under High Cooldown (30s, 60s, 120s)...")
    latencies = []

    for cooldown in [30.0, 60.0, 120.0]:
        with tempfile.TemporaryDirectory() as tmpdir:
            success_file = os.path.join(tmpdir, "success.txt")
            status_file = os.path.join(tmpdir, "status.json")

            coordinator = ClaimCoordinator(
                ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"],
                success_file,
                status_file,
                cadence_seconds=20.0,
                min_interval_seconds=14.0,
                max_cadence=45.0,
            )

            # Push pipeline into high cooldown
            coordinator.broadcast_rate_limit("Seeder", cooldown_seconds=cooldown)
            sleep_ahead = coordinator.next_allowed_request_time - time.time()
            assert sleep_ahead >= cooldown, f"Expected sleep_ahead >= {cooldown}, got {sleep_ahead}"

            num_workers = 20
            results: List[Tuple[int, str]] = [None] * num_workers
            barrier = threading.Barrier(num_workers + 1)
            threads: List[threading.Thread] = []

            def worker_task(idx):
                barrier.wait()
                res = coordinator.acquire_next_turn(f"Worker-{idx}")
                results[idx] = res

            for i in range(num_workers):
                t = threading.Thread(target=worker_task, args=(i,), daemon=True)
                threads.append(t)
                t.start()

            barrier.wait()
            # Allow workers to enter acquire_next_turn and block on stop_event.wait()
            time.sleep(0.1)

            t_start = time.perf_counter()
            coordinator.stop_event.set()

            for t in threads:
                t.join(timeout=2.0)
                assert not t.is_alive(), f"FAIL: Worker thread {t.name} hung after stop_event.set()!"

            t_elapsed = time.perf_counter() - t_start
            latencies.append(t_elapsed)

            for idx, res in enumerate(results):
                assert res == (0, None), f"FAIL: Worker {idx} received non-shutdown slot: {res}"

            assert coordinator.total_attempts == 0, f"FAIL: total_attempts != 0 (got {coordinator.total_attempts})"
            assert t_elapsed < 0.5, f"FAIL: Unblock latency {t_elapsed:.4f}s exceeded 0.5s limit"
            print(f"  -> PASSED [Cooldown {cooldown:5.1f}s]: {num_workers} threads unblocked in {t_elapsed*1000:6.2f} ms")

    max_lat = max(latencies)
    print(f"  -> PASSED: Max latency across all cooldown regimes: {max_lat*1000:.2f} ms (Ceiling: 500.0 ms)")


def test_2_cadence_ceiling_stress():
    """Test 2: Cadence Ceiling Under 100 Concurrent Broadcasts

    Simulates 100 concurrent workers blasting rate-limit broadcasts and verifies that
    coordinator.cadence NEVER exceeds max_cadence (45.0s), and status snapshots reflect the ceiling.
    """
    print("\n[TEST 2] Testing Cadence Ceiling Under 100 Concurrent Broadcasts (500 total events)...")
    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        for ceiling_val in [45.0, 30.0, 15.0, 60.0]:
            coordinator = ClaimCoordinator(
                ["FAULT-DOMAIN-1"],
                success_file,
                status_file,
                cadence_seconds=10.0,
                min_interval_seconds=5.0,
                max_cadence=ceiling_val,
            )

            observed = []
            lock = threading.Lock()

            def broadcast_task(wid):
                for _ in range(5):
                    coordinator.broadcast_rate_limit(f"W-{wid}", cooldown_seconds=38.0)
                    with lock:
                        observed.append(coordinator.cadence)

            with concurrent.futures.ThreadPoolExecutor(max_workers=100) as ex:
                futs = [ex.submit(broadcast_task, i) for i in range(100)]
                concurrent.futures.wait(futs)

            assert len(observed) == 500, f"Expected 500 observations, got {len(observed)}"
            max_obs = max(observed)
            assert max_obs <= ceiling_val, f"FAIL: Observed cadence {max_obs} exceeded ceiling {ceiling_val}"
            assert coordinator.cadence == ceiling_val, f"FAIL: Final cadence {coordinator.cadence} != {ceiling_val}"
            assert coordinator.rate_limit_hits == 500

            coordinator.write_status_snapshot()
            with open(status_file, "r") as f:
                snap = json.load(f)
                assert snap["cadence"] == ceiling_val
                assert snap["max_cadence"] == ceiling_val

            print(f"  -> PASSED [Ceiling {ceiling_val:4.1f}s]: 500 broadcasts safely clamped (max={max_obs:.1f}s, final={coordinator.cadence:.1f}s)")

        # Initial cadence clamp test
        coord_init_clamp = ClaimCoordinator(
            ["FAULT-DOMAIN-1"],
            success_file,
            status_file,
            cadence_seconds=92.0,  # Observed live drift
            max_cadence=45.0,
        )
        assert coord_init_clamp.cadence == 45.0, f"FAIL: Initial cadence not clamped: {coord_init_clamp.cadence}"
        print("  -> PASSED [Initial Clamp]: Initial cadence 92.0s clamped to 45.0s on instantiation")


def test_3_counter_initialization_and_aimd_lifecycle():
    """Test 3: Counter Initialization & AIMD Lifecycle

    Verifies consecutive_clean starts at 0 without AttributeError, increments on capacity checks,
    triggers AIMD cadence optimization at 6 clean checks, and resets on rate-limit hits.
    """
    print("\n[TEST 3] Testing Counter Initialization & AIMD Lifecycle...")
    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        coord = ClaimCoordinator(
            ["FAULT-DOMAIN-1"],
            success_file,
            status_file,
            cadence_seconds=20.0,
            min_interval_seconds=14.0,
            max_cadence=45.0,
        )

        assert hasattr(coord, "consecutive_clean"), "FAIL: consecutive_clean attribute missing"
        assert coord.consecutive_clean == 0, f"FAIL: consecutive_clean starts at {coord.consecutive_clean} != 0"

        for step in range(1, 6):
            coord.record_capacity_check()
            assert coord.consecutive_clean == step
            assert coord.cadence == 20.0

        coord.record_capacity_check()  # 6th clean check
        assert coord.consecutive_clean == 0, f"FAIL: counter did not reset at 6 (got {coord.consecutive_clean})"
        assert coord.cadence == 19.0, f"FAIL: cadence did not reduce to 19.0 (got {coord.cadence})"

        coord.record_capacity_check()
        coord.record_capacity_check()
        assert coord.consecutive_clean == 2
        coord.broadcast_rate_limit("Worker-A", cooldown_seconds=5.0)
        assert coord.consecutive_clean == 0, "FAIL: rate limit did not reset consecutive_clean"
        assert coord.cadence == 21.0

        print("  -> PASSED: Lifecycle verified: init=0, increment 1..5, AIMD recovery at 6, reset on 429")


def test_4_cli_and_config_contracts():
    """Test 4: CLI & Config Contracts for Max Cadence

    Verifies --max-cadence CLI argument, MAX_CADENCE_SECONDS env variable, and default fallback.
    """
    print("\n[TEST 4] Testing CLI & Config Contracts for Max Cadence...")
    cfg = ClaimerConfig(config_file="c.ini", profile="DEFAULT", private_key_path="k.key", public_ssh_key="pub")
    assert cfg.max_cadence == 45.0

    with patch.dict(os.environ, {"MAX_CADENCE_SECONDS": "52.0"}):
        cfg_env = ClaimerConfig.load_from_env_or_file()
        assert cfg_env.max_cadence == 52.0

    with patch("sys.argv", ["oraclaim"]):
        args = parse_args()
        assert args.max_cadence == 45.0

    with patch("sys.argv", ["oraclaim", "--max-cadence", "38.5"]):
        args = parse_args()
        assert args.max_cadence == 38.5

    print("  -> PASSED: CLI flag --max-cadence and env var MAX_CADENCE_SECONDS validated")


def test_5_race_condition_rapid_stop():
    """Test 5: Race Condition Resistance During Rapid Shutdown

    Verifies that when stop_event is set, no thread in acquire_next_turn can allocate a slot.
    """
    print("\n[TEST 5] Testing Race Condition Resistance During Rapid Shutdown...")
    with tempfile.TemporaryDirectory() as tmpdir:
        success_file = os.path.join(tmpdir, "success.txt")
        status_file = os.path.join(tmpdir, "status.json")

        coord = ClaimCoordinator(
            ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"],
            success_file,
            status_file,
            cadence_seconds=0.005,
            min_interval_seconds=0.002,
            max_cadence=45.0,
        )

        slots = []
        lock = threading.Lock()
        stop_time = 0.0

        def runner(wid):
            while not coord.is_stopped():
                att, fd = coord.acquire_next_turn(f"W-{wid}")
                if att > 0:
                    with lock:
                        slots.append((time.time(), att, fd))
                else:
                    break

        threads = [threading.Thread(target=runner, args=(i,), daemon=True) for i in range(30)]
        for t in threads:
            t.start()

        time.sleep(0.05)
        stop_time = time.time()
        coord.stop_event.set()

        for t in threads:
            t.join(timeout=1.0)
            assert not t.is_alive(), f"FAIL: Thread {t.name} hung"

        post_stop = [s for s in slots if s[0] > stop_time + 0.05]
        assert len(post_stop) == 0, f"FAIL: Slots awarded after stop: {post_stop}"
        print(f"  -> PASSED: {len(slots)} safe slots awarded prior to stop, exactly 0 slots post-stop")


def run_all():
    print("=" * 70)
    print("EMPIRICAL ADVERSARIAL STRESS HARNESS — M1 CONCURRENCY & CADENCE")
    print("Agent: teamwork_preview_challenger_m1_1")
    print("=" * 70)

    tests = [
        test_1_instant_unblocking_latency,
        test_2_cadence_ceiling_stress,
        test_3_counter_initialization_and_aimd_lifecycle,
        test_4_cli_and_config_contracts,
        test_5_race_condition_rapid_stop,
    ]

    passed = 0
    failed = 0
    t0 = time.time()

    for t in tests:
        try:
            t()
            passed += 1
        except Exception as ex:
            failed += 1
            print(f"\n[X] TEST FAILED: {t.__name__}: {ex}")
            import traceback
            traceback.print_exc()

    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print(f"HARNESS SUMMARY: {passed} PASSED, {failed} FAILED in {elapsed:.2f}s")
    print("=" * 70)
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
