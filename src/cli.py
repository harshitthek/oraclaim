import argparse
import os
import signal
import sys
import threading
import time

from src.config import ClaimerConfig
from src.coordinator import ClaimCoordinator
from src.oci_client import OCIClientWrapper
from src.worker import run_claimer_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="oraclaim",
        description="Universal Asynchronous Multi-Worker Oracle Cloud Always Free Instance Auto-Claimer.",
    )
    parser.add_argument(
        "-c", "--config", dest="config_file", help="Path to OCI config file (default: ./config.ini or ./oci_config)"
    )
    parser.add_argument(
        "-k", "--key", dest="key_file", help="Path to private signing key (.key or .pem)"
    )
    parser.add_argument(
        "-p", "--pub-key", dest="pub_key_file", help="Path to public SSH key (.pub)"
    )
    parser.add_argument(
        "--shape", type=str, default="VM.Standard.A1.Flex", help="Target instance shape (e.g. VM.Standard.A1.Flex, VM.Standard.E2.1.Micro)"
    )
    parser.add_argument(
        "--ocpus", type=float, default=1.0, help="Number of OCPUs (for Flex shapes, 1.0 to 4.0)"
    )
    parser.add_argument(
        "--memory", type=float, default=6.0, help="Amount of RAM in GB (for Flex shapes, 1.0 to 24.0)"
    )
    parser.add_argument(
        "--os", dest="os_name", type=str, default="Canonical Ubuntu", help="Operating System name (e.g. 'Canonical Ubuntu', 'Oracle Linux', 'Debian')"
    )
    parser.add_argument(
        "--os-version", type=str, default=None, help="Operating System version (e.g. '24.04', '9', '12')"
    )
    parser.add_argument(
        "--boot-volume-gbs", type=int, default=None, help="Custom boot volume size in GB (e.g. 50, 100, 200)"
    )
    parser.add_argument(
        "--name", type=str, default="OCI-Auto-Claimed-Instance", help="Custom display name for the instance"
    )
    parser.add_argument(
        "--workers", type=int, default=2, help="Number of concurrent alternating worker threads (default: 2)"
    )
    parser.add_argument(
        "--cadence", type=float, default=20.0, help="Base polling cadence in seconds across the pipeline (default: 20.0)"
    )
    parser.add_argument(
        "--max-cadence", type=float, default=45.0, help="Maximum cadence ceiling in seconds (default: 45.0)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate credentials and resource discovery without launching"
    )
    return parser.parse_args()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except Exception:
                pass

    args = parse_args()

    cfg = ClaimerConfig.load_from_env_or_file(
        config_file=args.config_file, key_path=args.key_file, pub_key_path=args.pub_key_file
    )
    cfg.shape = args.shape
    cfg.ocpus = args.ocpus
    cfg.memory_in_gbs = args.memory
    cfg.os_name = args.os_name
    cfg.os_version = args.os_version
    cfg.boot_volume_size_in_gbs = args.boot_volume_gbs
    cfg.display_name = args.name
    cfg.num_workers = args.workers
    cfg.base_cadence = args.cadence
    cfg.max_cadence = args.max_cadence

    shape_info = f"{cfg.shape} ({cfg.ocpus:.0f} OCPU / {cfg.memory_in_gbs:.0f} GB RAM)" if cfg.is_flex_shape else cfg.shape

    print("\n" + "=" * 75)
    print("  [+] ORACLAIM AUTO-PROVISIONING ENGINE")
    print("=" * 75)
    print(f"  - Target Shape:     {shape_info}")
    print(f"  - Target OS:        {cfg.os_name} {cfg.os_version or '(Latest)'}")
    if cfg.boot_volume_size_in_gbs:
        print(f"  - Boot Volume:      {cfg.boot_volume_size_in_gbs} GB")
    print(f"  - Display Name:     {cfg.display_name}")
    print(f"  - Pipeline Cadence: Every ~{cfg.base_cadence:.0f}s (Max Ceiling: {cfg.max_cadence:.0f}s, Token-Ring Sequencer)")
    print(f"  - Workers:          {cfg.num_workers} Concurrent Synchronized Threads")
    print(f"  - Config File:      {cfg.config_file}")
    print("=" * 75 + "\n", flush=True)

    if not cfg.public_ssh_key:
        print("[!] Error: No public SSH key provided. Set OCI_SSH_PUBLIC_KEY_FILE or pass --pub-key.")
        sys.exit(1)

    oci_wrapper = OCIClientWrapper(cfg.config_file, cfg.profile, cfg.private_key_path)

    ad_name = oci_wrapper.get_availability_domain()
    raw_fds = oci_wrapper.get_fault_domains(ad_name)
    fd_candidates = [None] + raw_fds

    image_id = oci_wrapper.discover_image(cfg.os_name, cfg.shape, cfg.os_version)
    subnet_id = oci_wrapper.discover_public_subnet()

    print(f"[+] Availability Domain: {ad_name}")
    print(f"[+] Target Subnet:        {subnet_id}")
    print(f"[+] Discovered Image:     {image_id}")
    print(f"[+] Placement Targets:    Wildcard (ANY_FD), {', '.join(raw_fds)}")

    if args.dry_run:
        print("\n[+] Dry run pre-flight checks passed! All credentials, image catalogs, and discovery endpoints valid!")
        return

    success_file = os.path.join(os.getcwd(), "instance_success.txt")
    status_file = os.path.join(os.getcwd(), "claimer_status.json")

    coordinator = ClaimCoordinator(
        fd_candidates,
        success_file,
        status_file,
        cadence_seconds=cfg.base_cadence,
        min_interval_seconds=cfg.min_safe_interval,
        max_cadence=cfg.max_cadence,
    )

    def sig_handler(sig, frame):
        print("\n[*] Stopping claimer workers gracefully...", flush=True)
        coordinator.stop_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    worker_threads = []
    for i in range(cfg.num_workers):
        worker_name = f"Worker-{chr(65 + i)}"
        t = threading.Thread(
            target=run_claimer_worker,
            args=(worker_name, cfg, coordinator, oci_wrapper, ad_name, image_id, subnet_id),
            daemon=True,
        )
        worker_threads.append(t)
        t.start()

    while not coordinator.is_stopped():
        coordinator.stop_event.wait(timeout=1.0)

    print("[*] Claimer process finished.")


if __name__ == "__main__":
    main()
