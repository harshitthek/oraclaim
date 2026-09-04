import json
import os
import random
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class ClaimCoordinator:
    """Strict Token-Ring Sequencer Coordinator.

    Guarantees that no two workers can ever launch requests simultaneously,
    strictly spacing all requests by the configured cadence even after rate-limit cooldowns.
    """

    def __init__(
        self,
        fault_domain_candidates: List[Optional[str]],
        success_file: str,
        status_file: str,
        cadence_seconds: float = 20.0,
        min_interval_seconds: float = 14.0,
        max_cadence: float = 45.0,
        max_cadence_seconds: Optional[float] = None,
        discord_webhook_url: Optional[str] = None,
    ):
        self.fd_candidates = fault_domain_candidates
        self.success_file = success_file
        self.status_file = status_file
        self.max_cadence: float = float(max_cadence if max_cadence_seconds is None else max_cadence_seconds)
        self.cadence = min(cadence_seconds, self.max_cadence)
        self.min_interval = min_interval_seconds
        self.consecutive_clean: int = 0
        self.discord_webhook_url: Optional[str] = discord_webhook_url

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.next_allowed_request_time: float = time.time()
        self.fd_index: int = 0
        self.total_attempts: int = 0
        self.capacity_errors: int = 0
        self.rate_limit_hits: int = 0
        self.start_time: str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.worker_heartbeats: Dict[str, str] = {}

    def is_stopped(self) -> bool:
        return self.stop_event.is_set() or os.path.exists(self.success_file)

    def trigger_success(self, worker_name: str, instance_id: str, display_name: str, fd_name: Optional[str]) -> None:
        self.stop_event.set()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fd_display = fd_name if fd_name else "WILDCARD (Auto-Placed by Oracle)"

        print("\n" + "=" * 75, flush=True)
        print(f"[SUCCESS] Instance Claimed by {worker_name}!", flush=True)
        print(f"Name:         {display_name}", flush=True)
        print(f"Instance ID:  {instance_id}", flush=True)
        print(f"Fault Domain: {fd_display}", flush=True)
        print(f"Time:         {now_str}", flush=True)
        print("=" * 75 + "\n", flush=True)

        try:
            with open(self.success_file, "w", encoding="utf-8") as f:
                f.write(
                    f"SUCCESS!\nName: {display_name}\nID: {instance_id}\nFD: {fd_display}\nWorker: {worker_name}\nTime: {now_str}\n"
                )
        except Exception:
            pass

        self.send_discord_notification(worker_name, instance_id, display_name, fd_display, now_str)

    def send_discord_notification(
        self, worker_name: str, instance_id: str, display_name: str, fd_display: str, now_str: str
    ) -> bool:
        if not self.discord_webhook_url:
            return False
        try:
            import urllib.request

            payload = {
                "content": "@everyone [SUCCESS] Oracle Cloud Always-Free Instance Claimed!",
                "embeds": [
                    {
                        "title": "Instance Provisioned Successfully",
                        "color": 65280,
                        "fields": [
                            {"name": "Instance Name", "value": f"`{display_name}`", "inline": True},
                            {"name": "Worker", "value": f"`{worker_name}`", "inline": True},
                            {"name": "Fault Domain", "value": f"`{fd_display}`", "inline": True},
                            {"name": "Instance ID", "value": f"```{instance_id}```", "inline": False},
                            {"name": "Timestamp", "value": now_str, "inline": True},
                        ],
                        "footer": {"text": "OraClaim Auto-Provisioning Engine"},
                    }
                ],
            }
            req = urllib.request.Request(
                self.discord_webhook_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "OraClaim/1.0"},
            )
            urllib.request.urlopen(req, timeout=10)
            print("[+] Discord alert sent to webhook successfully!", flush=True)
            return True
        except Exception as ex:
            print(f"   [!] Failed to send Discord alert: {ex}", flush=True)
            return False

    def broadcast_rate_limit(self, source_worker: str, cooldown_seconds: float = 38.0) -> None:
        with self.lock:
            self.rate_limit_hits += 1
            now = time.time()

            # AIMD: Increase cadence on rate limit (capped at max_cadence)
            self.cadence = min(self.max_cadence, self.cadence + 2.0)
            self.consecutive_clean = 0

            self.next_allowed_request_time = max(
                self.next_allowed_request_time,
                now + max(cooldown_seconds, self.cadence) + random.uniform(1.0, 3.0),
            )
            print(
                f"   -> [LEARNED RATE LIMIT from {source_worker}] Auto-tuning cadence to {self.cadence:.1f}s (ceiling: {self.max_cadence:.1f}s). Pipeline spaced by {cooldown_seconds:.1f}s",
                flush=True,
            )

    def record_capacity_check(self) -> None:
        with self.lock:
            self.capacity_errors += 1
            self.consecutive_clean += 1

            # AIMD Recovery: If we have 6 consecutive clean capacity checks, gently optimize cadence
            if self.consecutive_clean >= 6 and self.cadence > self.min_interval:
                self.cadence = max(self.min_interval, self.cadence - 1.0)
                self.consecutive_clean = 0
                print(f"   -> [PIPELINE OPTIMIZED] 6 clean checks! Accelerated cadence to {self.cadence:.1f}s", flush=True)

    def acquire_next_turn(self, worker_name: str, is_surge: bool = False) -> Tuple[int, Optional[str]]:
        """Atomically allocates a dedicated, collision-free time slot in the launch pipeline."""
        while True:
            if self.is_stopped():
                return 0, None

            with self.lock:
                now = time.time()
                scheduled_time = max(now, self.next_allowed_request_time)
                sleep_needed = scheduled_time - now

            if sleep_needed > 0.05:
                if self.stop_event.wait(timeout=sleep_needed) or self.is_stopped():
                    return 0, None
                continue  # Wake up and re-check in case another thread extended the rate limit!

            with self.lock:
                if self.is_stopped():
                    return 0, None

                # Final check to ensure we still have the floor
                now = time.time()
                if now < self.next_allowed_request_time:
                    continue

                # It's our turn. Claim the slot and advance the global clock.
                effective_cadence = self.cadence * 0.75 if is_surge else self.cadence
                jitter = random.uniform(-1.0, 1.0) if self.min_interval > 1.0 else 0.0
                slot_interval = max(self.min_interval, effective_cadence + jitter)

                self.next_allowed_request_time = now + slot_interval

                self.total_attempts += 1
                fd = self.fd_candidates[self.fd_index % len(self.fd_candidates)]
                self.fd_index += 1
                attempt_num = self.total_attempts
                self.worker_heartbeats[worker_name] = datetime.now().strftime("%H:%M:%S")

                return attempt_num, fd

    def write_status_snapshot(self) -> None:
        try:
            snapshot = {
                "total_attempts": self.total_attempts,
                "capacity_errors": self.capacity_errors,
                "rate_limits": self.rate_limit_hits,
                "cadence": round(self.cadence, 2),
                "max_cadence": self.max_cadence,
                "workers": self.worker_heartbeats,
                "start_time": self.start_time,
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception:
            pass
