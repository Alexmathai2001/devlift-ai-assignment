"""Every user-visible reply is built here, in code, from fixed templates.

The model only chooses WHICH function to call. It never writes free text that reaches the user,
so an off-topic or manipulative prompt cannot make the chat talk about anything else.
"""
from . import guardrails

MAX_DETAIL = 200

OUT_OF_CONTEXT = (
    "That's outside what I can help with. I only handle configuration changes to the health-api "
    "ECS service on AWS: task count, CPU and memory, image tag, environment variables, log "
    "retention and deployment settings.\n\n"
    'Try something like "scale to 2 tasks" or "set log retention to 14 days".'
)

NOT_POSSIBLE = {
    "networking_or_load_balancer": "I can't change networking, subnets, security groups or the load balancer.",
    "iam_or_security": "I can't change IAM roles, permissions or security settings.",
    "port_change": "I can't change the container port; the app is built to listen on 8080.",
    "create_or_delete_resources": "I can't create or delete AWS resources; I only change settings of the existing service.",
    "region_or_account": "I can't change the AWS region or account.",
    "exceeds_limits": "That is outside the limits this tool enforces.",
    "unsupported_setting": "That setting isn't one I'm able to change.",
    "other": "That isn't something this tool can do.",
}

CAPABILITIES = "What I can change: " + "; ".join(
    f"{name} ({desc})" for name, desc in guardrails.ALLOWED_VARIABLES.items()) + "."


def _clean(text, limit=MAX_DETAIL) -> str:
    """Single line, length-capped, so model-written detail can't turn into an essay."""
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def not_possible(data: dict) -> str:
    base = NOT_POSSIBLE.get(data.get("reason_code"), NOT_POSSIBLE["other"])
    detail = _clean(data.get("detail"))
    return f"That isn't possible: {base}" + (f" ({detail})" if detail else "") + f"\n\n{CAPABILITIES}"


def clarify(data: dict) -> str:
    q = _clean(data.get("question"))
    return q or "Could you say a bit more about what you'd like to change?"


def config(current: dict, data: dict) -> str:
    wanted = data.get("fields") or ["all"]
    names = list(current) if "all" in wanted else [f for f in wanted if f in current]
    if not names:
        names = list(current)
    lines = [f"{n}: {current[n] if current[n] not in ({}, None) else '(none)'}" for n in names]
    return "Current configuration:\n" + "\n".join(lines)


def changes(changed: dict, current: dict) -> str:
    parts = []
    for name, new in changed.items():
        old = current.get(name)
        if name == "environment":
            added = sorted(set(new) - set(old or {}))
            removed = sorted(set(old or {}) - set(new))
            edited = sorted(k for k in new if k in (old or {}) and new[k] != old[k])
            bits = [f"+{k}" for k in added] + [f"~{k}" for k in edited] + [f"-{k}" for k in removed]
            parts.append("environment " + " ".join(bits))
        else:
            parts.append(f"{name} {old} → {new}")
    return "Proposed: " + "; ".join(parts) + ".\nReview the Terraform plan below, then Apply or Cancel."


def rejected(errors: list[str]) -> str:
    return "That isn't possible: " + "; ".join(errors) + f".\n\n{CAPABILITIES}"


def blocked(reasons: list[str]) -> str:
    return "That isn't possible: the resulting plan was blocked for safety (" + "; ".join(reasons) + ")."
