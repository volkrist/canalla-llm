from ..contracts import ToolCredentialProvider, ToolError


class LocalCredentialProvider(ToolCredentialProvider):
    """Secrets stay on the paired host. The backend never receives a raw secret."""

    def configured(self, provider: str) -> bool:
        return provider == "local"

    def resolve(self, provider: str) -> str:
        raise ToolError("credentials_stay_on_host")
