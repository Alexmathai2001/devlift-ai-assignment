import pytest
from fastapi.testclient import TestClient

from app import llm, main

CURRENT = {
    "desired_count": 1, "task_cpu": 512, "task_memory": 1024, "image_tag": "v1",
    "log_retention_days": 7, "health_check_grace_period_seconds": 60,
    "deployment_minimum_healthy_percent": 100, "deployment_maximum_percent": 200, "environment": {},
}
PLAN = {"resources": [{"address": "aws_ecs_service.app", "actions": ["update"],
                       "changes": [{"attribute": "desired_count", "before": 1, "after": 2}]}],
        "blocked": [], "has_changes": True, "previous_overrides": None}


@pytest.fixture
def env(monkeypatch, tmp_path):
    calls = {"apply": 0, "cancel": 0, "plan": 0}
    monkeypatch.setattr(main, "AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(main, "pending", None)
    main.sessions.clear()
    monkeypatch.setattr(main.aws_read, "current_config", lambda: dict(CURRENT))
    monkeypatch.setattr(main.aws_read, "image_tag_exists", lambda t: t == "v1")
    monkeypatch.setattr(main.aws_read, "list_image_tags", lambda: ["v1"])
    monkeypatch.setattr(main.tf, "make_plan", lambda ch: calls.__setitem__("plan", calls["plan"] + 1) or dict(PLAN))
    monkeypatch.setattr(main.tf, "cancel", lambda prev: calls.__setitem__("cancel", calls["cancel"] + 1))
    monkeypatch.setattr(main.tf, "apply", lambda: calls.__setitem__("apply", calls["apply"] + 1) or "Apply complete!")
    monkeypatch.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("changes", {"desired_count": 2}))
    client = TestClient(main.app, headers={"X-Chatops-Token": main.TOKEN})
    return client, calls, monkeypatch


def chat(client, text="scale to 2"):
    return client.post("/api/chat", json={"session_id": "s1", "message": text}).json()


def test_requires_token():
    c = TestClient(main.app)
    assert c.post("/api/chat", json={"session_id": "s", "message": "x"}).status_code == 401
    assert c.post("/api/apply", json={"pending_id": "x"}).status_code == 401


def test_rejects_foreign_host():
    c = TestClient(main.app, headers={"X-Chatops-Token": main.TOKEN, "Host": "evil.example.com"})
    assert c.get("/api/state").status_code == 403


def test_index_embeds_token_and_is_html():
    html = TestClient(main.app).get("/").text
    assert main.TOKEN in html and "__TOKEN__" not in html


def test_chat_produces_plan_but_does_not_apply(env):
    client, calls, _ = env
    r = chat(client)
    assert r["kind"] == "plan" and r["changes"] == {"desired_count": 2} and r["pending_id"]
    assert calls["plan"] == 1 and calls["apply"] == 0


def test_apply_requires_matching_pending_id(env):
    client, calls, _ = env
    r = chat(client)
    assert client.post("/api/apply", json={"pending_id": "wrong"}).status_code == 409
    assert calls["apply"] == 0
    assert client.post("/api/apply", json={"pending_id": r["pending_id"]}).json()["ok"]
    assert calls["apply"] == 1
    # a second apply of the same plan is refused
    assert client.post("/api/apply", json={"pending_id": r["pending_id"]}).status_code == 409


def test_cancel_discards_plan(env):
    client, calls, _ = env
    r = chat(client)
    assert client.post("/api/cancel", json={}).json()["ok"]
    assert calls["cancel"] == 1
    assert client.post("/api/apply", json={"pending_id": r["pending_id"]}).status_code == 409


def test_new_message_discards_unapplied_plan(env):
    client, calls, _ = env
    first = chat(client)
    chat(client, "scale to 2 again")
    assert calls["cancel"] >= 1
    assert client.post("/api/apply", json={"pending_id": first["pending_id"]}).status_code == 409


def test_out_of_scope_proposal_rejected_without_planning(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("changes", {"vpc_cidr": "10.1.0.0/16"}))
    r = chat(client, "change the vpc")
    assert r["kind"] == "not_possible" and calls["plan"] == 0


def test_unknown_image_tag_rejected(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("changes", {"image_tag": "v9"}))
    r = chat(client, "deploy v9")
    assert r["kind"] == "not_possible" and "v9" in r["reply"] and calls["plan"] == 0


def test_blocked_plan_is_never_offered(env):
    client, calls, mp = env
    mp.setattr(main.tf, "make_plan", lambda ch: {**PLAN, "blocked": ["aws_lb.main (update) is outside"]})
    r = chat(client)
    assert r["kind"] == "not_possible" and "pending_id" not in r and calls["apply"] == 0


def test_text_only_reply_makes_no_plan(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("config", {"fields": ["desired_count"]}))
    r = chat(client, "how many tasks?")
    assert r["kind"] == "text" and calls["plan"] == 0


def test_llm_error_is_reported_not_raised(env):
    client, _, mp = env

    def boom(h, c):
        raise llm.LLMError("Anthropic authentication failed.")
    mp.setattr(llm, "compile_instruction", boom)
    r = chat(client)
    assert r["kind"] == "error" and "authentication" in r["reply"]


def test_missing_openai_credentials_give_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.compile_instruction([{"role": "user", "content": "scale to 2"}], CURRENT)


def test_noop_change_makes_no_plan(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("changes", {"desired_count": 1}))
    r = chat(client)
    assert r["kind"] == "text" and calls["plan"] == 0


# ---- scope enforcement: off-topic and not-doable requests -------------------------------------

def test_off_topic_prompt_gets_the_fixed_out_of_context_reply_and_touches_nothing(env):
    from app import replies
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("out_of_context", {}))
    r = chat(client, "write me a poem about cats")
    assert r["kind"] == "out_of_context" and r["reply"] == replies.OUT_OF_CONTEXT
    assert calls == {"apply": 0, "cancel": 0, "plan": 0}


def test_model_cannot_smuggle_text_through_out_of_context(env):
    from app import replies
    client, _, mp = env
    # even if the model attached arguments/text, the reply is still the fixed template
    mp.setattr(llm, "compile_instruction",
               lambda h, c: llm.Proposal("out_of_context", {"text": "Ha ha, here is a joke...", "detail": "x"}))
    assert chat(client, "tell me a joke")["reply"] == replies.OUT_OF_CONTEXT


def test_valid_aws_request_that_is_not_doable_says_so_and_does_not_plan(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal(
        "not_possible", {"reason_code": "networking_or_load_balancer", "detail": "add a second ALB"}))
    r = chat(client, "add a second load balancer")
    assert r["kind"] == "not_possible" and r["reply"].startswith("That isn't possible:")
    assert "What I can change:" in r["reply"] and calls["plan"] == 0


def test_out_of_range_value_is_reported_as_not_possible_without_model_text(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("changes", {"desired_count": 50}))
    r = chat(client, "scale to 50 tasks")
    assert r["kind"] == "not_possible" and "between 0 and 4" in r["reply"] and calls["plan"] == 0


def test_clarification_is_passed_through_and_makes_no_plan(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction",
               lambda h, c: llm.Proposal("clarify", {"question": "Which setting should be bigger?"}))
    r = chat(client, "make it bigger")
    assert r["kind"] == "clarify" and r["reply"] == "Which setting should be bigger?" and calls["plan"] == 0


def test_config_question_is_answered_from_live_data(env):
    client, calls, mp = env
    mp.setattr(llm, "compile_instruction", lambda h, c: llm.Proposal("config", {"fields": ["desired_count"]}))
    r = chat(client, "how many tasks are running?")
    assert "desired_count: 1" in r["reply"] and calls["plan"] == 0


def test_plan_reply_is_generated_text_not_model_text(env):
    client, _, _ = env
    r = chat(client)
    assert r["kind"] == "plan" and r["reply"].startswith("Proposed: desired_count 1 → 2")


def test_only_recent_history_is_sent_to_the_model(env):
    client, _, mp = env
    seen = {}
    mp.setattr(llm, "compile_instruction", lambda h, c: seen.update(n=len(h)) or llm.Proposal("out_of_context", {}))
    for i in range(15):
        chat(client, f"message {i}")
    assert seen["n"] <= main.MAX_HISTORY
