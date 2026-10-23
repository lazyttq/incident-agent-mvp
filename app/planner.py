from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import Request, urlopen

from .tools import SCENARIOS, detect_scenario


@dataclass(frozen=True)
class PlannerResult:
    scenario: str
    priority: str
    source: str


class RuleBasedPlanner:
    def classify(self, title: str, description: str) -> PlannerResult:
        scenario = detect_scenario(f"{title} {description}")
        return PlannerResult(scenario, SCENARIOS[scenario]["priority"], "rules")


class OpenAICompatiblePlanner:
    """Minimal model adapter using the common /chat/completions contract."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def classify(self, title: str, description: str) -> PlannerResult:
        allowed = ", ".join(SCENARIOS)
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify the incident into exactly one scenario. "
                        f"Allowed scenarios: {allowed}. Return JSON only: "
                        '{"scenario":"...","priority":"P1|P2|P3"}'
                    ),
                },
                {"role": "user", "content": f"title: {title}\ndescription: {description}"},
            ],
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured endpoint
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        result = json.loads(content)
        scenario = result.get("scenario")
        if scenario not in SCENARIOS:
            raise ValueError(f"model returned unsupported scenario: {scenario}")
        return PlannerResult(scenario, SCENARIOS[scenario]["priority"], "llm")


class HybridPlanner:
    """Use a configured model when available, otherwise keep the demo offline."""

    def __init__(self) -> None:
        self.fallback = RuleBasedPlanner()
        base_url = os.getenv("AGENT_LLM_BASE_URL")
        api_key = os.getenv("AGENT_LLM_API_KEY")
        model = os.getenv("AGENT_LLM_MODEL", "gpt-4o-mini")
        self.model_planner = (
            OpenAICompatiblePlanner(base_url, api_key, model)
            if base_url and api_key
            else None
        )

    def classify(self, title: str, description: str) -> PlannerResult:
        if self.model_planner:
            try:
                return self.model_planner.classify(title, description)
            except (OSError, URLError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                # The workflow remains available when an LLM provider is unavailable.
                pass
        return self.fallback.classify(title, description)

