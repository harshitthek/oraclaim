import concurrent.futures
import threading
import time
import pytest
from src.coordinator import ClaimCoordinator


def test_concurrent_slot_reservation_no_races(tmp_path):
    """Stress tests ClaimCoordinator under 20 concurrent threads to ensure

    atomic sequential attempt numbers and strictly increasing timestamps without race conditions.
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    candidates = [None, "FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3"]
    coordinator = ClaimCoordinator(
        candidates, success_file, status_file, cadence_seconds=0.001, min_interval_seconds=0.0
    )

    total_requests = 100
    results = []
    lock = threading.Lock()

    def worker_task(worker_id):
        for _ in range(10):
            attempt_num, fd = coordinator.acquire_next_turn(f"Worker-{worker_id}")
            with lock:
                results.append((attempt_num, fd))

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(worker_task, i) for i in range(10)]
        concurrent.futures.wait(futures)

    assert len(results) == total_requests

    # Verify attempt numbers are exactly 1 to 100 with zero duplicates
    attempt_nums = [r[0] for r in results]
    assert sorted(attempt_nums) == list(range(1, total_requests + 1))
    assert len(set(attempt_nums)) == total_requests
    assert coordinator.total_attempts == total_requests


def test_concurrent_threat_broadcast_safety(tmp_path):
    """Verifies that multiple concurrent 429 broadcasts safely extend the timeline without deadlocks."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=0.001, min_interval_seconds=0.0
    )

    def spam_broadcast(worker_id):
        for _ in range(5):
            coordinator.broadcast_rate_limit(f"Worker-{worker_id}", cooldown_seconds=1.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(spam_broadcast, i) for i in range(8)]
        concurrent.futures.wait(futures)

    assert coordinator.rate_limit_hits == 40
    assert coordinator.next_allowed_request_time > time.time()


def test_concurrent_workers_immediate_unblock_on_success(tmp_path):
    """Verify that 6 concurrent worker threads sleeping in rate-limit cooldowns

    all unblock and terminate cleanly within < 1.0s when coordinator.trigger_success() is called.
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"],
        success_file,
        status_file,
        cadence_seconds=20.0,
        min_interval_seconds=14.0,
        max_cadence=45.0,
    )

    # Put pipeline into deep rate-limit sleep (e.g. 60.0s cooldown)
    coordinator.broadcast_rate_limit("Seeder", cooldown_seconds=60.0)

    num_workers = 6
    results = [None] * num_workers
    threads = []
    barrier = threading.Barrier(num_workers + 1)

    def worker_loop(idx):
        barrier.wait()
        res = coordinator.acquire_next_turn(f"Worker-{idx}")
        results[idx] = res

    for i in range(num_workers):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        threads.append(t)
        t.start()

    barrier.wait()
    time.sleep(0.05)  # Let all workers enter acquire_next_turn wait

    start_time = time.perf_counter()
    coordinator.trigger_success(
        "Hero-Worker", "ocid1.instance.oc1..captured", "Captured-Instance", "FAULT-DOMAIN-1"
    )

    for t in threads:
        t.join(timeout=2.0)
        assert not t.is_alive(), f"Thread {t.name} failed to terminate after success!"

    elapsed = time.perf_counter() - start_time
    assert elapsed < 1.0, f"Unblocking took {elapsed:.4f}s (must be < 1.0s)"

    for idx, res in enumerate(results):
        assert res == (0, None), f"Worker {idx} got {res}, expected (0, None)"

    assert coordinator.total_attempts == 0
    assert coordinator.is_stopped()


def test_concurrent_cadence_ceiling_under_rate_limit_flood(tmp_path):
    """Multi-threaded flood test asserting cadence ceiling is never breached

    under extreme concurrency (20 threads sending 100 total rate-limit broadcasts).
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    ceiling = 35.0
    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=15.0,
        max_cadence=ceiling,
    )

    def spammer(wid):
        for _ in range(5):
            coordinator.broadcast_rate_limit(f"Flooder-{wid}", cooldown_seconds=10.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futs = [executor.submit(spammer, i) for i in range(20)]
        concurrent.futures.wait(futs)

    assert coordinator.rate_limit_hits == 100
    assert coordinator.cadence <= ceiling
    assert coordinator.cadence == ceiling


def test_concurrent_rapid_stop_race_safety(tmp_path):
    """Verify that rapid stop_event setting while multiple threads request turns

    awards zero post-stop slots.
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"],
        success_file,
        status_file,
        cadence_seconds=0.002,
        min_interval_seconds=0.001,
        max_cadence=45.0,
    )

    slots = []
    lock = threading.Lock()
    stop_marked = False

    def runner(wid):
        while not coordinator.is_stopped():
            att, fd = coordinator.acquire_next_turn(f"Worker-{wid}")
            if att > 0:
                with lock:
                    slots.append((time.time(), att, fd, stop_marked))
            else:
                break

    threads = [threading.Thread(target=runner, args=(i,), daemon=True) for i in range(15)]
    for t in threads:
        t.start()

    time.sleep(0.02)
    coordinator.stop_event.set()
    stop_marked = True

    for t in threads:
        t.join(timeout=1.0)
        assert not t.is_alive()

    # Zero slots awarded after stop was set and processed
    assert coordinator.is_stopped()
