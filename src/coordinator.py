import json
import os
import random
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class ClaimCoordinator:
    """Thread-safe central coordinator managing phase locking, threat broadcasts,

    fault domain distribution, and atomic termination.
    """

    def __init__(self, fault_domain_candidates: List[Optional[str]], success_file: str, status_file: str):
        self.fd_candidates = fault_domain_candidates
        self.success_file = success_file
        self.status_file = status_file

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.global_cooldown_until: float = 0.0
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
        print(f"🎉 [SUCCESS] Instance Claimed by {worker_name}!", flush=True)
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

    def broadcast_rate_limit(self, source_worker: str, base_cooldown: float = 36.0) -> None:
        with self.lock:
            self.rate_limit_hits += 1
            cooldown = base_cooldown + random.uniform(1.0, 6.0)
            target_time = time.time() + cooldown
            if target_time > self.global_cooldown_until:
                self.global_cooldown_until = target_time
                print(
                    f"   -> [BROADCAST from {source_worker}] Rate limit hit. Inter-worker cooldown: {cooldown:.1f}s",
                    flush=True,
                )

    def get_next_assignment(self, worker_name: str) -> Tuple[int, Optional[str]]:
        with self.lock:
            self.total_attempts += 1
            fd = self.fd_candidates[self.fd_index % len(self.fd_candidates)]
            self.fd_index += 1
            attempt_num = self.total_attempts
            self.worker_heartbeats[worker_name] = datetime.now().strftime("%H:%M:%S")
            return attempt_num, fd

    def check_cooldown(self) -> float:
        with self.lock:
            remaining = self.global_cooldown_until - time.time()
            return max(0.0, remaining)

    def write_status_snapshot(self) -> None:
        try:
            snapshot = {
                "total_attempts": self.total_attempts,
                "capacity_errors": self.capacity_errors,
                "rate_limits": self.rate_limit_hits,
                "workers": self.worker_heartbeats,
                "start_time": self.start_time,
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception:
            pass
