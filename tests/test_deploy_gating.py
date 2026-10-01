"""A deploy needs a green CI run of a push to main: the blueprint and the deploy workflow.

``render.yaml`` had ``autoDeployTrigger: commit``, which deploys every push at once whatever CI
says (the comment in ``deploy.yml`` nevertheless promised that "a broken build never reaches
production"). ``deploy.yml`` also only looked at ``conclusion == 'success'`` of a ``workflow_run``
filtered by branch *name*, which a pull request from a fork's own ``main`` branch matches too.

Neither file can be executed here, so the tests read them: the blueprint's trigger, and the
workflow's ``if`` expression, evaluated against sample ``workflow_run`` payloads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
BLUEPRINT = (ROOT / "render.yaml").read_text()

REPOSITORY = "owner/nim-key-manager"
GOOD_RUN = {
    "conclusion": "success",
    "event": "push",
    "head_branch": "main",
    "head_repository.full_name": REPOSITORY,
}


def deploy_condition() -> str:
    """The ``if`` of the deploy job (single-line or folded block), on one line."""
    inline = re.search(r"^[ \t]+if:[ \t]*(?P<expression>[^>\s].*)$", WORKFLOW, re.MULTILINE)
    if inline:
        return inline.group("expression").strip()
    folded = re.search(
        r"^(?P<indent>[ \t]+)if:\s*>-?\s*\n(?P<body>(?:(?P=indent)[ \t]+\S.*\n)+)",
        WORKFLOW,
        re.MULTILINE,
    )
    assert folded, "the deploy job must have an `if`"
    return " ".join(folded.group("body").split())


def deploys(**changes: str) -> bool:
    """Evaluate the deploy job's ``if`` for a ``workflow_run`` payload with ``changes`` applied."""
    expression = deploy_condition()
    # The evaluator below understands exactly these two operators: anything else must fail loudly.
    assert set(re.findall(r"[=!<>|&]+", expression)) <= {"==", "&&"}, expression
    payload = {**GOOD_RUN, **changes}
    context = {f"github.event.workflow_run.{key}": value for key, value in payload.items()}
    context["github.repository"] = REPOSITORY
    for clause in expression.split("&&"):
        left, right = (side.strip() for side in clause.split("=="))
        expected = right.strip("'") if right.startswith("'") else context[right]
        if context[left] != expected:
            return False
    return True


# --------------------------------------------------------------------------- #
# render.yaml                                                                  #
# --------------------------------------------------------------------------- #
def test_render_deploys_a_commit_only_after_its_ci_checks_pass():
    triggers = re.findall(r"^\s+autoDeployTrigger:\s*(\S+)", BLUEPRINT, re.MULTILINE)
    assert triggers == ["checksPass"]  # one web service; never "commit"


# --------------------------------------------------------------------------- #
# .github/workflows/deploy.yml                                                 #
# --------------------------------------------------------------------------- #
def test_a_successful_ci_run_of_a_push_to_main_deploys():
    assert deploys() is True


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out", ""])
def test_a_ci_run_that_did_not_succeed_does_not_deploy(conclusion):
    assert deploys(conclusion=conclusion) is False


@pytest.mark.parametrize("event", ["pull_request", "pull_request_target", "workflow_dispatch"])
def test_only_pushes_deploy(event):
    """A pull request's CI run can be green too, and a fork's `main` branch is called `main`."""
    assert deploys(event=event) is False


def test_a_pull_request_from_a_forks_main_branch_does_not_deploy():
    assert deploys(event="pull_request", **{"head_repository.full_name": "stranger/fork"}) is False


@pytest.mark.parametrize("branch", ["feature", "fix/security-errors", "main-2", "release"])
def test_other_branches_do_not_deploy(branch):
    assert deploys(head_branch=branch) is False


def test_a_push_to_somebody_elses_repository_does_not_deploy():
    assert deploys(**{"head_repository.full_name": "stranger/fork"}) is False


def test_the_workflow_still_only_listens_to_the_ci_workflow_on_main():
    assert re.search(r"workflows:\s*\[CI\]", WORKFLOW)
    assert re.search(r"types:\s*\[completed\]", WORKFLOW)
    assert re.search(r"branches:\s*\[main\]", WORKFLOW)
    assert re.search(r"^permissions:\s*\{\}\s*$", WORKFLOW, re.MULTILINE)
