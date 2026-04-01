import os
from dataclasses import dataclass
from pathlib import Path

# Auto-load .env when running locally.
# On Azure, env vars are injected by the Function App -- dotenv is a no-op.
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=_env_path, override=False)
except ImportError:
    pass  # python-dotenv not installed in production -- that's fine


@dataclass(frozen=True)
class Config:
    """
    Central config loaded from environment variables.
    On Azure, these are injected via Key Vault references in Function App settings.
    Locally, loaded from a .env file via python-dotenv above.
    """

    # Azure identity
    tenant_id: str
    client_id: str | None       # Set locally (service principal), None on Azure (managed identity)
    client_secret: str | None   # Set locally (service principal), None on Azure (managed identity)

    # Microsoft Graph
    graph_base_url: str

    # Cosmos DB
    cosmos_endpoint: str
    cosmos_key: str
    cosmos_database: str

    # Azure OpenAI
    openai_endpoint: str
    openai_api_key: str
    openai_model: str

    # Service Bus
    servicebus_conn: str

    # Runtime
    environment: str
    bot_type: str

    # Local dev only — bypasses approval gate for testing
    # NEVER set this to true in production
    skip_approval: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            tenant_id=_require("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),
            graph_base_url=os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"),
            cosmos_endpoint=os.getenv("COSMOS_DB_ENDPOINT", ""),
            cosmos_key=os.getenv("COSMOS_DB_KEY", ""),
            cosmos_database=os.getenv("COSMOS_DATABASE", "iam-db"),
            openai_endpoint=_require("OPENAI_ENDPOINT"),
            openai_api_key=_require("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            servicebus_conn=os.getenv("SERVICE_BUS_CONN", ""),
            environment=os.getenv("ENVIRONMENT", "dev"),
            bot_type=os.getenv("BOT_TYPE", "unknown"),
            skip_approval=os.getenv("SKIP_APPROVAL", "false").lower() == "true",
        )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Add it to your .env file for local dev."
        )
    return value


# Singleton -- imported by all modules
config = Config.from_env()