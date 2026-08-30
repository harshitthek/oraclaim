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
    assert cfg.ocpus == 1.0
    assert cfg.memory_in_gbs == 6.0
    assert cfg.display_name == "WorldTree-ARM-1Core-6GB"
    assert cfg.base_cadence == 28.0
    assert cfg.phase_offset == 14.0


def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("TARGET_OCPUS", "2.0")
    monkeypatch.setenv("TARGET_MEMORY_GBS", "12.0")
    monkeypatch.setenv("TARGET_DISPLAY_NAME", "Custom-ARM-Node")

    cfg = ClaimerConfig.load_from_env_or_file()
    assert cfg.ocpus == 2.0
    assert cfg.memory_in_gbs == 12.0
    assert cfg.display_name == "Custom-ARM-Node"
