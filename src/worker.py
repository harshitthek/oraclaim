import random
import time
from datetime import datetime
import oci

from src.config import ClaimerConfig
from src.coordinator import ClaimCoordinator
from src.oci_client import OCIClientWrapper


def is_surge_window() -> bool:
    """Detects whether current timestamp is near account lease drop windows."""
    now = datetime.now()
    minute = now.minute
    second = now.second
    return (minute in [59, 14, 29, 44] and second >= 30) or (minute in [0, 1, 15, 16, 30, 31, 45, 46])


def run_claimer_worker(
    worker_id: str,
    config: ClaimerConfig,
    coordinator: ClaimCoordinator,
    oci_wrapper: OCIClientWrapper,
    ad_name: str,
    image_id: str,
    subnet_id: str,
) -> None:
    """Synchronized pipeline worker thread."""
    while not coordinator.is_stopped():
        surge = is_surge_window()
        attempt_num, target_fd = coordinator.acquire_next_turn(worker_id, is_surge=surge)

        if coordinator.is_stopped():
            break

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode_tag = "🔥 SURGE" if surge else "SYNC"
        fd_display = target_fd if target_fd else "ANY_FD"

        launch_kwargs = {
            "compartment_id": oci_wrapper.tenancy_id,
            "availability_domain": ad_name,
            "shape": config.shape,
            "display_name": config.display_name,
            "image_id": image_id,
            "create_vnic_details": oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id, assign_public_ip=True
            ),
            "metadata": {"ssh_authorized_keys": config.public_ssh_key},
        }

        if config.is_flex_shape:
            launch_kwargs["shape_config"] = oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=config.ocpus, memory_in_gbs=config.memory_in_gbs
            )

        if config.boot_volume_size_in_gbs:
            launch_kwargs["source_details"] = oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=image_id,
                boot_volume_size_in_gbs=config.boot_volume_size_in_gbs,
            )

        if target_fd:
            launch_kwargs["fault_domain"] = target_fd

        launch_details = oci.core.models.LaunchInstanceDetails(**launch_kwargs)

        spec_str = (
            f"{config.shape} ({config.ocpus:.0f} OCPU / {config.memory_in_gbs:.0f} GB RAM)"
            if config.is_flex_shape
            else f"{config.shape}"
        )

        start_req = time.time()
        try:
            print(
                f"[{now_str}] [#{attempt_num}] [{worker_id}] [{fd_display}] [{mode_tag}] Launching {spec_str}...",
                flush=True,
            )
            response = oci_wrapper.compute_client.launch_instance(launch_details)
            instance = response.data
            coordinator.trigger_success(worker_id, instance.id, instance.display_name, target_fd)
            break

        except oci.exceptions.ServiceError as e:
            latency = time.time() - start_req
            msg = str(e.message).lower()

            if e.status == 500 or "out of host capacity" in msg or "capacity" in e.code.lower():
                with coordinator.lock:
                    coordinator.capacity_errors += 1
                print(
                    f"   -> [{worker_id}] Out of capacity in {fd_display} (Latency: {latency:.2f}s). Rotating...",
                    flush=True,
                )

            elif e.status == 429 or "toomanyrequests" in e.code.lower():
                retry_header = 38.0
                if hasattr(e, "headers") and e.headers and "retry-after" in e.headers:
                    try:
                        retry_header = float(e.headers["retry-after"])
                    except Exception:
                        pass
                coordinator.broadcast_rate_limit(worker_id, retry_header)

            elif e.status in [502, 503, 504]:
                coordinator.broadcast_rate_limit(worker_id, 20.0)

            else:
                print(f"   -> [{worker_id}] Notice [{e.status}]: {e.message}", flush=True)

        except Exception as ex:
            print(f"   -> [{worker_id}] Glitch: {ex}", flush=True)

        coordinator.write_status_snapshot()
