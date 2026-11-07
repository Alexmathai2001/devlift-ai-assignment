"""FastAPI chat server. Local only.

Flow per instruction: compile (LLM) -> validate (guardrails) -> terraform plan -> WAIT for a human
-> terraform apply of that exact plan -> verify via read-only AWS calls.
"""
import hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from . import aws_read, guardrails, llm, replies, tf

MAX_HISTORY = 12  # messages sent to the model; bounds cost and limits how far a bad turn can steer it

ROOT = Path(__file__).resolve().parents[1]
AUDIT_LOG = Path(os.environ.get("CHATOPS_AUDIT_LOG", ROOT / "audit.log"))
TOKEN = os.environ.get("CHATOPS_TOKEN") or secrets.token_urlsafe(24)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "testserver"}

app = FastAPI(title="ECS ChatOps", docs_url=None, redoc_url=None, openapi_url=None)

sessions: dict[str, list[dict]] = {}
pending: dict | None = None  # at most one unapplied plan at a time


def audit(event: str, **fields) -> None:
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}) + "\n")


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Blocks DNS-rebinding and drive-by requests from other websites.
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-chatops-token", "")
        if not hmac.compare_digest(supplied, TOKEN):
            return JSONResponse({"detail": "missing or bad token"}, status_code=401)
    return await call_next(request)


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)


class ApplyIn(BaseModel):
    pending_id: str


def _discard_pending(reason: str) -> None:
    global pending
    if pending:
        tf.cancel(pending["previous"])
        audit("discarded", pending_id=pending["id"], reason=reason)
        pending = None


@app.get("/", response_class=HTMLResponse)
def index():
    html = (ROOT / "static" / "index.html").read_text()
    return html.replace("__TOKEN__", TOKEN)


@app.get("/api/state")
def state():
    try:
        return {"config": aws_read.current_config(), "status": aws_read.service_status(),
                "pending": pending["id"] if pending else None}
    except Exception as e:  # surface AWS credential problems clearly
        raise HTTPException(502, f"AWS read failed: {e}") from e


@app.post("/api/chat")
def chat(body: ChatIn):
    global pending
    _discard_pending("new instruction")  # an unapplied plan is never carried over silently
    history = sessions.setdefault(body.session_id, [])
    history.append({"role": "user", "content": body.message})
    audit("instruction", session=body.session_id, text=body.message)

    try:
        current = aws_read.current_config()
    except Exception as e:
        history.pop()
        raise HTTPException(502, f"Could not read the live configuration from AWS: {e}") from e

    try:
        proposal = llm.compile_instruction(history[-MAX_HISTORY:], current)
    except llm.LLMError as e:
        history.pop()
        audit("llm_error", error=str(e))
        return {"reply": str(e), "kind": "error"}
    audit("intent", kind=proposal.kind)

    def finish(reply: str, **extra):
        history.append({"role": "assistant", "content": reply})
        return {"reply": reply, **extra}

    # Every reply below is built from fixed templates in replies.py, never from model-written text.
    if proposal.kind == "out_of_context":
        return finish(replies.OUT_OF_CONTEXT, kind="out_of_context")
    if proposal.kind == "not_possible":
        return finish(replies.not_possible(proposal.data), kind="not_possible")
    if proposal.kind == "clarify":
        return finish(replies.clarify(proposal.data), kind="clarify")
    if proposal.kind == "config":
        return finish(replies.config(current, proposal.data), kind="text")

    changes, errors, warnings = guardrails.validate(proposal.data, current)
    if "image_tag" in changes and not aws_read.image_tag_exists(changes["image_tag"]):
        errors.append(f"image tag '{changes['image_tag']}' does not exist in ECR "
                      f"(available: {', '.join(aws_read.list_image_tags()) or 'none'})")
    audit("proposal", raw=proposal.data, changes=changes, errors=errors)
    if errors:
        return finish(replies.rejected(errors), kind="not_possible", errors=errors)
    if not changes:
        return finish("That is already the current configuration; nothing to change.", kind="text")

    try:
        plan = tf.make_plan(changes)
    except tf.TerraformError as e:
        audit("plan_error", error=str(e))
        return finish(f"Terraform plan failed:\n{e}", kind="error")
    if plan["blocked"]:
        tf.cancel(plan["previous_overrides"])
        audit("plan_blocked", blocked=plan["blocked"])
        return finish(replies.blocked(plan["blocked"]), kind="not_possible")
    if not plan["has_changes"]:
        tf.cancel(plan["previous_overrides"])
        return finish("Terraform found no difference to apply.", kind="text")

    pending = {"id": uuid.uuid4().hex[:12], "previous": plan["previous_overrides"], "changes": changes}
    audit("plan_ready", pending_id=pending["id"], changes=changes,
          resources=[(r["address"], r["actions"]) for r in plan["resources"]])
    return finish(replies.changes(changes, current), kind="plan", pending_id=pending["id"], changes=changes,
                  warnings=warnings, plan=plan["resources"])


@app.post("/api/apply")
def apply(body: ApplyIn):
    global pending
    if not pending or not hmac.compare_digest(pending["id"], body.pending_id):
        raise HTTPException(409, "No matching pending plan (it may have been replaced or cancelled).")
    audit("apply_start", pending_id=pending["id"], changes=pending["changes"])
    try:
        message = tf.apply()
    except tf.TerraformError as e:
        audit("apply_error", error=str(e))
        raise HTTPException(500, f"terraform apply failed:\n{e}") from e
    audit("apply_done", pending_id=pending["id"], message=message)
    pending = None
    return {"ok": True, "message": message}


@app.post("/api/cancel")
def cancel():
    if not pending:
        return {"ok": True, "message": "nothing pending"}
    _discard_pending("user cancelled")
    return {"ok": True, "message": "plan discarded, no changes made"}
