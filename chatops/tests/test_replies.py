from app import replies

CURRENT = {"desired_count": 1, "task_cpu": 512, "environment": {"A": "1"}, "log_retention_days": 7}


def test_out_of_context_is_fixed_and_points_back_to_scope():
    assert "outside what I can help with" in replies.OUT_OF_CONTEXT
    assert "health-api" in replies.OUT_OF_CONTEXT and "scale to 2 tasks" in replies.OUT_OF_CONTEXT


def test_not_possible_uses_template_per_reason_code_and_lists_capabilities():
    text = replies.not_possible({"reason_code": "networking_or_load_balancer"})
    assert text.startswith("That isn't possible:") and "load balancer" in text
    assert "What I can change:" in text and "desired_count" in text


def test_unknown_reason_code_falls_back_to_generic():
    assert "isn't something this tool can do" in replies.not_possible({"reason_code": "made-up"})
    assert "isn't something this tool can do" in replies.not_possible({})


def test_detail_is_single_line_and_length_capped():
    text = replies.not_possible({"reason_code": "other", "detail": "line1\n\nline2 " + "x" * 5000})
    detail = text.split("(", 1)[1].split(")", 1)[0]
    assert "\n" not in detail and len(detail) <= replies.MAX_DETAIL


def test_clarify_is_capped_and_has_a_default():
    assert len(replies.clarify({"question": "q" * 1000})) <= replies.MAX_DETAIL
    assert "say a bit more" in replies.clarify({})


def test_config_all_and_selected_fields_come_from_live_data():
    assert "desired_count: 1" in replies.config(CURRENT, {"fields": ["all"]})
    only = replies.config(CURRENT, {"fields": ["task_cpu"]})
    assert "task_cpu: 512" in only and "desired_count" not in only
    assert "desired_count: 1" in replies.config(CURRENT, {"fields": ["not_a_field"]})  # falls back to all


def test_changes_text_describes_before_and_after():
    text = replies.changes({"desired_count": 2, "environment": {"A": "9", "B": "2"}}, CURRENT)
    assert "desired_count 1 → 2" in text and "+B" in text and "~A" in text and "Apply or Cancel" in text


def test_rejected_and_blocked_are_phrased_as_not_possible():
    assert replies.rejected(["desired_count must be an integer between 0 and 4"]).startswith("That isn't possible:")
    assert replies.blocked(["aws_lb.main would be deleted"]).startswith("That isn't possible:")
