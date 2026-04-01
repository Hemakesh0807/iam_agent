import logging
from openai import AsyncAzureOpenAI, AsyncOpenAI
from shared.config import config

logger = logging.getLogger(__name__)

# Azure endpoint patterns — covers all Azure hosting formats
_AZURE_ENDPOINT_PATTERNS = (
    "openai.azure.com",            # Azure OpenAI classic
    "services.ai.azure.com",       # Azure AI Foundry new format
    "cognitiveservices.azure.com", # Cognitive Services
)

_AZURE_API_VERSION = "2024-08-01-preview"


def build_openai_client():
    """
    Returns the correct async OpenAI client based on OPENAI_ENDPOINT.

    Handles all Azure endpoint formats:
      https://xxx.openai.azure.com/           -> Azure OpenAI classic
      https://xxx.services.ai.azure.com/      -> Azure AI Foundry
      https://xxx.cognitiveservices.azure.com/ -> Cognitive Services

    Falls back to standard OpenAI for everything else.
    """
    endpoint = config.openai_endpoint.lower()
    is_azure = any(pattern in endpoint for pattern in _AZURE_ENDPOINT_PATTERNS)

    if is_azure:
        logger.info("OpenAI client: Azure (endpoint=%s)", config.openai_endpoint)
        return AsyncAzureOpenAI(
            api_key=config.openai_api_key,
            azure_endpoint=config.openai_endpoint,
            api_version=_AZURE_API_VERSION,
        )

    logger.info("OpenAI client: standard OpenAI API")
    return AsyncOpenAI(api_key=config.openai_api_key)