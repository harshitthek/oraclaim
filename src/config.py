import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClaimerConfig:
    config_file: str
    profile: str
    private_key_path: str
    public_ssh_key: str
    ocpus: float = 1.0
    memory_in_gbs: float = 6.0
    display_name: str = "WorldTree-ARM-1Core-6GB"
    base_cadence: float = 28.0
    phase_offset: float = 14.0
    surge_cadence: float = 18.0
    min_safe_interval: float = 14.0

    @classmethod
    def load_from_env_or_file(
        cls,
        config_file: Optional[str] = None,
        key_path: Optional[str] = None,
        pub_key_path: Optional[str] = None,
    ) -> "ClaimerConfig":
        base_dir = os.getcwd()

        cfg_path = config_file or os.getenv("OCI_CONFIG_FILE") or os.path.join(base_dir, "config.ini")
        if not os.path.exists(cfg_path):
            cfg_path_alt = os.path.join(base_dir, "oci_config")
            if os.path.exists(cfg_path_alt):
                cfg_path = cfg_path_alt

        priv_key = key_path or os.getenv("OCI_KEY_FILE")
        if not priv_key:
            for f in os.listdir(base_dir):
                if f.endswith(".key") and not f.endswith(".pub"):
                    priv_key = os.path.join(base_dir, f)
                    break

        pub_key_str = ""
        pub_key_file = pub_key_path or os.getenv("OCI_SSH_PUBLIC_KEY_FILE")
        if pub_key_file and os.path.exists(pub_key_file):
            with open(pub_key_file, "r", encoding="utf-8") as f:
                pub_key_str = f.read().strip()
        else:
            for f in os.listdir(base_dir):
                if f.endswith(".pub") or f.endswith(".key.pub"):
                    with open(os.path.join(base_dir, f), "r", encoding="utf-8") as f:
                        pub_key_str = f.read().strip()
                        break

        return cls(
            config_file=cfg_path,
            profile=os.getenv("OCI_PROFILE", "DEFAULT"),
            private_key_path=priv_key or "",
            public_ssh_key=pub_key_str,
            ocpus=float(os.getenv("TARGET_OCPUS", "1.0")),
            memory_in_gbs=float(os.getenv("TARGET_MEMORY_GBS", "6.0")),
            display_name=os.getenv("TARGET_DISPLAY_NAME", "WorldTree-ARM-1Core-6GB"),
            base_cadence=float(os.getenv("BASE_CADENCE_SECONDS", "28.0")),
            phase_offset=float(os.getenv("PHASE_OFFSET_SECONDS", "14.0")),
            surge_cadence=float(os.getenv("SURGE_CADENCE_SECONDS", "18.0")),
        )
