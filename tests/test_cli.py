import os
import signal
import sys
from unittest.mock import MagicMock, patch
import pytest
from src.cli import main, parse_args


def test_cli_argument_parsing(monkeypatch):
    test_args = [
        "oraclaim",
        "--shape", "VM.Standard.E2.1.Micro",
        "--ocpus", "2.0",
        "--memory", "12.0",
        "--os", "Oracle Linux",
        "--os-version", "9",
        "--boot-volume-gbs", "100",
        "--name", "My-Server",
        "--workers", "4",
        "--cadence", "15.0",
        "--max-cadence", "40.0",
        "--dry-run",
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    args = parse_args()
    assert args.shape == "VM.Standard.E2.1.Micro"
    assert args.ocpus == 2.0
    assert args.memory == 12.0
    assert args.os_name == "Oracle Linux"
    assert args.os_version == "9"
    assert args.boot_volume_gbs == 100
    assert args.name == "My-Server"
    assert args.workers == 4
    assert args.cadence == 15.0
    assert args.max_cadence == 40.0
    assert args.dry_run is True


def test_cli_max_cadence_argument(monkeypatch):
    """Verify default 45.0s and custom --max-cadence parsing."""
    monkeypatch.setattr(sys, "argv", ["oraclaim"])
    args = parse_args()
    assert args.max_cadence == 45.0

    monkeypatch.setattr(sys, "argv", ["oraclaim", "--max-cadence", "32.5"])
    args_custom = parse_args()
    assert args_custom.max_cadence == 32.5


def test_cli_missing_ssh_key_exits(monkeypatch, tmp_path):
    """Verify preflight CLI error handling when no SSH key is found."""
    dummy_cfg = tmp_path / "config.ini"
    dummy_cfg.write_text("[DEFAULT]\nuser=test\n")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oraclaim",
            "-c", str(dummy_cfg),
            "--dry-run",
        ],
    )
    # Ensure no SSH keys in working dir or env
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OCI_SSH_PUBLIC_KEY_FILE", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


@patch("src.cli.OCIClientWrapper")
def test_cli_dry_run_execution(mock_wrapper_cls, monkeypatch, tmp_path):
    dummy_key = tmp_path / "key.pub"
    dummy_key.write_text("ssh-rsa DUMMY")
    dummy_cfg = tmp_path / "config.ini"
    dummy_cfg.write_text("[DEFAULT]\nuser=test\n")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oraclaim",
            "-c", str(dummy_cfg),
            "-p", str(dummy_key),
            "--boot-volume-gbs", "50",
            "--dry-run",
        ],
    )

    mock_wrapper = MagicMock()
    mock_wrapper.get_availability_domain.return_value = "AD-1"
    mock_wrapper.get_fault_domains.return_value = ["FD-1"]
    mock_wrapper.discover_image.return_value = "IMG-1"
    mock_wrapper.discover_public_subnet.return_value = "SUBNET-1"
    mock_wrapper_cls.return_value = mock_wrapper

    main()
    assert mock_wrapper.get_availability_domain.called


@patch("src.cli.run_claimer_worker")
@patch("src.cli.OCIClientWrapper")
def test_cli_main_worker_launch_and_shutdown(mock_wrapper_cls, mock_run_worker, monkeypatch, tmp_path):
    """Verify main execution path when dry-run is False, launching workers and stopping cleanly."""
    monkeypatch.chdir(tmp_path)
    dummy_key = tmp_path / "key.pub"
    dummy_key.write_text("ssh-rsa DUMMY")
    dummy_cfg = tmp_path / "config.ini"
    dummy_cfg.write_text("[DEFAULT]\nuser=test\n")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oraclaim",
            "-c", str(dummy_cfg),
            "-p", str(dummy_key),
            "--workers", "2",
        ],
    )

    mock_wrapper = MagicMock()
    mock_wrapper.get_availability_domain.return_value = "AD-1"
    mock_wrapper.get_fault_domains.return_value = ["FD-1"]
    mock_wrapper.discover_image.return_value = "IMG-1"
    mock_wrapper.discover_public_subnet.return_value = "SUBNET-1"
    mock_wrapper_cls.return_value = mock_wrapper

    # Make worker signal stop after a brief moment to exercise while loop wait
    def fake_worker(worker_name, cfg, coordinator, oci_wrapper, ad_name, image_id, subnet_id):
        import time
        time.sleep(0.05)
        coordinator.stop_event.set()

    mock_run_worker.side_effect = fake_worker

    main()
    assert mock_wrapper.get_availability_domain.called



def test_cli_signal_handler(monkeypatch, tmp_path):
    """Test SIGINT/SIGTERM graceful shutdown handler in main()."""
    captured_handler = {}

    def mock_signal(sig, handler):
        captured_handler[sig] = handler

    monkeypatch.setattr(signal, "signal", mock_signal)

    dummy_key = tmp_path / "key.pub"
    dummy_key.write_text("ssh-rsa DUMMY")
    dummy_cfg = tmp_path / "config.ini"
    dummy_cfg.write_text("[DEFAULT]\nuser=test\n")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oraclaim",
            "-c", str(dummy_cfg),
            "-p", str(dummy_key),
        ],
    )

    with patch("src.cli.run_claimer_worker") as mock_worker, patch("src.cli.OCIClientWrapper") as mock_wrapper_cls:
        mock_wrapper = MagicMock()
        mock_wrapper.get_availability_domain.return_value = "AD-1"
        mock_wrapper.get_fault_domains.return_value = ["FD-1"]
        mock_wrapper.discover_image.return_value = "IMG-1"
        mock_wrapper.discover_public_subnet.return_value = "SUBNET-1"
        mock_wrapper_cls.return_value = mock_wrapper

        def fake_worker(worker_name, cfg, coordinator, oci_wrapper, ad_name, image_id, subnet_id):
            coordinator.stop_event.set()

        mock_worker.side_effect = fake_worker

        main()

    assert signal.SIGINT in captured_handler
    assert signal.SIGTERM in captured_handler

    # Invoke captured handler and verify SystemExit(0)
    with pytest.raises(SystemExit) as exc:
        captured_handler[signal.SIGINT](signal.SIGINT, None)
    assert exc.value.code == 0

