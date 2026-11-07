"""OpenAI function-calling classification, with a fake client (no network, no API key)."""
import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app import llm

CURRENT = {"desired_count": 1}
HISTORY = [{"role": "user", "content": "scale to 2"}]


def fake_client(message=None, finish="stop", raises=None):
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        if raises:
            raise raises
        return NS(choices=[NS(message=message, finish_reason=finish)])

    return NS(chat=NS(completions=NS(create=create))), seen


def call(name, args=None):
    raw = args if isinstance(args, str) else json.dumps(args or {})
    return NS(type="function", function=NS(name=name, arguments=raw))


def reply_with(*calls, content=None):
    return NS(content=content, tool_calls=list(calls))


def api_error(cls, status):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return cls("boom", response=httpx.Response(status, request=req), body=None)


def test_model_is_forced_to_call_a_function_and_cannot_chat():
    client, seen = fake_client(reply_with(call("propose_changes", {"desired_count": 2})))
    p = llm.compile_instruction(HISTORY, CURRENT, client)
    assert (p.kind, p.data) == ("changes", {"desired_count": 2})
    assert seen["tool_choice"] == "required" and seen["parallel_tool_calls"] is False
    assert {t["function"]["name"] for t in seen["tools"]} == set(llm.KIND_BY_FUNCTION)
    assert seen["messages"][0]["role"] == "system" and seen["messages"][1:] == HISTORY


@pytest.mark.parametrize("fn,args,kind", [
    ("propose_changes", {"task_cpu": 1024}, "changes"),
    ("show_config", {"fields": ["all"]}, "config"),
    ("not_possible", {"reason_code": "networking_or_load_balancer"}, "not_possible"),
    ("out_of_context", {}, "out_of_context"),
    ("ask_clarification", {"question": "Which setting?"}, "clarify"),
])
def test_each_function_maps_to_its_kind(fn, args, kind):
    client, _ = fake_client(reply_with(call(fn, args)))
    p = llm.compile_instruction(HISTORY, CURRENT, client)
    assert p.kind == kind and p.data == args


def test_chatty_text_without_a_function_call_is_never_surfaced():
    client, _ = fake_client(NS(content="Sure! Here is a poem about clouds...", tool_calls=None))
    p = llm.compile_instruction(HISTORY, CURRENT, client)
    assert p.kind == "out_of_context" and p.data == {}


def test_unknown_function_name_is_treated_as_out_of_context():
    client, _ = fake_client(reply_with(call("delete_everything", {"x": 1}), content="ok"))
    assert llm.compile_instruction(HISTORY, CURRENT, client).kind == "out_of_context"


def test_malformed_arguments_raise_clear_error():
    client, _ = fake_client(reply_with(call("propose_changes", "{not json")))
    with pytest.raises(llm.LLMError, match="malformed"):
        llm.compile_instruction(HISTORY, CURRENT, client)


def test_non_object_arguments_rejected():
    client, _ = fake_client(reply_with(call("propose_changes", "[1,2]")))
    with pytest.raises(llm.LLMError, match="unexpected"):
        llm.compile_instruction(HISTORY, CURRENT, client)


@pytest.mark.parametrize("finish,needle", [("length", "cut off"), ("content_filter", "content filter")])
def test_finish_reasons(finish, needle):
    client, _ = fake_client(reply_with(call("out_of_context")), finish=finish)
    with pytest.raises(llm.LLMError, match=needle):
        llm.compile_instruction(HISTORY, CURRENT, client)


@pytest.mark.parametrize("cls,status,needle", [
    (openai.AuthenticationError, 401, "authentication failed"),
    (openai.NotFoundError, 404, "OPENAI_MODEL"),
    (openai.RateLimitError, 429, "quota"),
    (openai.InternalServerError, 500, "500"),
])
def test_api_errors_become_llm_errors(cls, status, needle):
    client, _ = fake_client(raises=api_error(cls, status))
    with pytest.raises(llm.LLMError, match=needle):
        llm.compile_instruction(HISTORY, CURRENT, client)


def test_missing_openai_credentials_give_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="OPENAI_API_KEY"):
        llm.compile_instruction(HISTORY, CURRENT)


def test_model_is_configurable(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert llm.model_name() == llm.DEFAULT_MODEL
    monkeypatch.setenv("OPENAI_MODEL", "my-model")
    client, seen = fake_client(reply_with(call("out_of_context")))
    llm.compile_instruction(HISTORY, CURRENT, client)
    assert seen["model"] == "my-model"
