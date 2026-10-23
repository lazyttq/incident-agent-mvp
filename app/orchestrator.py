from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from .models import (
    ApprovalDecision,
    ApprovalRequest,
    Event,
    IncidentCreate,
    IncidentState,
    IncidentStatus,
    PlanStep,
    ToolCall,
)
from .planner import HybridPlanner
from .store import SQLiteStore
from .tools import SimulatedOpsTools, ToolError, ToolOutput, SCENARIOS


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentOrchestrator:
    """Explicit, bounded state machine for the MVP.

    The planner is deterministic so the demo works without an API key. Its
    interface is intentionally isolated: a model-backed planner can replace
    `_classify_and_plan` in a later phase without changing the API or store.
    """

    MAX_TOOL_ATTEMPTS = 2

    def __init__(self, store: SQLiteStore, planner: HybridPlanner | None = None) -> None:
        self.store = store
        self.planner = planner or HybridPlanner()
        self._tools: dict[str, SimulatedOpsTools] = {}

    def _emit(self, incident_id: str, event_type: str, message: str, **data: Any) -> None:
        self.store.append_event(
            Event(incident_id=incident_id, type=event_type, message=message, data=data)
        )

    def _save_status(self, state: IncidentState, status: IncidentStatus, message: str) -> None:
        previous = state.status
        state.status = status
        self.store.save_incident(state)
        self._emit(
            state.id,
            "state_changed",
            message,
            from_status=previous.value,
            to_status=status.value,
        )

    def create(self, request: IncidentCreate) -> IncidentState:
        state = IncidentState(
            tenant_id=request.tenant_id,
            title=request.title,
            description=request.description,
            service=request.service,
            metadata=request.metadata,
        )
        self.store.save_incident(state)
        self._emit(state.id, "incident_created", "Incident created", trace_id=state.trace_id)
        self._run_to_approval(state)
        return self.store.get_incident(state.id) or state

    def approve(self, incident_id: str, decision: ApprovalDecision) -> IncidentState:
        state = self._require(incident_id)
        if state.status != IncidentStatus.WAITING_APPROVAL:
            raise ValueError(f"incident is not waiting for approval: {state.status.value}")
        state.approval_decision = decision
        self.store.save_incident(state)
        self._emit(
            incident_id,
            "approval_decided",
            "Approval decision received",
            approved=decision.approved,
            comment=decision.comment,
        )
        if not decision.approved:
            state.error = decision.comment or "remediation rejected by reviewer"
            self._save_status(state, IncidentStatus.CANCELLED, "Remediation rejected")
            state.final_summary = "诊断已完成，但修复动作被人工拒绝，系统未执行任何变更。"
            self.store.save_incident(state)
            return state

        self._execute_and_verify(state)
        return self.store.get_incident(incident_id) or state

    def cancel(self, incident_id: str, reason: str | None = None) -> IncidentState:
        state = self._require(incident_id)
        if state.status in {
            IncidentStatus.COMPLETED,
            IncidentStatus.CANCELLED,
            IncidentStatus.FAILED,
        }:
            return state
        state.error = reason or "cancelled by user"
        self._save_status(state, IncidentStatus.CANCELLED, "Incident cancelled")
        state.final_summary = "任务已取消，未执行修复动作。"
        self.store.save_incident(state)
        return state

    def _require(self, incident_id: str) -> IncidentState:
        state = self.store.get_incident(incident_id)
        if not state:
            raise KeyError(f"incident not found: {incident_id}")
        return state

    def _run_to_approval(self, state: IncidentState) -> None:
        try:
            self._save_status(state, IncidentStatus.PLANNING, "Planning investigation")
            self._classify_and_plan(state)
            self._save_status(state, IncidentStatus.INVESTIGATING, "Collecting evidence")
            self._investigate(state)
            self._diagnose(state)
            state.approval = ApprovalRequest(
                action=state.recommended_action or "unknown",
                reason=state.diagnosis or "diagnostic result requires review",
                risk="medium",
            )
            self.store.save_incident(state)
            self._emit(
                state.id,
                "approval_requested",
                "Waiting for human approval before remediation",
                action=state.approval.action,
                risk=state.approval.risk,
            )
            self._save_status(state, IncidentStatus.WAITING_APPROVAL, "Waiting for approval")
        except Exception as exc:  # noqa: BLE001 - boundary converts failures to durable state
            self._fail(state, str(exc))

    def _classify_and_plan(self, state: IncidentState) -> None:
        planner_result = self.planner.classify(state.title, state.description)
        state.scenario = planner_result.scenario
        state.priority = planner_result.priority
        scenario = SCENARIOS[state.scenario]
        state.plan = [
            PlanStep(id="classify", title="Classify incident", tool="rule_based_planner", status="DONE"),
            PlanStep(id="logs", title="Inspect service logs", tool="query_logs"),
            PlanStep(id="metrics", title="Inspect service metrics", tool="query_metrics"),
            PlanStep(id="runbook", title="Retrieve matching runbook", tool="search_runbook"),
            PlanStep(id="diagnose", title="Synthesize diagnosis", tool="diagnostic_synthesizer"),
            PlanStep(id="remediate", title="Apply approved remediation", tool="execute_remediation"),
            PlanStep(id="verify", title="Verify recovery", tool="verify_recovery"),
        ]
        self.store.save_incident(state)
        self._emit(
            state.id,
            "plan_created",
            "Investigation plan created",
            scenario=state.scenario,
            priority=state.priority,
            planner_source=planner_result.source,
            steps=[step.model_dump() for step in state.plan],
        )

    def _investigate(self, state: IncidentState) -> None:
        if not state.scenario:
            raise RuntimeError("scenario was not classified")
        tools = SimulatedOpsTools(state.metadata)
        self._tools[state.id] = tools
        calls: list[tuple[str, dict[str, Any], Callable[[], ToolOutput]]] = [
            (
                "query_logs",
                {"service": state.service, "scenario": state.scenario},
                lambda: tools.query_logs(state.scenario or "", state.service),
            ),
            (
                "query_metrics",
                {"service": state.service, "scenario": state.scenario},
                lambda: tools.query_metrics(state.scenario or "", state.service),
            ),
            (
                "search_runbook",
                {"query": state.description, "scenario": state.scenario},
                lambda: tools.search_runbook(state.scenario or "", state.description),
            ),
        ]
        for tool_name, arguments, operation in calls:
            result = self._call_tool(state, tool_name, arguments, operation)
            self._append_evidence(state, tool_name, result.data)
        for step_id in ("logs", "metrics", "runbook"):
            self._mark_step(state, step_id, "DONE")
        self.store.save_incident(state)

    def _diagnose(self, state: IncidentState) -> None:
        scenario = SCENARIOS[state.scenario or "upstream_timeout"]
        state.diagnosis = scenario["diagnosis"]
        state.recommended_action = scenario["action"]
        self._mark_step(state, "diagnose", "DONE")
        self.store.save_incident(state)
        self._emit(
            state.id,
            "diagnosis_ready",
            "Diagnosis synthesized from collected evidence",
            diagnosis=state.diagnosis,
            recommended_action=state.recommended_action,
        )

    def _execute_and_verify(self, state: IncidentState) -> None:
        try:
            self._save_status(state, IncidentStatus.EXECUTING, "Executing approved remediation")
            tools = self._tools.get(state.id) or SimulatedOpsTools(state.metadata)
            self._tools[state.id] = tools
            action = state.recommended_action or "unknown"
            self._call_tool(
                state,
                "execute_remediation",
                {"action": action, "service": state.service, "idempotency_key": state.run_id},
                lambda: tools.execute_remediation(action, state.service, state.run_id),
            )
            self._mark_step(state, "remediate", "DONE")
            self._save_status(state, IncidentStatus.VERIFYING, "Verifying service recovery")
            result = self._call_tool(
                state,
                "verify_recovery",
                {"service": state.service, "scenario": state.scenario},
                lambda: tools.verify_recovery(state.scenario or "", state.service),
            )
            if not result.data.get("healthy"):
                raise RuntimeError("verification failed: service is still unhealthy")
            self._mark_step(state, "verify", "DONE")
            state.final_summary = (
                f"{state.diagnosis} 已执行模拟修复 {action}，验证结果 healthy=true。"
            )
            self._save_status(state, IncidentStatus.COMPLETED, "Incident resolved")
            self.store.save_incident(state)
        except Exception as exc:  # noqa: BLE001 - durable workflow boundary
            self._fail(state, str(exc))

    def _call_tool(
        self,
        state: IncidentState,
        name: str,
        arguments: dict[str, Any],
        operation: Callable[[], ToolOutput],
    ) -> ToolOutput:
        tool_call = ToolCall(tool=name, arguments=arguments, status="RUNNING")
        state.tool_calls.append(tool_call)
        self.store.save_incident(state)
        self._emit(state.id, "tool_started", f"Calling {name}", tool=name, arguments=arguments)
        last_error: Exception | None = None
        for attempt in range(1, self.MAX_TOOL_ATTEMPTS + 1):
            tool_call.attempts = attempt
            try:
                result = operation()
                tool_call.status = "SUCCEEDED"
                tool_call.result = result.data
                tool_call.finished_at = utc_now()
                self.store.save_incident(state)
                self._emit(
                    state.id,
                    "tool_succeeded",
                    f"{name} succeeded",
                    tool=name,
                    attempts=attempt,
                )
                return result
            except ToolError as exc:
                last_error = exc
                self._emit(
                    state.id,
                    "tool_retry",
                    f"{name} failed; retrying",
                    tool=name,
                    attempt=attempt,
                    error=str(exc),
                )
        tool_call.status = "FAILED"
        tool_call.error = str(last_error or "tool failed")
        tool_call.finished_at = utc_now()
        self.store.save_incident(state)
        raise ToolError(f"{name} failed after {self.MAX_TOOL_ATTEMPTS} attempts: {tool_call.error}")

    def _append_evidence(self, state: IncidentState, source: str, data: dict[str, Any]) -> None:
        from .models import Evidence

        if source == "query_logs":
            summary = f"发现 {data.get('error_count', 0)} 条相关错误日志。"
        elif source == "query_metrics":
            summary = f"错误率 {data.get('error_rate')}，P95 延迟 {data.get('p95_latency_ms')}ms。"
        else:
            summary = f"匹配 Runbook {data.get('id')}：{data.get('title')}。"
        state.evidence.append(Evidence(source=source, summary=summary, details=data))

    def _mark_step(self, state: IncidentState, step_id: str, status: str) -> None:
        for step in state.plan:
            if step.id == step_id:
                step.status = status
                return

    def _fail(self, state: IncidentState, error: str) -> None:
        state.error = error
        self._save_status(state, IncidentStatus.FAILED, "Incident failed")
        state.final_summary = f"任务执行失败：{error}"
        self.store.save_incident(state)
        self._emit(state.id, "workflow_failed", state.final_summary, error=error)

