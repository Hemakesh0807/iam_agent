import os
import pytest

# Set required env vars before any module imports config.py
os.environ.setdefault("AZURE_TENANT_ID", "test-tenant-id")
os.environ.setdefault("AZURE_CLIENT_ID", "test-client-id")
os.environ.setdefault("AZURE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("OPENAI_ENDPOINT", "https://api.openai.com/v1")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("BOT_TYPE", "user_bot")
# Leave COSMOS_DB_ENDPOINT unset -> audit logger uses local file fallback