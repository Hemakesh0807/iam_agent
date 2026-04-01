import json
import logging
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ai_engine._openai_client import build_openai_client
from shared.config import config
from shared.exceptions import RiskScoringError
from shared.models import RiskSeverity

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja = Environment(loader=FileSystemLoader(_PROMPTS_DIR), autoescape=False)


class RiskScorer:
    """
    Uses GPT-4o to assess the severity of a security alert from Sentinel / UEBA.

    Works with Azure AI Foundry, Azure OpenAI, and standard OpenAI.
    Client is selected automatically based on OPENAI_ENDPOINT in .env.
    """

    def __init__(self):
        self._client   = build_openai_client()
        self._model    = config.openai_model
        self._template = _jinja.get_template("risk_prompt.jinja2")

    async def score(
        self,
        user_principal_name: str,
        alert_reason: str,
        sentinel_incident_id: str | None = None,
        additional_context: str = "",
    ) -> dict:
        prompt = self._template.render(
            user_principal_name=user_principal_name,
            alert_reason=alert_reason,
            sentinel_incident_id=sentinel_incident_id or "N/A",
            additional_context=additional_context or "None provided",
        )

        logger.info(
            "Scoring risk for user=%s alert='%.80s...'",
            user_principal_name, alert_reason,
        )

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw    = response.choices[0].message.content
            result = json.loads(raw)

        except Exception as exc:
            raise RiskScoringError(
                f"LLM call failed during risk scoring: {exc}"
            ) from exc

        return self._validate(result)

    def _validate(self, result: dict) -> dict:
        severity_str = result.get("severity", "").lower()
        if severity_str not in ("high", "medium", "low"):
            raise RiskScoringError(
                f"LLM returned invalid severity: '{severity_str}'. "
                "Expected high/medium/low."
            )

        severity     = RiskSeverity(severity_str)
        auto_isolate = bool(result.get("auto_isolate", False))

        if severity == RiskSeverity.HIGH and not auto_isolate:
            logger.warning(
                "LLM set auto_isolate=False for HIGH severity. Overriding to True."
            )
            auto_isolate = True

        validated = {
            "severity":             severity,
            "confidence":           float(result.get("confidence", 0.0)),
            "auto_isolate":         auto_isolate,
            "reasoning":            result.get("reasoning", ""),
            "recommended_actions":  result.get("recommended_actions", []),
        }

        logger.info(
            "Risk scored: severity=%s auto_isolate=%s confidence=%.2f — %s",
            severity.value, auto_isolate,
            validated["confidence"], validated["reasoning"],
        )
        return validated