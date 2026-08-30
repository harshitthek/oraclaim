import os
import tempfile
import pytest
from src.config import ClaimerConfig


def test_default_config():
    cfg = ClaimerConfig(
        config_file="test_config.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
    )
    assert cfg.shape == "VM.Standard.A1.Flex"
    assert cfg.is_flex_shape is True
    assert cfg.ocpus == 1.0
    assert cfg.memory_in_gbs == 6.0
    assert cfg.display_name == "OCI-Auto-Claimed-Instance"
    assert cfg.os_name == "Canonical Ubuntu"
    assert cfg.num_workers == 2
    assert cfg.base_cadence == 28.0
    assert cfg.phase_offset == 14.0
    assert cfg.surge_cadence == 18.0
    assert cfg.min_safe_interval == 14.0


def test_fixed_amd_shape_config():
    cfg = ClaimerConfig(
        config_file="test_config.ini",
        profile="DEFAULT",
        private_key_path="test.key",
        public_ssh_key="ssh-rsa AAAA...",
        shape="VM.Standard.E2.1.Micro",
        os_name="Oracle Linux",
    )
    assert cfg.shape == "VM.Standard.E2.1.Micro"
    assert cfg.is_flex_shape is False
    assert cfg.os_name == "Oracle Linux"


def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("TARGET_SHAPE", "VM.Standard.A1.Flex")
    monkeypatch.setenv("TARGET_OCPUS", "4.0")
    monkeypatch.setenv("TARGET_MEMORY_GBS", "24.0")
    monkeypatch.setenv("TARGET_BOOT_VOLUME_GBS", "100")
    monkeypatch.setenv("TARGET_OS_NAME", "Oracle Linux")
    monkeypatch.setenv("TARGET_OS_VERSION", "9")
    monkeypatch.setenv("TARGET_DISPLAY_NAME", "Production-Oracle-Node")
    monkeypatch.setenv("NUM_WORKERS", "3")
    monkeypatch.setenv("BASE_CADENCE_SECONDS", "20.0")
    monkeypatch.setenv("PHASE_OFFSET_SECONDS", "6.6")
    monkeypatch.setenv("SURGE_CADENCE_SECONDS", "12.0")

    cfg = ClaimerConfig.load_from_env_or_file()
    assert cfg.ocpus == 4.0
    assert cfg.memory_in_gbs == 24.0
    assert cfg.boot_volume_size_in_gbs == 100
    assert cfg.os_name == "Oracle Linux"
    assert cfg.os_version == "9"
    assert cfg.display_name == "Production-Oracle-Node"
    assert cfg.num_workers == 3
    assert cfg.base_cadence == 20.0
    assert cfg.phase_offset == 6.6
    assert cfg.surge_cadence == 12.0


def test_config_auto_discover_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # Create dummy key files in working directory
    dummy_key = tmp_path / "my-auth.key"
    dummy_key.write_text("DUMMY_PRIVATE_KEY")
    dummy_pub = tmp_path / "my-auth.pub"
    dummy_pub.write_text("ssh-rsa DUMMY_PUBLIC_KEY")
    dummy_cfg = tmp_path / "config.ini"
    dummy_cfg.write_text("[DEFAULT]\nuser=test\n")

    cfg = ClaimerConfig.load_from_env_or_file(config_file=str(dummy_cfg))
    assert cfg.private_key_path == str(dummy_key)
    assert cfg.public_ssh_key == "ssh-rsa DUMMY_PUBLIC_KEY"
