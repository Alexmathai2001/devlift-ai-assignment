"""Guardrails: the ONLY things the chat is allowed to change.

The model proposes values; this module decides whether they are acceptable. Nothing the model
says reaches Terraform without passing through validate().
"""
import re

# Valid Fargate CPU -> memory (MiB) combinations (capped at 2 vCPU to avoid cost surprises).
FARGATE_MEMORY = {
    256: [512, 1024, 2048],
    512: [1024, 2048, 3072, 4096],
    1024: [2048, 3072, 4096, 5120, 6144, 7168, 8192],
    2048: list(range(4096, 16385, 1024)),
}
LOG_RETENTION = {1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731,
                 1096, 1827, 2192, 2557, 2922, 3288, 3653}
MAX_TASKS = 4
SECRET_HINTS = ("PASSWORD", "SECRET", "TOKEN", "KEY", "CREDENTIAL", "PRIVATE")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
IMAGE_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")

# Terraform variable name -> human description (also shown to the model).
ALLOWED_VARIABLES = {
    "desired_count": f"number of running tasks, 0-{MAX_TASKS}",
    "task_cpu": f"CPU units, one of {sorted(FARGATE_MEMORY)}",
    "task_memory": "memory in MiB; must be valid for the chosen CPU",
    "image_tag": "ECR image tag to deploy (must already exist in ECR)",
    "log_retention_days": f"CloudWatch log retention, one of {sorted(LOG_RETENTION)}",
    "health_check_grace_period_seconds": "0-600",
    "deployment_minimum_healthy_percent": "0-100",
    "deployment_maximum_percent": "100-200",
    "environment": "container environment variables (map of NAME -> value, no secrets)",
}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(proposal: dict, current: dict) -> tuple[dict, list[str], list[str]]:
    """Check a model proposal against the current config.

    proposal: raw tool input from the model (None/missing fields mean "no change").
    current:  effective config read from AWS, keyed by Terraform variable name.
    Returns (changes, errors, warnings). `changes` only contains variables that actually differ
    from `current`, so a no-op request yields an empty dict.
    """
    errors: list[str] = []
    warnings: list[str] = []
    wanted: dict = {}

    unknown = set(proposal) - set(ALLOWED_VARIABLES) - {"set_environment", "unset_environment", "summary"}
    for name in sorted(unknown):
        errors.append(f"'{name}' is not a setting this chat may change")

    def int_in(name: str, lo: int, hi: int):
        v = proposal.get(name)
        if v is None:
            return
        if not _is_int(v) or not lo <= v <= hi:
            errors.append(f"{name} must be an integer between {lo} and {hi} (got {v!r})")
        else:
            wanted[name] = v

    int_in("desired_count", 0, MAX_TASKS)
    int_in("health_check_grace_period_seconds", 0, 600)
    int_in("deployment_minimum_healthy_percent", 0, 100)
    int_in("deployment_maximum_percent", 100, 200)

    v = proposal.get("task_cpu")
    if v is not None:
        if v in FARGATE_MEMORY and _is_int(v):
            wanted["task_cpu"] = v
        else:
            errors.append(f"task_cpu must be one of {sorted(FARGATE_MEMORY)} (got {v!r})")

    v = proposal.get("task_memory")
    if v is not None:
        if _is_int(v) and v > 0:
            wanted["task_memory"] = v
        else:
            errors.append(f"task_memory must be a positive integer in MiB (got {v!r})")

    v = proposal.get("log_retention_days")
    if v is not None:
        if _is_int(v) and v in LOG_RETENTION:
            wanted["log_retention_days"] = v
        else:
            errors.append(f"log_retention_days must be one of {sorted(LOG_RETENTION)} (got {v!r})")

    v = proposal.get("image_tag")
    if v is not None:
        if isinstance(v, str) and IMAGE_TAG.match(v):
            wanted["image_tag"] = v
        else:
            errors.append(f"image_tag is not a valid tag (got {v!r})")

    # Environment: build the new full map from the current one plus set/unset instructions.
    env_set = proposal.get("set_environment") or []
    env_unset = proposal.get("unset_environment") or []
    if env_set or env_unset:
        env = dict(current.get("environment") or {})
        for item in env_set:
            name, value = (item or {}).get("name"), (item or {}).get("value")
            if not isinstance(name, str) or not ENV_NAME.match(name):
                errors.append(f"invalid environment variable name {name!r}")
            elif any(h in name.upper() for h in SECRET_HINTS):
                errors.append(f"'{name}' looks like a secret; secrets must not go in plain environment variables")
            elif not isinstance(value, str) or len(value) > 256:
                errors.append(f"value for {name} must be a string up to 256 characters")
            else:
                env[name] = value
        for name in env_unset:
            if name not in env:
                warnings.append(f"environment variable {name} is not set, nothing to remove")
            env.pop(name, None)
        wanted["environment"] = env

    # Cross-field checks on the resulting configuration.
    merged = {**current, **wanted}
    cpu, mem = merged.get("task_cpu"), merged.get("task_memory")
    if ("task_cpu" in wanted or "task_memory" in wanted) and cpu in FARGATE_MEMORY and mem not in FARGATE_MEMORY[cpu]:
        errors.append(f"{cpu} CPU units supports memory of {FARGATE_MEMORY[cpu]} MiB, not {mem}")
    if merged.get("deployment_maximum_percent", 200) <= merged.get("deployment_minimum_healthy_percent", 100) \
            and merged.get("desired_count", 1) > 0 and merged.get("deployment_maximum_percent", 200) < 100:
        errors.append("deployment_maximum_percent must be at least 100")

    changes = {k: v for k, v in wanted.items() if current.get(k) != v}
    if changes.get("desired_count") == 0:
        warnings.append("desired_count 0 stops every task: the app will be OFFLINE until scaled back up")
    if "task_cpu" in changes or "task_memory" in changes:
        warnings.append("changing CPU/memory creates a new task definition and triggers a rolling redeploy")
    if "environment" in changes or "image_tag" in changes:
        warnings.append("this creates a new task definition and triggers a rolling redeploy")
    return changes, errors, warnings
