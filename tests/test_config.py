import os
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
    monkeypatch.setenv("TARGET_DISPLAY_NAME", "Production-Oracle-Node")
    monkeypatch.setenv("NUM_WORKERS", "3")

    cfg = ClaimerConfig.load_from_env_or_file()
    assert cfg.ocpus == 4.0
    assert cfg.memory_in_gbs == 24.0
    assert cfg.boot_volume_size_in_gbs == 100
    assert cfg.os_name == "Oracle Linux"
    assert cfg.display_name == "Production-Oracle-Node"
    assert cfg.num_workers == 3
