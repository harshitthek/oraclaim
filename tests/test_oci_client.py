import os
from unittest.mock import MagicMock, patch
import pytest
from src.oci_client import OCIClientWrapper


@pytest.fixture
def mock_oci_config(tmp_path):
    dummy_key = tmp_path / "test.key"
    dummy_key.write_text("DUMMY_KEY_CONTENT")

    cfg_file = tmp_path / "config.ini"
    cfg_file.write_text(
        "[DEFAULT]\n"
        "user=ocid1.user.oc1..test\n"
        "fingerprint=00:11:22:33:44:55:66:77:88:99:aa:bb:cc:dd:ee:ff\n"
        f"key_file={str(dummy_key).replace(chr(92), '/')}\n"
        "tenancy=ocid1.tenancy.oc1..testtenancy\n"
        "region=ap-mumbai-1\n"
    )
    return str(cfg_file)


def test_oci_client_init_missing_file():
    with pytest.raises(FileNotFoundError):
        OCIClientWrapper("non_existent_file.ini")


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_oci_client_init_key_file_override(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    wrapper = OCIClientWrapper(mock_oci_config, key_file="override.key")
    assert wrapper.config["key_file"] == "override.key"


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_oci_client_discovery(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    wrapper = OCIClientWrapper(mock_oci_config)
    assert wrapper.tenancy_id == "ocid1.tenancy.oc1..testtenancy"

    # Mock Availability Domain
    mock_ad = MagicMock()
    mock_ad.name = "gQcu:AP-MUMBAI-1-AD-1"
    wrapper.identity_client.list_availability_domains.return_value.data = [mock_ad]
    assert wrapper.get_availability_domain() == "gQcu:AP-MUMBAI-1-AD-1"

    # Mock Fault Domains
    mock_fd1 = MagicMock()
    mock_fd1.name = "FAULT-DOMAIN-1"
    mock_fd2 = MagicMock()
    mock_fd2.name = "FAULT-DOMAIN-2"
    wrapper.identity_client.list_fault_domains.return_value.data = [mock_fd1, mock_fd2]
    fds = wrapper.get_fault_domains("gQcu:AP-MUMBAI-1-AD-1")
    assert fds == ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"]

    # Mock Image Discovery
    mock_img = MagicMock()
    mock_img.id = "ocid1.image.oc1..discovered"
    wrapper.compute_client.list_images.return_value.data = [mock_img]
    img_id = wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex", os_version="24.04")
    assert img_id == "ocid1.image.oc1..discovered"

    # Mock Subnet Discovery
    mock_vcn = MagicMock()
    mock_vcn.id = "ocid1.vcn.oc1..test"
    wrapper.network_client.list_vcns.return_value.data = [mock_vcn]

    mock_subnet = MagicMock()
    mock_subnet.id = "ocid1.subnet.oc1..public"
    mock_subnet.display_name = "Public Subnet tree2"
    wrapper.network_client.list_subnets.return_value.data = [mock_subnet]

    subnet_id = wrapper.discover_public_subnet()
    assert subnet_id == "ocid1.subnet.oc1..public"


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_get_availability_domain_empty_raises(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    wrapper = OCIClientWrapper(mock_oci_config)
    wrapper.identity_client.list_availability_domains.return_value.data = []
    with pytest.raises(RuntimeError, match="No availability domains returned"):
        wrapper.get_availability_domain()


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_get_fault_domains_exception_fallback(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify fallback to default FD list when API raises exception."""
    wrapper = OCIClientWrapper(mock_oci_config)
    wrapper.identity_client.list_fault_domains.side_effect = RuntimeError("API Outage")
    fds = wrapper.get_fault_domains("AD-1")
    assert fds == ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3"]


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_discover_image_failure_raises_runtime_error(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify that when dynamic catalog discovery finds no image, discover_image raises RuntimeError

    and does NOT return a hardcoded Mumbai fallback OCID.
    """
    wrapper = OCIClientWrapper(mock_oci_config)
    wrapper.compute_client.list_images.return_value.data = []

    with pytest.raises(RuntimeError) as exc_info:
        wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex")

    err_msg = str(exc_info.value)
    assert "No suitable image found in dynamic catalog" in err_msg
    assert "VM.Standard.A1.Flex" in err_msg
    # Ensure zero hardcoded fallback OCIDs
    assert "aaaaaaaavpkbfemaxi7gfzobc4qsc3p2m5szuswd7skrxvzo5teii6bfkd2a" not in err_msg


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_discover_image_fallback_to_shape(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify that when specific OS filter fails, discover_image attempts shape-wide fallback."""
    wrapper = OCIClientWrapper(mock_oci_config)

    mock_shape_img = MagicMock()
    mock_shape_img.id = "ocid1.image.oc1..shape_fallback"

    # First call (specific OS) returns empty, second call (shape-wide) returns image
    wrapper.compute_client.list_images.side_effect = [
        MagicMock(data=[]),
        MagicMock(data=[mock_shape_img]),
    ]

    img_id = wrapper.discover_image("RareOS", "VM.Standard.A1.Flex")
    assert img_id == "ocid1.image.oc1..shape_fallback"
    assert wrapper.compute_client.list_images.call_count == 2


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_discover_public_subnet_fallback_to_first_subnet(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify that when no subnet has 'public' in its name, it falls back to the first subnet."""
    wrapper = OCIClientWrapper(mock_oci_config)

    mock_vcn = MagicMock()
    mock_vcn.id = "ocid1.vcn.oc1..test"
    wrapper.network_client.list_vcns.return_value.data = [mock_vcn]

    mock_subnet = MagicMock()
    mock_subnet.id = "ocid1.subnet.oc1..private1"
    mock_subnet.display_name = "Internal Private Subnet"
    wrapper.network_client.list_subnets.return_value.data = [mock_subnet]

    subnet_id = wrapper.discover_public_subnet()
    assert subnet_id == "ocid1.subnet.oc1..private1"


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_discover_public_subnet_failure_raises_runtime_error(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify subnet discovery failure raises RuntimeError when no subnets exist."""
    wrapper = OCIClientWrapper(mock_oci_config)

    # Empty VCNs or empty subnets
    wrapper.network_client.list_vcns.return_value.data = []
    with pytest.raises(RuntimeError, match="No suitable public subnet found in tenancy"):
        wrapper.discover_public_subnet()


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_discover_image_exception_swallowing_and_fallback(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify that when the first query throws an exception, it falls back to the shape query."""
    wrapper = OCIClientWrapper(mock_oci_config)
    mock_shape_img = MagicMock(id="ocid1.image.oc1..shape_img")
    wrapper.compute_client.list_images.side_effect = [
        RuntimeError("Transient API 500"),
        MagicMock(data=[mock_shape_img]),
    ]
    img_id = wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex")
    assert img_id == "ocid1.image.oc1..shape_img"


@patch("oci.core.ComputeClient")
@patch("oci.identity.IdentityClient")
@patch("oci.core.VirtualNetworkClient")
def test_discover_image_both_attempts_raise_exceptions(mock_vnc, mock_idc, mock_comp, mock_oci_config):
    """Verify that when both queries throw exceptions, discover_image raises RuntimeError."""
    wrapper = OCIClientWrapper(mock_oci_config)
    wrapper.compute_client.list_images.side_effect = [
        RuntimeError("First failure"),
        RuntimeError("Second failure"),
    ]
    with pytest.raises(RuntimeError, match="No suitable image found in dynamic catalog"):
        wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex")

