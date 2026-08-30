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
        prog="oci-claim",
        description="High-Performance Asynchronous Dual-Worker Oracle Cloud Always Free ARM Claimer.",
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
        "--ocpus", type=float, default=1.0, help="Number of OCPUs to request (default: 1.0)"
    )
    parser.add_argument(
        "--memory", type=float, default=6.0, help="Amount of RAM in GB to request (default: 6.0)"
    )
    parser.add_argument(
        "--name", type=str, default="WorldTree-ARM-1Core-6GB", help="Display name for the instance"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate credentials and exit without launching"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    cfg = ClaimerConfig.load_from_env_or_file(
        config_file=args.config_file, key_path=args.key_file, pub_key_path=args.pub_key_file
    )
    cfg.ocpus = args.ocpus
    cfg.memory_in_gbs = args.memory
    cfg.display_name = args.name

    print("\n" + "=" * 75)
    print("  🚀 OCI ARM SMART AUTO-CLAIMER")
    print("=" * 75)
    print(f"  • Target Shape:     VM.Standard.A1.Flex ({cfg.ocpus:.0f} Core / {cfg.memory_in_gbs:.0f} GB RAM)")
    print(f"  • Display Name:     {cfg.display_name}")
    print(f"  • Config File:      {cfg.config_file}")
    print(f"  • Key File:         {cfg.private_key_path}")
    print("=" * 75 + "\n", flush=True)

    if not cfg.public_ssh_key:
        print("[!] Error: No public SSH key provided. Set OCI_SSH_PUBLIC_KEY_FILE or pass --pub-key.")
        sys.exit(1)

    oci_wrapper = OCIClientWrapper(cfg.config_file, cfg.profile, cfg.private_key_path)

    ad_name = oci_wrapper.get_availability_domain()
    raw_fds = oci_wrapper.get_fault_domains(ad_name)
    # Include Wildcard (None) for full datacenter placement
    fd_candidates = [None] + raw_fds

    image_id = oci_wrapper.discover_arm_image()
    subnet_id = oci_wrapper.discover_public_subnet()

    print(f"[+] Availability Domain: {ad_name}")
    print(f"[+] Target Subnet:        {subnet_id}")
    print(f"[+] ARM Image:            {image_id}")
    print(f"[+] Fault Domains:        Wildcard (ANY_FD), {', '.join(raw_fds)}")
    print("[+] Architecture:         Phase-Locked Dual-Worker (14s cadence)")

    if args.dry_run:
        print("\n[✔] Dry run successful. All credentials and discovery endpoints valid!")
        return

    success_file = os.path.join(os.getcwd(), "instance_success.txt")
    status_file = os.path.join(os.getcwd(), "claimer_status.json")

    coordinator = ClaimCoordinator(fd_candidates, success_file, status_file)

    def sig_handler(sig, frame):
        print("\n[*] Stopping claimer workers gracefully...", flush=True)
        coordinator.stop_event.set()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    t1 = threading.Thread(
        target=run_claimer_worker,
        args=("Worker-Alpha", 0.0, cfg, coordinator, oci_wrapper, ad_name, image_id, subnet_id),
        daemon=True,
    )
    t2 = threading.Thread(
        target=run_claimer_worker,
        args=(
            "Worker-Beta",
            cfg.phase_offset,
            cfg,
            coordinator,
            oci_wrapper,
            ad_name,
            image_id,
            subnet_id,
        ),
        daemon=True,
    )

    t1.start()
    t2.start()

    while not coordinator.is_stopped():
        time.sleep(1.0)

    print("[*] Claimer process finished.")


if __name__ == "__main__":
    main()
