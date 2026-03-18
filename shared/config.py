import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """
    Central config loaded from environment variables.
    On Azure, these are injected via Key Vault references in Function App settings.
    Locally, load from a .env file using python-dotenv.
    """

    # Azure identity
    tenant_id: str
    client_id: str | None        # Only needed for local dev (service principal)
    client_secret: str | None    # Only needed for local dev (service principal)

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

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            tenant_id=_require("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),           # None on Azure (managed identity)
            client_secret=os.getenv("AZURE_CLIENT_SECRET"),   # None on Azure (managed identity)
            graph_base_url=os.getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0"),
            cosmos_endpoint=_require("COSMOS_DB_ENDPOINT"),
            cosmos_key=_require("COSMOS_DB_KEY"),
            cosmos_database=os.getenv("COSMOS_DATABASE", "iam-db"),
            openai_endpoint=_require("OPENAI_ENDPOINT"),
            openai_api_key=_require("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o"),
            servicebus_conn=_require("SERVICE_BUS_CONN"),
            environment=os.getenv("ENVIRONMENT", "dev"),
            bot_type=os.getenv("BOT_TYPE", "unknown"),
        )


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(f"Required environment variable '{key}' is not set.")
    return value


# Singleton — imported by all modules
config = Config.from_env()