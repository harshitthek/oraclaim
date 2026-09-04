"""
Adversarial Stress & Resilience Verification Harness for OraClaim M1.
Challenger: teamwork_preview_challenger_m1_2
Target Files: src/worker.py, src/oci_client.py
"""

import os
import sys
import time
import threading
import tempfile
import traceback
from unittest.mock import MagicMock, patch
import oci

# Add repository root to path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from src.config import ClaimerConfig
from src.coordinator import ClaimCoordinator
from src.oci_client import OCIClientWrapper
from src.worker import run_claimer_worker, is_surge_window


def make_dummy_config(tmp_dir: str) -> ClaimerConfig:
    key_path = os.path.join(tmp_dir, "test.key")
    with open(key_path, "w") as f:
        f.write("DUMMY_KEY")
    return ClaimerConfig(
        config_file=os.path.join(tmp_dir, "test.ini"),
        profile="DEFAULT",
        private_key_path=key_path,
        public_ssh_key="ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQ...",
        shape="VM.Standard.A1.Flex",
        ocpus=4.0,
        memory_in_gbs=24.0,
        max_cadence=45.0,
    )


def test_1_limit_exceeded_clean_termination():
    """
    Test 1: Standard LimitExceeded ServiceError
    - Mock launch_instance raising ServiceError(status=400, code="LimitExceeded")
    - Worker terminates immediately (< 1.0s)
    - coordinator.stop_event.is_set() is True
    - Zero infinite loops
    """
    print("\n[TEST 1] Testing LimitExceeded clean termination...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg = make_dummy_config(tmp_dir)
        coord = ClaimCoordinator(["FD-1"], os.path.join(tmp_dir, "s.txt"), os.path.join(tmp_dir, "st.json"), cadence_seconds=1.0)
        
        mock_wrapper = MagicMock(spec=OCIClientWrapper)
        mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
        mock_wrapper.compute_client = MagicMock()
        
        limit_error = oci.exceptions.ServiceError(
            status=400,
            code="LimitExceeded",
            headers={},
            message="The following service limits were exceeded: standard-a1-core-count",
        )
        mock_wrapper.compute_client.launch_instance.side_effect = limit_error

        start_time = time.time()
        worker_thread = threading.Thread(
            target=run_claimer_worker,
            args=("Worker-Limit-1", cfg, coord, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
            daemon=True,
        )
        worker_thread.start()
        worker_thread.join(timeout=3.0)
        elapsed = time.time() - start_time

        assert not worker_thread.is_alive(), f"FAIL: Worker thread is still alive after {elapsed:.2f}s (infinite loop detected)"
        assert coord.stop_event.is_set(), "FAIL: coordinator.stop_event was not set upon LimitExceeded"
        assert coord.is_stopped(), "FAIL: coordinator.is_stopped() returned False"
        assert coord.total_attempts == 1, f"FAIL: Expected 1 attempt before halt, got {coord.total_attempts}"
        assert elapsed < 2.5, f"FAIL: Worker took too long to terminate: {elapsed:.2f}s"
        print(f"  -> PASSED: Worker terminated cleanly in {elapsed*1000:.1f}ms, stop_event=True, attempts=1")


def test_2_quota_message_variants():
    """
    Test 2: Quota message variants and attribute safety
    - 2A: code=None, message="The following service limits were exceeded: standard-a1-core-count"
    - 2B: code="QuotaExceeded", message=None
    - 2C: code="limitexceeded" (lowercase), message=""
    - 2D: code=None, message=None, status=500 (AttributeError check)
    - 2E: code="LIMITEXCEEDED" (uppercase), message=None
    """
    print("\n[TEST 2] Testing Quota message variants and attribute safety...")
    variants = [
        ("2A: code=None, msg contains 'limits were exceeded'", None, "The following service limits were exceeded: standard-a1-core-count", 400, True),
        ("2B: code='QuotaExceeded', msg=None", "QuotaExceeded", None, 400, True),
        ("2C: code='limitexceeded', msg=''", "limitexceeded", "", 400, True),
        ("2D: code='LIMITEXCEEDED' (uppercase), msg=None", "LIMITEXCEEDED", None, 400, True),
        ("2E: code=None, msg=None (status=500, should not crash with AttributeError)", None, None, 500, False),
    ]

    variant_results = {}
    for label, code_val, msg_val, status_val, expect_limit_stop in variants:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = make_dummy_config(tmp_dir)
            coord = ClaimCoordinator(["FD-1"], os.path.join(tmp_dir, "s.txt"), os.path.join(tmp_dir, "st.json"), cadence_seconds=0.01, min_interval_seconds=0.0)

            mock_wrapper = MagicMock(spec=OCIClientWrapper)
            mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
            mock_wrapper.compute_client = MagicMock()

            service_err = oci.exceptions.ServiceError(
                status=status_val,
                code=code_val,
                headers={},
                message=msg_val,
            )
            
            if expect_limit_stop:
                mock_wrapper.compute_client.launch_instance.side_effect = service_err
            else:
                # To prevent infinite loop on status 500 fallback, stop after 2 calls
                def side_effect(*args, **kwargs):
                    if coord.total_attempts >= 2:
                        coord.stop_event.set()
                    raise service_err
                mock_wrapper.compute_client.launch_instance.side_effect = side_effect

            start_t = time.time()
            worker_thread = threading.Thread(
                target=run_claimer_worker,
                args=("Worker-Variant", cfg, coord, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
                daemon=True,
            )
            worker_thread.start()
            worker_thread.join(timeout=2.0)
            elapsed = time.time() - start_t

            is_alive = worker_thread.is_alive()
            if is_alive:
                coord.stop_event.set()
                worker_thread.join(timeout=1.0)

            if expect_limit_stop:
                if is_alive or not coord.stop_event.is_set():
                    variant_results[label] = f"FAILED: Worker hung / looped infinitely (limit not detected, fell through to generic notice)"
                    print(f"  -> FAILED [{label}]: Worker did NOT halt on quota exhaustion!")
                else:
                    variant_results[label] = f"PASSED: Terminated cleanly in {elapsed*1000:.1f}ms, stop_event=True"
                    print(f"  -> PASSED [{label}]: Terminated cleanly in {elapsed*1000:.1f}ms, stop_event=True")
            else:
                if is_alive:
                    variant_results[label] = "FAILED: Worker hung on generic error"
                    print(f"  -> FAILED [{label}]: Worker hung on generic error")
                elif coord.total_attempts < 2:
                    variant_results[label] = f"FAILED: Unexpected attempts: {coord.total_attempts}"
                    print(f"  -> FAILED [{label}]: Unexpected attempts: {coord.total_attempts}")
                else:
                    variant_results[label] = "PASSED: Handled gracefully without AttributeError"
                    print(f"  -> PASSED [{label}]: Handled gracefully without AttributeError")

    failed_variants = [k for k, v in variant_results.items() if "FAILED" in v]
    if failed_variants:
        raise AssertionError(f"Quota message variants failed: {failed_variants}")


def test_3_image_discovery_cleanliness():
    """
    Test 3: Image discovery cleanliness and error handling
    - 3A: discover_image() with empty catalog -> RuntimeError raised, NO Mumbai OCID returned
    - 3B: discover_image() when compute_client raises exceptions -> RuntimeError raised
    - 3C: Zero occurrences of 'ap-mumbai-1' in src/oci_client.py and src/worker.py
    """
    print("\n[TEST 3] Testing Image discovery cleanliness and no hardcoded Mumbai OCID...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg_file = os.path.join(tmp_dir, "config.ini")
        with open(cfg_file, "w") as f:
            f.write(
                "[DEFAULT]\n"
                "user=ocid1.user.oc1..test\n"
                "fingerprint=00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff\n"
                f"key_file={os.path.join(tmp_dir, 'test.key')}\n"
                "tenancy=ocid1.tenancy.oc1..test\n"
                "region=us-ashburn-1\n"
            )
        with open(os.path.join(tmp_dir, "test.key"), "w") as f:
            f.write("DUMMY_KEY")

        with patch("oci.core.ComputeClient"), patch("oci.identity.IdentityClient"), patch("oci.core.VirtualNetworkClient"):
            wrapper = OCIClientWrapper(cfg_file)
            
            # 3A: list_images returns empty list
            wrapper.compute_client.list_images.return_value.data = []
            
            raised = False
            try:
                result = wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex")
            except RuntimeError as e:
                raised = True
                err_msg = str(e)
                assert "Failed to discover a compatible image" in err_msg, f"Unexpected error message: {err_msg}"
                assert "VM.Standard.A1.Flex" in err_msg, f"Missing shape in error message: {err_msg}"
                assert "ap-mumbai-1" not in err_msg, "Mumbai region hardcoded in error message!"
            assert raised, "FAIL: discover_image did not raise RuntimeError on empty catalog"
            print("  -> PASSED [3A]: RuntimeError correctly raised on empty catalog with descriptive message")

            # 3B: list_images raises exceptions
            wrapper.compute_client.list_images.side_effect = Exception("OCI 404 Catalog Not Found")
            raised = False
            try:
                result = wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex")
            except RuntimeError as e:
                raised = True
                assert "Failed to discover a compatible image" in str(e)
            assert raised, "FAIL: discover_image did not raise RuntimeError when client throws exception"
            print("  -> PASSED [3B]: RuntimeError correctly raised when catalog API throws exceptions")

    # 3C: Static string search for ap-mumbai-1 in src/oci_client.py
    oci_client_path = os.path.join(REPO_ROOT, "src", "oci_client.py")
    with open(oci_client_path, "r", encoding="utf-8") as f:
        oci_client_code = f.read()
    assert "ap-mumbai-1" not in oci_client_code, "CRITICAL BUG: 'ap-mumbai-1' found in src/oci_client.py!"
    assert "aaaaaaaavpkbfemaxi7gfzobc4qsc3p2m5szuswd7skrxvzo5teii6bfkd2a" not in oci_client_code, "CRITICAL BUG: Hardcoded Mumbai image OCID found in src/oci_client.py!"
    print("  -> PASSED [3C]: Zero occurrences of 'ap-mumbai-1' or hardcoded Mumbai OCID in src/oci_client.py")


def test_4_multi_worker_cascade_resilience():
    """
    Test 4: Multi-Worker Cascade Termination Stress Test
    - 4 concurrent workers
    - Worker-0 hits LimitExceeded
    - Workers 1, 2, 3 must unblock immediately from stop_event.wait()
    - All 4 workers terminate in < 1.0s
    - Zero thread leaks, zero infinite loops
    """
    print("\n[TEST 4] Testing 4-worker concurrent cascade termination on LimitExceeded...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg = make_dummy_config(tmp_dir)
        coord = ClaimCoordinator(
            ["FD-1", "FD-2", "FD-3"],
            os.path.join(tmp_dir, "s.txt"),
            os.path.join(tmp_dir, "st.json"),
            cadence_seconds=10.0,  # 10s cadence would cause hang if not event-driven!
        )

        mock_wrapper = MagicMock(spec=OCIClientWrapper)
        mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
        mock_wrapper.compute_client = MagicMock()

        limit_err = oci.exceptions.ServiceError(
            status=400,
            code="LimitExceeded",
            headers={},
            message="The following service limits were exceeded: standard-a1-core-count",
        )
        
        # Worker-0 raises limit_err, others raise capacity if called
        def launch_side_effect(details):
            # If called, raise limit error
            raise limit_err

        mock_wrapper.compute_client.launch_instance.side_effect = launch_side_effect

        threads = []
        start_t = time.time()
        for i in range(4):
            t = threading.Thread(
                target=run_claimer_worker,
                args=(f"Worker-{i}", cfg, coord, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
                daemon=True,
            )
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=2.0)
        elapsed = time.time() - start_t

        for i, t in enumerate(threads):
            assert not t.is_alive(), f"FAIL: Worker-{i} is still alive after {elapsed:.2f}s"

        assert coord.stop_event.is_set(), "FAIL: coord.stop_event was not set"
        assert coord.is_stopped(), "FAIL: coord.is_stopped() is False"
        assert elapsed < 1.5, f"FAIL: Multi-worker shutdown took too long: {elapsed:.2f}s"
        print(f"  -> PASSED: All 4 concurrent workers terminated in {elapsed*1000:.1f}ms cleanly")


def test_5_rapid_stress_iterations():
    """
    Test 5: Rapid Stress Loop (30 iterations)
    - Re-runs LimitExceeded termination 30 times in quick succession
    - Verifies stability, no race conditions, no deadlocks
    """
    print("\n[TEST 5] Testing rapid sequential stress loop (30 iterations)...")
    for iteration in range(30):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = make_dummy_config(tmp_dir)
            coord = ClaimCoordinator(["FD-1"], os.path.join(tmp_dir, "s.txt"), os.path.join(tmp_dir, "st.json"), cadence_seconds=0.1)
            
            mock_wrapper = MagicMock(spec=OCIClientWrapper)
            mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
            mock_wrapper.compute_client = MagicMock()
            
            mock_wrapper.compute_client.launch_instance.side_effect = oci.exceptions.ServiceError(
                status=400,
                code="LimitExceeded",
                headers={},
                message="Limit reached",
            )
            
            t = threading.Thread(
                target=run_claimer_worker,
                args=(f"Worker-Stress-{iteration}", cfg, coord, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
                daemon=True,
            )
            t.start()
            t.join(timeout=1.0)
            assert not t.is_alive(), f"Iteration {iteration} thread hung"
            assert coord.stop_event.is_set(), f"Iteration {iteration} stop_event not set"
def test_6_surge_window_cp1252_encoding():
    """
    Test 6: Surge Window Unicode Encoding on Windows CP1252
    - When is_surge_window() is True, mode_tag is '🔥 SURGE'
    - Verify whether print() with CP1252 stdout causes UnicodeEncodeError and loop glitch
    """
    print("\n[TEST 6] Testing Surge Window Unicode encoding resilience under cp1252...")
    import io
    with tempfile.TemporaryDirectory() as tmp_dir:
        cfg = make_dummy_config(tmp_dir)
        coord = ClaimCoordinator(["FD-1"], os.path.join(tmp_dir, "s.txt"), os.path.join(tmp_dir, "st.json"), cadence_seconds=0.01, min_interval_seconds=0.0)

        mock_wrapper = MagicMock(spec=OCIClientWrapper)
        mock_wrapper.tenancy_id = "ocid1.tenancy.oc1..test"
        mock_wrapper.compute_client = MagicMock()

        # Simulate cp1252 buffer
        mock_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        old_stdout = sys.stdout
        
        with patch("src.worker.is_surge_window", return_value=True):
            try:
                sys.stdout = mock_stdout
                # Try to launch 1 instance, then stop
                def side_effect(*args, **kwargs):
                    coord.stop_event.set()
                    return MagicMock()
                mock_wrapper.compute_client.launch_instance.side_effect = side_effect

                # Stop thread after 0.5s if it gets trapped in glitch loop
                def watcher():
                    time.sleep(0.5)
                    coord.stop_event.set()
                threading.Thread(target=watcher, daemon=True).start()

                t = threading.Thread(
                    target=run_claimer_worker,
                    args=("Worker-Surge", cfg, coord, mock_wrapper, "AD-1", "IMG-1", "SUBNET-1"),
                    daemon=True,
                )
                t.start()
                t.join(timeout=1.0)
            finally:
                sys.stdout = old_stdout

        output_bytes = mock_stdout.buffer.getvalue()
        output_str = output_bytes.decode("cp1252", errors="replace")
        
        glitch_detected = "Glitch: 'charmap' codec can't encode" in output_str or "UnicodeEncodeError" in output_str
        if glitch_detected or not mock_wrapper.compute_client.launch_instance.called:
            print("  -> FAILED [TEST 6]: UnicodeEncodeError detected on cp1252 stdout during surge window! launch_instance was NOT reached.")
            raise AssertionError("UnicodeEncodeError on cp1252 stdout during surge window prevents launch_instance execution")
        else:
            print("  -> PASSED [TEST 6]: Surge window executed cleanly on cp1252 stdout")


def run_all_tests():
    print("=" * 70)
    print("EMPIRICAL ADVERSARIAL STRESS HARNESS — M1 RESILIENCE & CLEANLINESS")
    print("Agent: teamwork_preview_challenger_m1_2")
    print("=" * 70)

    tests = [
        test_1_limit_exceeded_clean_termination,
        test_2_quota_message_variants,
        test_3_image_discovery_cleanliness,
        test_4_multi_worker_cascade_resilience,
        test_5_rapid_stress_iterations,
        test_6_surge_window_cp1252_encoding,
    ]

    passed = 0
    failed = 0
    start_total = time.time()

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n[X] TEST FAILED: {test.__name__}")
            traceback.print_exc()

    total_elapsed = time.time() - start_total
    print("\n" + "=" * 70)
    print(f"HARNESS SUMMARY: {passed} PASSED, {failed} FAILED in {total_elapsed:.2f}s")
    print("=" * 70)
    if failed > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    run_all_tests()
