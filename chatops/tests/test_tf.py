from app import tf


def rc(address, actions, before=None, after=None, unknown=None):
    return {"address": address, "change": {"actions": actions, "before": before, "after": after,
                                           "after_unknown": unknown or {}}}


def test_scale_plan_is_allowed():
    plan = {"resource_changes": [
        rc("aws_ecs_service.app", ["update"], {"desired_count": 1}, {"desired_count": 2}),
        rc("aws_vpc.main", ["no-op"], {"x": 1}, {"x": 1}),
    ]}
    s = tf.summarize_plan(plan)
    assert s["has_changes"] and not s["blocked"]
    assert s["resources"][0]["changes"] == [{"attribute": "desired_count", "before": 1, "after": 2}]


def test_task_definition_replacement_is_allowed_and_diffed_inside_json():
    before = {"container_definitions": '[{"name":"a","image":"x:v1"}]'}
    after = {"container_definitions": '[{"name":"a","image":"x:v2"}]'}
    plan = {"resource_changes": [rc("aws_ecs_task_definition.app", ["delete", "create"], before, after)]}
    s = tf.summarize_plan(plan)
    assert not s["blocked"]
    assert s["resources"][0]["changes"] == [
        {"attribute": "container_definitions[0].image", "before": "x:v1", "after": "x:v2"}]


def test_touching_network_or_alb_is_blocked():
    for addr in ("aws_vpc.main", "aws_lb.main", "aws_security_group.task", "aws_iam_role.task_execution"):
        s = tf.summarize_plan({"resource_changes": [rc(addr, ["update"], {"a": 1}, {"a": 2})]})
        assert s["blocked"], addr


def test_deleting_service_or_log_group_is_blocked():
    for addr in ("aws_ecs_service.app", "aws_cloudwatch_log_group.app"):
        s = tf.summarize_plan({"resource_changes": [rc(addr, ["delete"], {"a": 1}, None)]})
        assert any("deleted" in b for b in s["blocked"]), addr


def test_empty_plan_has_no_changes():
    s = tf.summarize_plan({"resource_changes": []})
    assert not s["has_changes"] and not s["blocked"]


def test_task_definition_hides_computed_noise_but_service_shows_new_revision():
    td = rc("aws_ecs_task_definition.app", ["delete", "create"],
            {"cpu": "512", "arn": "old"}, {"cpu": "1024"}, {"arn": True, "id": True})
    svc = rc("aws_ecs_service.app", ["update"], {"task_definition": "old"}, {}, {"task_definition": True})
    s = tf.summarize_plan({"resource_changes": [svc, td]})
    by = {r["address"]: r["changes"] for r in s["resources"]}
    assert by["aws_ecs_task_definition.app"] == [{"attribute": "cpu", "before": "512", "after": "1024"}]
    assert by["aws_ecs_service.app"][0]["after"] == "(known after apply)"


def test_unknown_after_apply_values_are_labelled():
    plan = {"resource_changes": [rc("aws_ecs_service.app", ["update"], {}, {}, {"task_definition": True})]}
    s = tf.summarize_plan(plan)
    assert s["resources"][0]["changes"][0]["after"] == "(known after apply)"
