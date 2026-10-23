from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException

from .models import ApprovalDecision, Event, IncidentCreate, IncidentResponse, IncidentState
from .orchestrator import IncidentOrchestrator
from .store import SQLiteStore


DB_PATH = os.getenv("AGENT_DB_PATH", "data/agent.db")
store = SQLiteStore(DB_PATH)
orchestrator = IncidentOrchestrator(store)

app = FastAPI(
    title="Incident-to-Resolution Agent MVP",
    version="0.1.0",
    description="A safe, stateful local MVP for incident diagnosis and approved remediation.",
)


def response_for(state: IncidentState) -> IncidentResponse:
    next_action = None
    if state.status.value == "WAITING_APPROVAL":
        next_action = "POST /incidents/{id}/approve with {approved: true|false}"
    elif state.status.value in {"COMPLETED", "FAILED", "CANCELLED"}:
        next_action = "none"
    return IncidentResponse(incident=state, next_action=next_action)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "incident-agent-mvp"}


@app.post("/incidents", response_model=IncidentResponse, status_code=201)
def create_incident(request: IncidentCreate) -> IncidentResponse:
    return response_for(orchestrator.create(request))


@app.get("/incidents/{incident_id}", response_model=IncidentResponse)
def get_incident(incident_id: str) -> IncidentResponse:
    state = store.get_incident(incident_id)
    if not state:
        raise HTTPException(status_code=404, detail="incident not found")
    return response_for(state)


@app.get("/incidents/{incident_id}/events", response_model=list[Event])
def get_events(incident_id: str) -> list[Event]:
    if not store.get_incident(incident_id):
        raise HTTPException(status_code=404, detail="incident not found")
    return store.list_events(incident_id)


@app.post("/incidents/{incident_id}/approve", response_model=IncidentResponse)
def approve_incident(incident_id: str, decision: ApprovalDecision) -> IncidentResponse:
    try:
        return response_for(orchestrator.approve(incident_id, decision))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/incidents/{incident_id}/cancel", response_model=IncidentResponse)
def cancel_incident(incident_id: str, decision: ApprovalDecision | None = None) -> IncidentResponse:
    try:
        reason = decision.comment if decision else None
        return response_for(orchestrator.cancel(incident_id, reason))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

