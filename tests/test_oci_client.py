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
    img_id = wrapper.discover_image("Canonical Ubuntu", "VM.Standard.A1.Flex")
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
