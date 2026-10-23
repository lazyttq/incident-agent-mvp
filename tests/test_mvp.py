from __future__ import annotations

from pathlib import Path

from app.models import ApprovalDecision, IncidentCreate, IncidentStatus
from app.orchestrator import IncidentOrchestrator
from app.store import SQLiteStore


def make_orchestrator(tmp_path: Path) -> IncidentOrchestrator:
    return IncidentOrchestrator(SQLiteStore(tmp_path / "agent.db"))


def test_incident_stops_for_approval_with_evidence(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    state = orchestrator.create(
        IncidentCreate(
            title="checkout 数据库连接池耗尽",
            description="线上 checkout 服务报数据库连接池耗尽，接口大量超时",
        )
    )

    assert state.status == IncidentStatus.WAITING_APPROVAL
    assert state.scenario == "database_pool"
    assert len(state.evidence) == 3
    assert state.approval is not None
    assert {call.status for call in state.tool_calls} == {"SUCCEEDED"}


def test_approval_completes_simulated_remediation(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    state = orchestrator.create(
        IncidentCreate(title="发布后错误率回归", description="新版本发布后订单接口错误率上涨")
    )

    completed = orchestrator.approve(state.id, ApprovalDecision(approved=True, comment="同意回滚"))

    assert completed.status == IncidentStatus.COMPLETED
    assert completed.final_summary is not None
    assert any(call.tool == "execute_remediation" for call in completed.tool_calls)
    assert any(call.tool == "verify_recovery" and call.status == "SUCCEEDED" for call in completed.tool_calls)


def test_rejection_does_not_execute_mutation(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    state = orchestrator.create(
        IncidentCreate(title="支付超时", description="payment upstream timeout and high latency")
    )

    cancelled = orchestrator.approve(state.id, ApprovalDecision(approved=False, comment="先人工确认上游") )

    assert cancelled.status == IncidentStatus.CANCELLED
    assert not any(call.tool == "execute_remediation" for call in cancelled.tool_calls)


def test_transient_tool_error_is_retried(tmp_path: Path) -> None:
    orchestrator = make_orchestrator(tmp_path)
    state = orchestrator.create(
        IncidentCreate(
            title="发布后错误率回归",
            description="release deploy regression",
            metadata={"fail_tools": ["query_logs"]},
        )
    )

    logs_call = next(call for call in state.tool_calls if call.tool == "query_logs")
    assert logs_call.status == "SUCCEEDED"
    assert logs_call.attempts == 2


def test_state_survives_new_store_instance(tmp_path: Path) -> None:
    database = tmp_path / "agent.db"
    orchestrator = IncidentOrchestrator(SQLiteStore(database))
    created = orchestrator.create(
        IncidentCreate(title="接口延迟升高", description="checkout latency spike")
    )

    restored = SQLiteStore(database).get_incident(created.id)

    assert restored is not None
    assert restored.id == created.id
    assert restored.trace_id == created.trace_id
    assert len(SQLiteStore(database).list_events(created.id)) >= 6

