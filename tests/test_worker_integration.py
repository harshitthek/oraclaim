import threading
import time
from unittest.mock import MagicMock
import oci
import pytest

from src.config import ClaimerConfig
from src.coordinator import ClaimCoordinator
from src.oci_client import OCIClientWrapper
from src.worker import run_claimer_worker


def test_worker_success_flow(tmp_path):
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    cfg = ClaimerConfig(
        config_file="test.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
        shape="VM.Standard.A1.Flex",
        ocpus=1.0,
        memory_in_gbs=6.0,
    )

    coordinator = ClaimCoordinator([None], success_file, status_file, cadence_seconds=0.001, min_interval_seconds=0.0)

    # Mock OCI Client
    mock_wrapper = MagicMock(spec=OCIClientWrapper)
    mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
    mock_wrapper.compute_client = MagicMock()

    mock_instance = MagicMock()
    mock_instance.id = "ocid1.instance.oc1..success123"
    mock_instance.display_name = "OCI-Auto-Claimed-Instance"
    mock_wrapper.compute_client.launch_instance.return_value.data = mock_instance

    # Run worker in thread
    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-Alpha", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=2.0)

    assert coordinator.is_stopped()
    assert coordinator.total_attempts == 1
    assert mock_wrapper.compute_client.launch_instance.called


def test_worker_capacity_error_resilience(tmp_path):
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    cfg = ClaimerConfig(
        config_file="test.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
    )

    coordinator = ClaimCoordinator(["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=0.001, min_interval_seconds=0.0)

    mock_wrapper = MagicMock(spec=OCIClientWrapper)
    mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
    mock_wrapper.compute_client = MagicMock()

    # Raise Out of host capacity on first attempt, then succeed on second attempt
    mock_instance = MagicMock()
    mock_instance.id = "ocid1.instance.oc1..second_attempt"
    mock_instance.display_name = "OCI-Node"

    cap_error = oci.exceptions.ServiceError(
        status=500,
        code="InternalError",
        headers={},
        message="Out of host capacity.",
    )

    mock_wrapper.compute_client.launch_instance.side_effect = [
        cap_error,
        MagicMock(data=mock_instance),
    ]

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-Beta", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=2.0)

    assert coordinator.is_stopped()
    assert coordinator.capacity_errors == 1
    assert coordinator.total_attempts == 2
