from __future__ import annotations

import pytest
from pydantic import ValidationError

from codex_ai_os.application.responses import error_envelope, success_envelope
from codex_ai_os.domain.coordination import HandoffReviewInput
from codex_ai_os.domain.governance import (
    CheckEvidenceInput,
    CheckStatus,
    ReviewDecision,
    ReviewFinding,
    ReviewFindingSeverity,
    ReviewFindingStatus,
)
from codex_ai_os.domain.invocation import InvocationContext, InvocationSource
from codex_ai_os.domain.workflow import ActionKind, NextAction


def test_shared_response_envelope_uses_authoritative_next_actions() -> None:
    context = InvocationContext.local(InvocationSource.MCP)
    action = NextAction(
        kind=ActionKind.HOST_OPERATION,
        operation_id="OP-1",
        expected_state_version=7,
    ).model_dump(mode="json")

    payload = success_envelope(
        {"result": "pending"},
        context=context,
        run_id="RUN-1",
        run_status="running",
        workflow_phase="implementation",
        state_version=7,
        next_actions=(action,),
    )

    assert payload["api_version"] == "1.2"
    assert payload["request_id"] == context.request_id
    assert payload["correlation_id"] == context.correlation_id
    assert payload["next_actions"] == [action]
    assert payload["next_action"] == action
    assert payload["warnings"] == []
    assert payload["error"] is None


def test_shared_error_envelope_has_retryability_and_details() -> None:
    payload = error_envelope(
        "STATE_VERSION_CONFLICT",
        "state changed",
        {"expected": 4, "actual": 5},
        retryable=True,
    )

    assert payload["ok"] is False
    assert payload["next_actions"] == []
    assert payload["error"] == {
        "code": "STATE_VERSION_CONFLICT",
        "message": "state changed",
        "details": {"expected": 4, "actual": 5},
        "retryable": True,
    }


def test_accepted_handoff_rejects_open_high_finding() -> None:
    finding = ReviewFinding(
        id="FINDING-1",
        severity=ReviewFindingSeverity.HIGH,
        status=ReviewFindingStatus.OPEN,
        summary="Unresolved authorization bypass",
    )

    with pytest.raises(ValidationError, match="open high/critical"):
        HandoffReviewInput(
            handoff_id="HANDOFF-1",
            reviewer="reviewer",
            reviewed_commit="a" * 40,
            decision=ReviewDecision.ACCEPTED,
            reason="incorrectly accepted",
            findings=(finding,),
            report_ref="reports/review.json",
            report_hash="b" * 64,
        )


def test_check_evidence_status_and_timestamps_fail_closed() -> None:
    baseline = {
        "name": "pytest",
        "command_hash": "a" * 64,
        "execution_id": "EXEC-1",
        "exit_code": 0,
        "report_path": "reports/pytest.json",
        "report_hash": "b" * 64,
        "source_commit": "c" * 40,
        "started_at": "2026-08-29T00:00:00+00:00",
        "ended_at": "2026-08-29T00:01:00+00:00",
        "executed_at": "2026-08-29T00:01:00+00:00",
        "status": CheckStatus.PASSED,
    }
    CheckEvidenceInput.model_validate(baseline)
    for updates in (
        {"exit_code": 7},
        {"status": CheckStatus.FAILED},
        {"started_at": "invalid"},
        {"started_at": "2026-08-29T00:00:00"},
        {"ended_at": "2026-08-28T00:00:00+00:00"},
    ):
        with pytest.raises(ValidationError):
            CheckEvidenceInput.model_validate({**baseline, **updates})
