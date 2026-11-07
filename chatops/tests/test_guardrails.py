from app import guardrails as g

CURRENT = {
    "desired_count": 1, "task_cpu": 512, "task_memory": 1024, "image_tag": "v1",
    "log_retention_days": 7, "health_check_grace_period_seconds": 60,
    "deployment_minimum_healthy_percent": 100, "deployment_maximum_percent": 200, "environment": {},
}


def check(proposal):
    return g.validate(proposal, CURRENT)


def test_scale_up_is_accepted():
    changes, errors, _ = check({"desired_count": 2})
    assert changes == {"desired_count": 2} and not errors


def test_noop_yields_no_changes():
    changes, errors, _ = check({"desired_count": 1})
    assert changes == {} and not errors


def test_too_many_tasks_rejected():
    _, errors, _ = check({"desired_count": 50})
    assert errors


def test_zero_tasks_allowed_but_warns_offline():
    changes, errors, warnings = check({"desired_count": 0})
    assert changes == {"desired_count": 0} and not errors
    assert any("OFFLINE" in w for w in warnings)


def test_invalid_cpu_memory_combination_rejected():
    _, errors, _ = check({"task_cpu": 256, "task_memory": 8192})
    assert errors and "256" in errors[0]


def test_cpu_change_alone_checks_existing_memory():
    # 1024 MiB is invalid for 2048 CPU units, so bumping CPU alone must be rejected
    _, errors, _ = check({"task_cpu": 2048})
    assert errors


def test_cpu_and_memory_together_accepted():
    changes, errors, warnings = check({"task_cpu": 1024, "task_memory": 2048})
    assert changes == {"task_cpu": 1024, "task_memory": 2048} and not errors
    assert any("redeploy" in w for w in warnings)


def test_unknown_setting_rejected():
    for name in ("container_port", "vpc_cidr", "aws_region", "name"):
        _, errors, _ = check({name: 1})
        assert errors, name


def test_bool_and_float_rejected():
    assert check({"desired_count": True})[1]
    assert check({"desired_count": 1.5})[1]
    assert check({"desired_count": "2"})[1]


def test_bad_log_retention_rejected():
    assert check({"log_retention_days": 10})[1]
    assert check({"log_retention_days": 30})[0] == {"log_retention_days": 30}


def test_env_set_merges_with_existing():
    cur = {**CURRENT, "environment": {"A": "1"}}
    changes, errors, _ = g.validate({"set_environment": [{"name": "B", "value": "2"}]}, cur)
    assert changes == {"environment": {"A": "1", "B": "2"}} and not errors


def test_env_unset_removes():
    cur = {**CURRENT, "environment": {"A": "1", "B": "2"}}
    changes, _, _ = g.validate({"unset_environment": ["A"]}, cur)
    assert changes == {"environment": {"B": "2"}}


def test_secret_looking_env_rejected():
    for name in ("DB_PASSWORD", "api_key", "AUTH_TOKEN", "AWS_SECRET_ACCESS_KEY"):
        _, errors, _ = check({"set_environment": [{"name": name, "value": "x"}]})
        assert errors, name


def test_bad_env_name_rejected():
    assert check({"set_environment": [{"name": "1BAD", "value": "x"}]})[1]
    assert check({"set_environment": [{"name": "A B", "value": "x"}]})[1]


def test_image_tag_format():
    assert check({"image_tag": "v2"})[0] == {"image_tag": "v2"}
    assert check({"image_tag": "v2; rm -rf /"})[1]
    assert check({"image_tag": "../x"})[1]


def test_deployment_percent_bounds():
    assert check({"deployment_maximum_percent": 50})[1]
    assert check({"deployment_minimum_healthy_percent": 150})[1]
    assert check({"deployment_minimum_healthy_percent": 50})[0] == {"deployment_minimum_healthy_percent": 50}
