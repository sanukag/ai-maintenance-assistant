from pathlib import Path
import re

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_uses_immutable_actions_with_read_only_permissions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    actions = re.findall(r"^\s*uses:\s+([^\s#]+)", workflow, flags=re.MULTILINE)

    assert actions
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in actions)
    assert "permissions:\n  contents: read" in workflow
    assert "pull_request_target" not in workflow


def test_ci_enforces_coverage_and_real_container_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "--cov-fail-under=90" in workflow
    assert 'AMA_RUN_CONTAINER_TESTS: "1"' in workflow
    assert "python -m pytest tests/container -q" in workflow
