import os
from typing import List, Optional
import oci


class OCIClientWrapper:
    """Manages authenticated OCI API clients with connection pooling and universal resource discovery."""

    def __init__(self, config_file: str, profile: str = "DEFAULT", key_file: Optional[str] = None):
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"OCI config file not found at: {config_file}")

        self.config = oci.config.from_file(config_file, profile)
        if key_file:
            self.config["key_file"] = key_file

        self.tenancy_id = self.config["tenancy"]

        # Instantiate persistent API clients
        self.compute_client = oci.core.ComputeClient(self.config)
        self.identity_client = oci.identity.IdentityClient(self.config)
        self.network_client = oci.core.VirtualNetworkClient(self.config)

    def get_availability_domain(self) -> str:
        ads = self.identity_client.list_availability_domains(self.tenancy_id).data
        if not ads:
            raise RuntimeError(f"No availability domains returned for tenancy {self.tenancy_id}")
        return ads[0].name

    def get_fault_domains(self, ad_name: str) -> List[str]:
        try:
            fds = self.identity_client.list_fault_domains(
                self.tenancy_id, availability_domain=ad_name
            ).data
            return [fd.name for fd in fds]
        except Exception:
            return ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2", "FAULT-DOMAIN-3"]

    def discover_image(self, os_name: str = "Canonical Ubuntu", shape: str = "VM.Standard.A1.Flex", os_version: Optional[str] = None) -> str:
        """Dynamically queries OCI image catalog matching the requested OS, version, and architecture/shape."""
        try:
            list_kwargs = {
                "compartment_id": self.tenancy_id,
                "shape": shape,
                "sort_by": "TIMECREATED",
                "sort_order": "DESC",
            }
            if os_name:
                list_kwargs["operating_system"] = os_name
            if os_version:
                list_kwargs["operating_system_version"] = os_version

            images = self.compute_client.list_images(**list_kwargs).data
            if images:
                return images[0].id
        except Exception:
            pass

        # Fallback: List all compatible images for shape
        try:
            images = self.compute_client.list_images(
                self.tenancy_id, shape=shape, sort_by="TIMECREATED", sort_order="DESC"
            ).data
            if images:
                return images[0].id
        except Exception:
            pass

        # Ultimate fallback (Default Mumbai Ubuntu 24.04 ARM)
        return "ocid1.image.oc1.ap-mumbai-1.aaaaaaaavpkbfemaxi7gfzobc4qsc3p2m5szuswd7skrxvzo5teii6bfkd2a"

    def discover_public_subnet(self) -> str:
        vcns = self.network_client.list_vcns(self.tenancy_id).data
        for vcn in vcns:
            subnets = self.network_client.list_subnets(self.tenancy_id, vcn_id=vcn.id).data
            for s in subnets:
                if "public" in s.display_name.lower():
                    return s.id
            if subnets:
                return subnets[0].id

        raise RuntimeError("No suitable public subnet found in tenancy.")
