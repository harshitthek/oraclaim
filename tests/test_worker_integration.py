import threading
import time
from unittest.mock import MagicMock
import oci
import oci.core.models

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
        boot_volume_size_in_gbs=50,
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
    t.join(timeout=5.0)

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
    t.join(timeout=5.0)

    assert coordinator.is_stopped()
    assert coordinator.capacity_errors == 1
    assert coordinator.total_attempts == 2


def test_worker_halts_on_limit_exceeded(tmp_path):
    """Simulate ServiceError(status=400, code='LimitExceeded') and verify

    worker sets stop_event and exits cleanly with 1 attempt.
    """
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

    limit_error = oci.exceptions.ServiceError(
        status=400,
        code="LimitExceeded",
        headers={},
        message="Account service limit reached for standard-a1-core-count",
    )
    mock_wrapper.compute_client.launch_instance.side_effect = limit_error

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-LimitExceeded", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive(), "Worker thread hung on LimitExceeded!"
    assert coordinator.stop_event.is_set()
    assert coordinator.is_stopped()
    assert coordinator.total_attempts == 1


def test_worker_halts_on_quota_exceeded_message(tmp_path):
    """Test case for message containing 'The following service limits were exceeded: standard-a1-core-count'

    with code=None.
    """
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

    quota_error = oci.exceptions.ServiceError(
        status=400,
        code=None,
        headers={},
        message="The following service limits were exceeded: standard-a1-core-count",
    )
    mock_wrapper.compute_client.launch_instance.side_effect = quota_error

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-QuotaMsg", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=5.0)

    assert not t.is_alive(), "Worker thread hung on quota message variant!"
    assert coordinator.stop_event.is_set()
    assert coordinator.is_stopped()
    assert coordinator.total_attempts == 1


def test_worker_429_rate_limit_retry_after(tmp_path):
    """Verify that HTTP 429 with retry-after header executes AIMD backoff

    and does NOT falsely trigger coordinator.stop_event.
    """
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    cfg = ClaimerConfig(
        config_file="test.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
        shape="VM.Standard.E2.1.Micro",  # Test non-flex shape branch as well
    )

    coordinator = ClaimCoordinator(["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=20.0, max_cadence=45.0)

    mock_wrapper = MagicMock(spec=OCIClientWrapper)
    mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
    mock_wrapper.compute_client = MagicMock()

    rate_limit_err = oci.exceptions.ServiceError(
        status=429,
        code="TooManyRequests",
        headers={"retry-after": "40.0"},
        message="Too many requests to Compute API. Rate limit exceeded.",
    )

    def launch_side_effect(*args, **kwargs):
        # Stop worker after recording the rate limit hit so the test finishes promptly
        def delayed_stop():
            time.sleep(0.05)
            coordinator.stop_event.set()
        threading.Thread(target=delayed_stop, daemon=True).start()
        raise rate_limit_err

    mock_wrapper.compute_client.launch_instance.side_effect = launch_side_effect

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-429", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=5.0)

    assert coordinator.rate_limit_hits == 1
    assert coordinator.cadence == 22.0  # 20.0 + 2.0 AIMD step


def test_worker_502_gateway_error_resilience(tmp_path):
    """Verify 502/503/504 gateway backoff resilience."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    cfg = ClaimerConfig(
        config_file="test.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
    )

    coordinator = ClaimCoordinator(["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=20.0, max_cadence=45.0)

    mock_wrapper = MagicMock(spec=OCIClientWrapper)
    mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
    mock_wrapper.compute_client = MagicMock()

    gateway_error = oci.exceptions.ServiceError(
        status=502,
        code="BadGateway",
        headers={},
        message="Bad Gateway from upstream edge proxy",
    )

    def launch_side_effect(*args, **kwargs):
        def delayed_stop():
            time.sleep(0.05)
            coordinator.stop_event.set()
        threading.Thread(target=delayed_stop, daemon=True).start()
        raise gateway_error

    mock_wrapper.compute_client.launch_instance.side_effect = launch_side_effect

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-502", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=5.0)

    assert coordinator.rate_limit_hits == 1
    assert coordinator.cadence == 22.0


def test_worker_generic_notice_and_glitch_exception(tmp_path):
    """Verify handling of generic ServiceError (notice) and generic Exception (glitch)."""
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

    # 1st attempt: 404 Notice
    # 2nd attempt: generic Exception
    # 3rd attempt: stop
    notice_error = oci.exceptions.ServiceError(
        status=404,
        code="NotFound",
        headers={},
        message="Resource not found",
    )

    def side_effect(*args, **kwargs):
        if coordinator.total_attempts == 1:
            raise notice_error
        elif coordinator.total_attempts == 2:
            raise RuntimeError("Transient connection reset")
        else:
            coordinator.stop_event.set()
            return MagicMock()

    mock_wrapper.compute_client.launch_instance.side_effect = side_effect

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-Notice", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=5.0)

    assert coordinator.total_attempts >= 2


def test_worker_429_invalid_retry_after_header_fallback(tmp_path):
    """Verify that HTTP 429 with non-numeric retry-after header safely falls back to default 38.0s."""
    success_file = str(tmp_path / "success.txt")
    status_file = str(tmp_path / "status.json")

    cfg = ClaimerConfig(
        config_file="test.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
    )

    coordinator = ClaimCoordinator(["FAULT-DOMAIN-1"], success_file, status_file, cadence_seconds=20.0, max_cadence=45.0)

    mock_wrapper = MagicMock(spec=OCIClientWrapper)
    mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
    mock_wrapper.compute_client = MagicMock()

    rate_limit_err = oci.exceptions.ServiceError(
        status=429,
        code="TooManyRequests",
        headers={"retry-after": "not-a-number"},
        message="Too many requests",
    )

    def launch_side_effect(*args, **kwargs):
        def delayed_stop():
            time.sleep(0.05)
            coordinator.stop_event.set()
        threading.Thread(target=delayed_stop, daemon=True).start()
        raise rate_limit_err

    mock_wrapper.compute_client.launch_instance.side_effect = launch_side_effect

    t = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-InvalidRetry", cfg, coordinator, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
        daemon=True,
    )
    t.start()
    t.join(timeout=5.0)

    assert coordinator.rate_limit_hits == 1

