from .contracts import CredentialReference, ToolCredentialProvider, ToolError


class WindowsCredentialStore:
    """Protocol marker: the paired host is the only place a secret is loaded."""

    backend = "windows_credential_manager"


class CredentialBroker:
    def __init__(self, provider: ToolCredentialProvider | None = None):
        from .local.credentials import LocalCredentialProvider

        self.provider = provider or LocalCredentialProvider()
        self.store = WindowsCredentialStore()

    def reference(self, name: str) -> CredentialReference:
        value = (name or "").strip()
        if not value or len(value) > 80:
            raise ToolError("invalid_reference")
        return CredentialReference(provider="local", reference_id=value)

    def configured(self) -> bool:
        return self.provider.configured("local")

    def reveal_for_model(self, reference: CredentialReference) -> str:
        raise ToolError("credentials_stay_on_host")

    def checkout(self, reference: CredentialReference) -> str:
        # Backend never holds the secret. The host checks out at the execution boundary.
        raise ToolError("credentials_stay_on_host")
