"""Terraform driver: write overrides -> plan -> (human approval) -> apply the exact saved plan.

The model never writes HCL. It only produces values for existing Terraform variables, which land
in `chatops.auto.tfvars.json` (Terraform loads *.auto.tfvars.json automatically).
"""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_DIR = Path(os.environ.get("CHATOPS_TF_DIR", REPO_ROOT / "terraform" / "ecs"))
OVERRIDES = TF_DIR / "chatops.auto.tfvars.json"
PLAN_FILE = "chatops.tfplan"  # relative to TF_DIR
TERRAFORM = shutil.which("terraform") or str(Path.home() / ".local/bin/terraform")

# Resources a chat-driven change may touch. Anything else in a plan blocks the apply.
ALLOWED_ADDRESSES = {
    "aws_ecs_service.app",
    "aws_ecs_task_definition.app",
    "aws_cloudwatch_log_group.app",
}
# Only the task definition may be replaced (ECS makes a new revision); nothing else may be deleted.
MAY_DELETE = {"aws_ecs_task_definition.app"}
# For these, hide attributes that are merely "(known after apply)" (new ARN, id, revision...).
QUIET_WHEN_COMPUTED = {"aws_ecs_task_definition.app"}

_lock = threading.Lock()  # one Terraform operation at a time


class TerraformError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        [TERRAFORM, f"-chdir={TF_DIR}", *args],
        capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "TF_IN_AUTOMATION": "1"},
    )


def read_overrides() -> dict:
    return json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}


def _flatten(value, prefix="") -> dict:
    """Flatten nested dicts/lists (and JSON-in-strings) to dotted paths so diffs are readable."""
    if isinstance(value, str) and value[:1] in "[{":
        try:
            value = json.loads(value)
        except ValueError:
            pass
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else k))
        return out
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}[{i}]"))
        return out
    return {prefix: value}


def summarize_plan(plan_json: dict) -> dict:
    """Turn `terraform show -json` into a compact, reviewable summary and a safety verdict."""
    resources, blocked = [], []
    for rc in plan_json.get("resource_changes", []):
        actions = rc["change"]["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        addr = rc["address"]
        if addr not in ALLOWED_ADDRESSES:
            blocked.append(f"{addr} ({'/'.join(actions)}) is outside what the chat may change")
        if "delete" in actions and addr not in MAY_DELETE:
            blocked.append(f"{addr} would be deleted")
        before = _flatten(rc["change"].get("before") or {})
        after = _flatten(rc["change"].get("after") or {})
        unknown = _flatten(rc["change"].get("after_unknown") or {})
        diffs = []
        pending_keys = {k for k, v in unknown.items() if v is True}
        for key in sorted(set(before) | set(after) | pending_keys):
            if addr in QUIET_WHEN_COMPUTED and (key in pending_keys or key.endswith("hostPort")):
                continue  # IDs/ARNs that ECS assigns itself; they would bury the real change
            if key in pending_keys:
                diffs.append({"attribute": key, "before": before.get(key), "after": "(known after apply)"})
            elif before.get(key) != after.get(key):
                diffs.append({"attribute": key, "before": before.get(key), "after": after.get(key)})
        resources.append({"address": addr, "actions": actions, "changes": diffs})
    return {"resources": resources, "blocked": blocked, "has_changes": bool(resources)}


def make_plan(new_overrides: dict) -> dict:
    """Write overrides, run `terraform plan -out`, and return a summary.

    The previous overrides are returned in the result so a cancel can roll the file back.
    """
    with _lock:
        previous = OVERRIDES.read_text() if OVERRIDES.exists() else None
        merged = {**read_overrides(), **new_overrides}
        OVERRIDES.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
        try:
            plan = _run(["plan", "-input=false", "-no-color", f"-out={PLAN_FILE}"])
            if plan.returncode != 0:
                raise TerraformError(plan.stderr.strip() or plan.stdout.strip())
            shown = _run(["show", "-json", PLAN_FILE])
            if shown.returncode != 0:
                raise TerraformError(shown.stderr.strip())
            summary = summarize_plan(json.loads(shown.stdout))
        except Exception:
            _restore(previous)
            raise
        summary["previous_overrides"] = previous
        return summary


def _restore(previous: str | None) -> None:
    if previous is None:
        OVERRIDES.unlink(missing_ok=True)
    else:
        OVERRIDES.write_text(previous)
    (TF_DIR / PLAN_FILE).unlink(missing_ok=True)


def cancel(previous: str | None) -> None:
    with _lock:
        _restore(previous)


def apply() -> str:
    """Apply the exact plan that was reviewed. Terraform itself rejects a stale plan."""
    with _lock:
        if not (TF_DIR / PLAN_FILE).exists():
            raise TerraformError("no saved plan to apply")
        result = _run(["apply", "-input=false", "-no-color", PLAN_FILE])
        (TF_DIR / PLAN_FILE).unlink(missing_ok=True)
        if result.returncode != 0:
            raise TerraformError(result.stderr.strip() or result.stdout.strip())
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "applied"
