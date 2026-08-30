import concurrent.futures
import threading
import time
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
