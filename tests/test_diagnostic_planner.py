from pathlib import Path
from types import SimpleNamespace

import pytest

from maintenance_assistant.diagnostic_planner import (
    DiagnosticPlanningError,
    DiagnosticSource,
    GeneratedDiagnosticPlan,
    GuidedDiagnosticService,
    OpenAIDiagnosticProvider,
    _DiagnosticPayload,
    _validate_plan,
    _preserve_known_facts,
)
from maintenance_assistant.diagnostics import (
    DiagnosticAction,
    DiagnosticHypothesis,
    DiagnosticLikelihood,
    DiagnosticSafetyStatus,
    DiagnosticState,
    DiagnosticStatus,
    DiagnosticStore,
)
from maintenance_assistant.ingestion import LocalDocumentStore


class EmptySearch:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, **_: object) -> tuple[()]:
        self.queries.append(query)
        return ()


class QuestionProvider:
    model = "diagnostic-test"

    def __init__(self) -> None:
        self.calls: list[tuple[str, DiagnosticState, tuple[tuple[str, str], ...]]] = []

    def plan(
        self,
        latest_message: str,
        state: DiagnosticState,
        recent_turns: tuple[tuple[str, str], ...],
        sources: tuple[DiagnosticSource, ...],
        safety_status: DiagnosticSafetyStatus,
    ) -> GeneratedDiagnosticPlan:
        self.calls.append((latest_message, state, recent_turns))
        return GeneratedDiagnosticPlan(
            action=DiagnosticAction.ASK_QUESTION,
            message="Does it trip immediately or after running for a period?",
            state=DiagnosticState(
                symptoms=state.symptoms,
                observations=state.observations + (latest_message,),
                summary="The pump trips during operation.",
            ),
            citation_ids=(),
            model=self.model,
        )


class FailingProvider(QuestionProvider):
    def plan(self, *args, **kwargs):
        raise DiagnosticPlanningError("provider failed")


def _service(tmp_path: Path) -> tuple[GuidedDiagnosticService, QuestionProvider]:
    store = DiagnosticStore(LocalDocumentStore(tmp_path / "data"))
    provider = QuestionProvider()
    return GuidedDiagnosticService(EmptySearch(), provider, store), provider  # type: ignore[arg-type]


def test_service_starts_and_continues_stateful_diagnostic_session(tmp_path: Path) -> None:
    service, provider = _service(tmp_path)

    started = service.start_session("Pump trips after five minutes")
    continued = service.continue_session(
        started.session.id,
        "It happens only when the discharge valve is open",
    )

    assert [turn.role.value for turn in started.turns] == ["user", "assistant"]
    assert [turn.role.value for turn in continued.turns] == [
        "user", "assistant", "user", "assistant"
    ]
    assert continued.session.state.observations[-1] == (
        "It happens only when the discharge valve is open"
    )
    assert provider.calls[-1][2][-1][0] == "assistant"


def _source() -> DiagnosticSource:
    return DiagnosticSource(
        source_id="S1",
        score=0.9,
        document=SimpleNamespace(id="doc-1", title="Pump manual", original_filename="pump.pdf"),
        chunk=SimpleNamespace(id="chunk-1", sequence=1),
    )  # type: ignore[arg-type]


def test_validator_accepts_grounded_check_and_hypothesis() -> None:
    plan = _validate_plan(
        GeneratedDiagnosticPlan(
            action=DiagnosticAction.SUGGEST_CHECK,
            message="Inspect the visible overload indicator [S1].",
            state=DiagnosticState(
                hypotheses=(
                    DiagnosticHypothesis(
                        "Overload trip",
                        DiagnosticLikelihood.MEDIUM,
                        "The indicator distinguishes an overload trip.",
                        ("S1",),
                    ),
                )
            ),
            citation_ids=("S1",),
            model="test",
        ),
        (_source(),),
        DiagnosticSafetyStatus.NON_INTRUSIVE_ONLY,
    )

    assert plan.citations[0].source_id == "S1"


@pytest.mark.parametrize(
    ("generated", "message"),
    [
        (
            GeneratedDiagnosticPlan(
                action=DiagnosticAction.ASK_QUESTION,
                message="Which alarm is visible [S1]?",
                state=DiagnosticState(),
                citation_ids=(),
            ),
            "markers did not match",
        ),
        (
            GeneratedDiagnosticPlan(
                action=DiagnosticAction.SUGGEST_CHECK,
                message="Inspect the coupling.",
                state=DiagnosticState(),
                citation_ids=(),
            ),
            "must cite evidence",
        ),
        (
            GeneratedDiagnosticPlan(
                action=DiagnosticAction.REQUEST_MEASUREMENT,
                message="Measure the live terminals [S1].",
                state=DiagnosticState(),
                citation_ids=("S1",),
                intrusive=True,
            ),
            "requires explicit safety confirmation",
        ),
        (
            GeneratedDiagnosticPlan(
                action=DiagnosticAction.ASK_QUESTION,
                message="Is the fault resolved?",
                state=DiagnosticState(),
                citation_ids=(),
                status=DiagnosticStatus.RESOLVED,
            ),
            "requires a resolution action",
        ),
    ],
)
def test_validator_rejects_unsafe_or_inconsistent_plans(
    generated: GeneratedDiagnosticPlan,
    message: str,
) -> None:
    with pytest.raises(DiagnosticPlanningError, match=message):
        _validate_plan(
            generated,
            (_source(),),
            DiagnosticSafetyStatus.UNKNOWN,
        )


def test_missing_session_is_rejected_before_model_call(tmp_path: Path) -> None:
    service, provider = _service(tmp_path)

    with pytest.raises(KeyError):
        service.continue_session("missing", "What next?")

    assert provider.calls == []


def test_failed_initial_plan_does_not_leave_a_partial_session(tmp_path: Path) -> None:
    store = DiagnosticStore(LocalDocumentStore(tmp_path / "data"))
    service = GuidedDiagnosticService(EmptySearch(), FailingProvider(), store)  # type: ignore[arg-type]

    with pytest.raises(DiagnosticPlanningError, match="provider failed"):
        service.start_session("Pump will not start")

    assert store.list_sessions() == ()


def test_known_worker_facts_cannot_be_silently_removed_by_a_model_turn() -> None:
    previous = DiagnosticState(
        symptoms=("Pump trips under load",),
        observations=("Red overload light is visible",),
        completed_checks=("Inspected the status panel",),
    )

    preserved = _preserve_known_facts(
        previous,
        DiagnosticState(observations=("Cooling fan is stationary",), summary="Updated"),
    )

    assert preserved.symptoms == previous.symptoms
    assert preserved.observations == (
        "Red overload light is visible",
        "Cooling fan is stationary",
    )
    assert preserved.completed_checks == previous.completed_checks


def test_stop_state_and_escalation_status_are_enforced() -> None:
    with pytest.raises(DiagnosticPlanningError, match="must escalate"):
        _validate_plan(
            GeneratedDiagnosticPlan(
                action=DiagnosticAction.ASK_QUESTION,
                message="What happened?",
                state=DiagnosticState(),
                citation_ids=(),
            ),
            (),
            DiagnosticSafetyStatus.STOP,
        )


def test_openai_provider_requests_typed_non_stored_diagnostic_output() -> None:
    parsed = _DiagnosticPayload.model_validate(
        {
            "action": "ask_question",
            "message": "Which fault code is visible?",
            "state": {
                "symptoms": ["Drive stopped"],
                "observations": [],
                "measurements": [],
                "completed_checks": [],
                "hypotheses": [],
                "summary": "The drive stopped.",
            },
            "citations": [],
            "intrusive": False,
            "requires_safety_confirmation": False,
            "status": "active",
        }
    )
    response = SimpleNamespace(
        output_parsed=parsed,
        model="diagnostic-model",
        usage=SimpleNamespace(input_tokens=20, output_tokens=8),
    )
    calls = {}

    def parse(**kwargs):
        calls.update(kwargs)
        return response

    parse = SimpleNamespace(parse=parse)
    client = SimpleNamespace(responses=parse)
    provider = OpenAIDiagnosticProvider(
        api_key="test-key",
        model="diagnostic-model",
        max_output_tokens=500,
        client=client,
    )

    generated = provider.plan(
        "The drive stopped",
        DiagnosticState(symptoms=("Drive stopped",)),
        (("user", "The drive stopped"),),
        (),
        DiagnosticSafetyStatus.UNKNOWN,
    )

    assert generated.action == DiagnosticAction.ASK_QUESTION
    assert calls["text_format"] is _DiagnosticPayload
    assert calls["store"] is False

    with pytest.raises(DiagnosticPlanningError, match="must match"):
        _validate_plan(
            GeneratedDiagnosticPlan(
                action=DiagnosticAction.ESCALATE,
                message="Stop and contact the responsible engineer.",
                state=DiagnosticState(),
                citation_ids=(),
            ),
            (),
            DiagnosticSafetyStatus.UNKNOWN,
        )
