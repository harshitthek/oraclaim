import os
import time
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
    with open(success_file, "r") as f:
        content = f.read()
        assert "ocid1.instance.oc1..123" in content
