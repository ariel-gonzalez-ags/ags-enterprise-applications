"""Azure credential + management-client factory for the platform control plane.

Deliberately thin: this is the ONLY place the platform service principal is
turned into authenticated clients. Sandbox agents never see it; they get a
per-resource-group managed identity instead (see lifecycle.py). Centralizing
the client construction makes the module trivially mockable in tests and makes
the future BYOC swap (per-customer credentials) a change to one function.
"""
from azure.identity import ClientSecretCredential
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.containerinstance import ContainerInstanceManagementClient
from azure.mgmt.msi import ManagedServiceIdentityClient
# azure-mgmt-resource v26 moved the client under the .resources subpackage.
from azure.mgmt.resource.resources import ResourceManagementClient


class AzureUnavailable(Exception):
    """Raised when the Azure backend is selected but not configured."""


def platform_credential(settings) -> ClientSecretCredential:
    if not settings.azure_configured:
        raise AzureUnavailable(
            "EXECUTOR_BACKEND=azure but AZURE_* platform credentials are missing")
    return ClientSecretCredential(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
    )


def clients(settings):
    """Return the four management clients the lifecycle needs, all sharing one
    platform credential. Kept as a small namespace so tests can stub it."""
    cred = platform_credential(settings)
    sub = settings.azure_subscription_id
    return {
        "credential": cred,
        "resource": ResourceManagementClient(cred, sub),
        "msi": ManagedServiceIdentityClient(cred, sub),
        "auth": AuthorizationManagementClient(cred, sub),
        "aci": ContainerInstanceManagementClient(cred, sub),
    }
