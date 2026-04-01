import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ai_engine._openai_client import build_openai_client
from shared.config import config
from shared.exceptions import IntentClassificationError
from shared.models import FlowName

logger = logging.getLogger(__name__)

_INTENT_MAP = {
    "user_onboarding":  FlowName.USER_ONBOARDING,
    "user_offboarding": FlowName.USER_OFFBOARDING,
    "risk_isolation":   FlowName.RISK_ISOLATION,
    "app_onboarding":   FlowName.APP_ONBOARDING,
    "app_offboarding":  FlowName.APP_OFFBOARDING,
}

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja = Environment(loader=FileSystemLoader(_PROMPTS_DIR), autoescape=False)


class IntentClassifier:
    """
    Uses GPT-4o to classify an incoming IAM request into one of the 5 flow types.

    Works with Azure AI Foundry, Azure OpenAI, and standard OpenAI.
    Client is selected automatically based on OPENAI_ENDPOINT in .env.
    """

    CONFIDENCE_THRESHOLD = 0.6

    def __init__(self):
        self._client  = build_openai_client()
        self._model   = config.openai_model
        self._template = _jinja.get_template("intent_prompt.jinja2")

    async def classify(self, request_text: str) -> dict:
        """
        Classify a free-text IAM request.

        Returns:
            dict with keys: intent (FlowName), confidence (float), reasoning (str)

        Raises:
            IntentClassificationError: if classification fails or confidence is too low.
        """
        prompt = self._template.render(request_text=request_text)
        logger.info("Classifying intent for: %.80s...", request_text)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=256,
                response_format={"type": "json_object"},
            )
            raw    = response.choices[0].message.content
            result = json.loads(raw)

        except Exception as exc:
            raise IntentClassificationError(
                f"LLM call failed during intent classification: {exc}"
            ) from exc

        return self._validate(result, request_text)

    def _validate(self, result: dict, original_request: str) -> dict:
        intent_str = result.get("intent", "unknown")
        confidence = float(result.get("confidence", 0.0))
        reasoning  = result.get("reasoning", "")

        if intent_str == "unknown":
            raise IntentClassificationError(
                f"Could not classify request as a known IAM flow: '{original_request}'"
            )

        if confidence < self.CONFIDENCE_THRESHOLD:
            raise IntentClassificationError(
                f"Classification confidence too low ({confidence:.2f}) for intent "
                f"'{intent_str}'. Request may be ambiguous: '{original_request}'"
            )

        if intent_str not in _INTENT_MAP:
            raise IntentClassificationError(
                f"LLM returned unknown intent type: '{intent_str}'"
            )

        flow = _INTENT_MAP[intent_str]
        logger.info(
            "Intent classified: %s (confidence=%.2f) — %s",
            flow.value, confidence, reasoning,
        )
        return {"intent": flow, "confidence": confidence, "reasoning": reasoning}