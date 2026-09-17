"""Azure backend for the executor seam. Platform control plane (credentials,
lifecycle, cost ledger) + the in-sandbox agent loop. See executor.py."""
from .executor import AzureExecutor

__all__ = ["AzureExecutor"]
