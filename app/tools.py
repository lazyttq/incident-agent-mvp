from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, Any]


SCENARIOS: dict[str, dict[str, Any]] = {
    "database_pool": {
        "keywords": ("数据库", "连接池", "connection pool", "database", "db"),
        "priority": "P1",
        "logs": {
            "entries": [
                "ERROR checkout-api db.pool - acquire timeout after 3000ms",
                "ERROR checkout-api postgres - remaining connection slots are reserved",
            ],
            "error_count": 184,
        },
        "metrics": {"error_rate": 0.42, "p95_latency_ms": 2140, "db_pool_usage": 1.0},
        "runbook": {
            "id": "RB-DB-001",
            "title": "数据库连接池耗尽",
            "steps": ["检查连接泄漏", "临时重启 checkout-api 实例", "观察连接池恢复"],
        },
        "diagnosis": "checkout-api 的数据库连接池已耗尽，导致请求在获取连接时超时。",
        "action": "restart_service",
        "action_description": "重启 checkout-api 的模拟实例以释放泄漏连接。",
    },
    "upstream_timeout": {
        "keywords": ("超时", "延迟", "timeout", "latency", "慢"),
        "priority": "P2",
        "logs": {
            "entries": [
                "WARN checkout-api payment-client - upstream request timeout",
                "WARN checkout-api gateway - retry budget exhausted",
            ],
            "error_count": 67,
        },
        "metrics": {"error_rate": 0.18, "p95_latency_ms": 4860, "upstream_timeout_rate": 0.31},
        "runbook": {
            "id": "RB-NET-002",
            "title": "上游依赖超时",
            "steps": ["检查上游状态", "降低重试并观察", "必要时切换降级开关"],
        },
        "diagnosis": "payment-client 的上游依赖响应变慢并触发超时，重试预算已经耗尽。",
        "action": "enable_degraded_mode",
        "action_description": "开启 checkout-api 的模拟支付降级开关。",
    },
    "release_regression": {
        "keywords": ("发布", "回滚", "版本", "release", "deploy", "回归"),
        "priority": "P1",
        "logs": {
            "entries": [
                "ERROR checkout-api order-validator - schema validation failed",
                "INFO deploy - version 2026.10.23.4 became active 8 minutes ago",
            ],
            "error_count": 231,
        },
        "metrics": {"error_rate": 0.53, "p95_latency_ms": 1730, "invalid_order_rate": 0.46},
        "runbook": {
            "id": "RB-REL-003",
            "title": "发布后错误率回归",
            "steps": ["确认错误是否随新版本出现", "回滚到上一稳定版本", "观察错误率"],
        },
        "diagnosis": "新版本 checkout-api 的订单校验逻辑出现回归，错误率与版本发布时间高度相关。",
        "action": "rollback_release",
        "action_description": "将 checkout-api 的模拟版本回滚到上一稳定版本。",
    },
}


def detect_scenario(text: str) -> str:
    lowered = text.lower()
    for name, scenario in SCENARIOS.items():
        if any(keyword.lower() in lowered for keyword in scenario["keywords"]):
            return name
    return "upstream_timeout"


class SimulatedOpsTools:
    """Safe local tools. They only read fixture data or mutate in-memory simulation state."""

    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = metadata or {}
        self.failed_once: set[str] = set()
        self.recovered = False

    def _maybe_fail(self, tool_name: str) -> None:
        fail_tools = self.metadata.get("fail_tools", [])
        if tool_name in fail_tools and tool_name not in self.failed_once:
            self.failed_once.add(tool_name)
            raise ToolError(f"simulated transient failure from {tool_name}")

    def query_logs(self, scenario: str, service: str) -> ToolOutput:
        self._maybe_fail("query_logs")
        data = SCENARIOS[scenario]["logs"]
        return ToolOutput({"service": service, **data})

    def query_metrics(self, scenario: str, service: str) -> ToolOutput:
        self._maybe_fail("query_metrics")
        return ToolOutput({"service": service, **SCENARIOS[scenario]["metrics"]})

    def search_runbook(self, scenario: str, query: str) -> ToolOutput:
        self._maybe_fail("search_runbook")
        return ToolOutput({"query": query, **SCENARIOS[scenario]["runbook"]})

    def execute_remediation(self, action: str, service: str, idempotency_key: str) -> ToolOutput:
        self._maybe_fail("execute_remediation")
        if not idempotency_key:
            raise ToolError("idempotency key is required")
        self.recovered = True
        return ToolOutput(
            {
                "action": action,
                "service": service,
                "idempotency_key": idempotency_key,
                "accepted": True,
                "simulated": True,
            }
        )

    def verify_recovery(self, scenario: str, service: str) -> ToolOutput:
        self._maybe_fail("verify_recovery")
        return ToolOutput(
            {
                "service": service,
                "healthy": self.recovered,
                "error_rate": 0.01 if self.recovered else SCENARIOS[scenario]["metrics"]["error_rate"],
            }
        )

