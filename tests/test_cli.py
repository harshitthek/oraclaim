import sys
from unittest.mock import patch, MagicMock
import pytest
from src.cli import parse_args, main


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
        "--dry-run"
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
    assert args.dry_run is True


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
            "--dry-run"
        ]
    )

    mock_wrapper = MagicMock()
    mock_wrapper.get_availability_domain.return_value = "AD-1"
    mock_wrapper.get_fault_domains.return_value = ["FD-1"]
    mock_wrapper.discover_image.return_value = "IMG-1"
    mock_wrapper.discover_public_subnet.return_value = "SUBNET-1"
    mock_wrapper_cls.return_value = mock_wrapper

    # Should run and exit cleanly without starting background worker loops
    main()
    assert mock_wrapper.get_availability_domain.called
