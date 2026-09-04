import json
import os
import threading
import time
from unittest.mock import patch
import pytest
from src.coordinator import ClaimCoordinator


def test_coordinator_rotation(tmp_path):
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    candidates = [None, "FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3"]
    coordinator = ClaimCoordinator(
        candidates, success_file, status_file, cadence_seconds=0.001, min_interval_seconds=0.0
    )

    a1, fd1 = coordinator.acquire_next_turn("Worker-A")
    assert a1 == 1
    assert fd1 is None

    a2, fd2 = coordinator.acquire_next_turn("Worker-B")
    assert a2 == 2
    assert fd2 == "FAULT-DOMAIN-1"

    a3, fd3 = coordinator.acquire_next_turn("Worker-A")
    assert a3 == 3
    assert fd3 == "FAULT-DOMAIN-2"


def test_rate_limit_broadcast_pushes_pipeline(tmp_path):
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=0.001, min_interval_seconds=0.0
    )
    coordinator.broadcast_rate_limit("Worker-A", cooldown_seconds=5.0)
    assert coordinator.rate_limit_hits == 1
    assert coordinator.next_allowed_request_time > time.time() + 4.0


def test_trigger_success_stops_coordinator(tmp_path):
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(["FAULT-DOMAIN-1"], success_file, status_file)
    assert not coordinator.is_stopped()

    coordinator.trigger_success(
        "Worker-Alpha", "ocid1.instance.oc1..123", "WorldTree-Node", "FAULT-DOMAIN-1"
    )

    assert coordinator.is_stopped()
    assert os.path.exists(success_file)
    with open(success_file, "r", encoding="utf-8") as f:
        content = f.read()
        assert "ocid1.instance.oc1..123" in content
        assert "Worker-Alpha" in content


def test_coordinator_immediate_unblock_on_stop_event(tmp_path):
    """Verify that a worker thread waiting in acquire_next_turn with a 30s rate-limit cooldown

    unblocks in < 0.5s when coordinator.stop_event.set() is called, returning (0, None).
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=20.0,
        min_interval_seconds=14.0,
        max_cadence=45.0,
    )

    # Impose a 30s rate limit cooldown
    coordinator.broadcast_rate_limit("Seeder", cooldown_seconds=30.0)
    assert coordinator.next_allowed_request_time - time.time() >= 30.0

    result = [None]
    barrier = threading.Barrier(2)

    def worker_thread():
        barrier.wait()
        result[0] = coordinator.acquire_next_turn("Worker-Blocked")

    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()

    barrier.wait()
    time.sleep(0.05)  # Ensure worker enters sleep wait

    t_start = time.perf_counter()
    coordinator.stop_event.set()
    t.join(timeout=2.0)
    t_elapsed = time.perf_counter() - t_start

    assert not t.is_alive(), "Worker thread failed to terminate after stop_event.set()"
    assert result[0] == (0, None), f"Expected (0, None), got {result[0]}"
    assert t_elapsed < 0.5, f"Unblock latency took {t_elapsed:.4f}s (exceeded 0.5s requirement)"
    assert coordinator.total_attempts == 0


def test_cadence_ceiling_enforcement(tmp_path):
    """Verify that 50 repeated broadcast_rate_limit() calls clamp coordinator.cadence

    at max_cadence (default 45.0s, or custom ceiling like 35.0s).
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    # Default ceiling 45.0s
    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=20.0,
        max_cadence=45.0,
    )

    for i in range(50):
        coordinator.broadcast_rate_limit(f"Worker-{i}", cooldown_seconds=1.0)
        assert coordinator.cadence <= 45.0

    assert coordinator.cadence == 45.0
    assert coordinator.rate_limit_hits == 50

    # Custom ceiling 35.0s
    custom_coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=20.0,
        max_cadence=35.0,
    )
    for i in range(50):
        custom_coordinator.broadcast_rate_limit(f"Worker-{i}", cooldown_seconds=1.0)
        assert custom_coordinator.cadence <= 35.0

    assert custom_coordinator.cadence == 35.0

    # Test initial cadence clamp if initial cadence exceeds max_cadence
    drifted_coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=92.0,
        max_cadence=45.0,
    )
    assert drifted_coordinator.cadence == 45.0


def test_coordinator_counter_initialization(tmp_path):
    """Verify coordinator.consecutive_clean == 0 on init, increments on clean check,

    and resets on rate limit.
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=20.0,
        min_interval_seconds=14.0,
        max_cadence=45.0,
    )

    assert hasattr(coordinator, "consecutive_clean")
    assert coordinator.consecutive_clean == 0

    coordinator.record_capacity_check()
    assert coordinator.consecutive_clean == 1
    assert coordinator.capacity_errors == 1

    coordinator.record_capacity_check()
    assert coordinator.consecutive_clean == 2
    assert coordinator.capacity_errors == 2

    coordinator.broadcast_rate_limit("Worker-Reset", cooldown_seconds=2.0)
    assert coordinator.consecutive_clean == 0
    assert coordinator.rate_limit_hits == 1


def test_coordinator_aimd_recovery_acceleration(tmp_path):
    """Verify cadence decays by 1.0s down to min_safe_interval when consecutive_clean >= 6."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=16.0,
        min_interval_seconds=14.0,
        max_cadence=45.0,
    )

    # 5 consecutive clean checks: cadence unchanged
    for step in range(1, 6):
        coordinator.record_capacity_check()
        assert coordinator.consecutive_clean == step
        assert coordinator.cadence == 16.0

    # 6th clean check: cadence reduced by 1.0s, consecutive_clean resets to 0
    coordinator.record_capacity_check()
    assert coordinator.consecutive_clean == 0
    assert coordinator.cadence == 15.0

    # 6 more clean checks: cadence reduced to min_safe_interval (14.0s)
    for _ in range(6):
        coordinator.record_capacity_check()
    assert coordinator.consecutive_clean == 0
    assert coordinator.cadence == 14.0

    # Further clean checks should NOT decay below min_interval (14.0s)
    for _ in range(6):
        coordinator.record_capacity_check()
    assert coordinator.cadence == 14.0


def test_coordinator_is_stopped_on_success_file(tmp_path):
    """Verify shutdown triggers when success file appears on disk."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=0.001
    )
    assert not coordinator.is_stopped()

    # Write dummy success file
    with open(success_file, "w", encoding="utf-8") as f:
        f.write("CLAIMED")

    assert coordinator.is_stopped()
    att, fd = coordinator.acquire_next_turn("Worker-Stopped")
    assert att == 0
    assert fd is None


def test_coordinator_status_snapshot_and_surge_turn(tmp_path):
    """Verify write_status_snapshot format and surge cadence calculation in acquire_next_turn."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        [None, "FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=0.001,
        min_interval_seconds=0.0,
        max_cadence=45.0,
    )

    att, fd = coordinator.acquire_next_turn("Worker-Surge", is_surge=True)
    assert att == 1
    assert fd is None
    assert "Worker-Surge" in coordinator.worker_heartbeats

    coordinator.write_status_snapshot()
    assert os.path.exists(status_file)
    with open(status_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert data["total_attempts"] == 1
        assert data["max_cadence"] == 45.0
        assert "Worker-Surge" in data["workers"]
        assert "start_time" in data
        assert "last_update" in data


def test_coordinator_error_swallowing_coverage(tmp_path):
    """Test resilient error handling in trigger_success and write_status_snapshot."""
    # Pass directory path instead of file path to trigger IOError in write
    bad_success = str(tmp_path)
    bad_status = str(tmp_path)

    coordinator = ClaimCoordinator(["FD-1"], bad_success, bad_status)
    # Should not raise exception even if write fails
    coordinator.trigger_success("Worker-A", "id-1", "name-1", None)
    coordinator.write_status_snapshot()
    assert coordinator.is_stopped()


def test_coordinator_sleep_timeout_continue(tmp_path):
    """Verify continue branch when stop_event.wait times out during acquire_next_turn."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    coordinator = ClaimCoordinator(
        ["FAULT-DOMAIN-1"],
        success_file,
        status_file,
        cadence_seconds=0.06,
        min_interval_seconds=0.06,
        max_cadence=45.0,
    )

    # First turn is claimed immediately
    att1, _ = coordinator.acquire_next_turn("Worker-1")
    assert att1 == 1

    # Second turn requires >0.05s wait, wait() times out and loops around to claim turn
    att2, _ = coordinator.acquire_next_turn("Worker-1")
    assert att2 == 2

