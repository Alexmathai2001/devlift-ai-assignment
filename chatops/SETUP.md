# ChatOps: local setup

A local web chat where you type plain-text instructions (for example "scale to 2 tasks") and the
app turns them into a **Terraform plan** for the running ECS service. Nothing changes in AWS until
you click **Apply**. It runs only on your machine (`127.0.0.1`).

## What you need first

| Requirement | Why | Check |
|---|---|---|
| Python 3 (developed on 3.12) | Runs the FastAPI app | `python3 --version` |
| Terraform (developed on 1.17.1) | Creates and applies the plan | `terraform version` |
| AWS CLI v2 | Sign-in (`aws login`) and credentials | `aws --version` |
| An OpenAI API key | Turns your sentence into a structured request | your own key, from your OpenAI account |
| The deployed stack and its Terraform state | The chat changes the **existing** ECS service | `terraform/ecs/terraform.tfstate` exists |

> **State matters.** Terraform state is stored as a local file (`terraform/ecs/terraform.tfstate`,
> git-ignored). On a machine without that file, Terraform thinks nothing exists and the plan would
> try to create a second copy of the whole stack. Run this app on the machine that has the state,
> or copy the state files across first.

## 1. Create the Python environment

From the repo root:

```bash
cd chatops
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

`requirements.txt` includes `boto3[crt]`. The `crt` part is required, otherwise boto3 cannot read
credentials created by `aws login`.

## 2. Sign in to AWS

```bash
aws login            # add --remote if no browser opens
aws configure get region      # must print eu-north-1
aws sts get-caller-identity   # must print your account and user
```

If the region prints `eu-north`, fix it with `aws configure set region eu-north-1`.

`aws login` gives **temporary credentials that expire**. When the chat shows
*"Could not read the live configuration from AWS ... Please reauthenticate"*, run `aws login`
again and resend your message. The server normally does not need a restart.

## 3. Initialise Terraform (once per machine)

```bash
cd ../terraform/ecs
terraform init
terraform plan        # expect "No changes" if nothing was changed by hand
cd ../../chatops
```

The app runs `terraform plan` and `terraform apply` in `terraform/ecs` itself. The saved settings
it writes go to `terraform/ecs/chatops.auto.tfvars.json`, which Terraform loads automatically.

## 4. Set your OpenAI key

Do this in **your own terminal**, in the same shell that starts the server. Never put the key in
a file in this repo and never paste it into a chat.

```bash
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL="..."      # optional, default is gpt-4.1
```

`OPENAI_MODEL` must be a model your key can use and that supports function calling. If the chat
says the model was not found, set this variable to one your account has.

## 5. Start the app

```bash
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** in a browser on the same machine.

Keep `--host 127.0.0.1`. The app has no login. It is protected only by being reachable from
localhost, a Host-header check and a random token created on each start, so do not expose it on a
network interface.

## 6. Try it

1. Type `how many tasks are running?`. You get a reply from live AWS data and nothing changes.
2. Type `scale to 2 tasks`. A plan card appears with **Apply** and **Cancel**.
3. Read the plan, then click **Apply** to change AWS, or **Cancel** to discard it.
4. Type `write me a poem`. You get the fixed out-of-context reply.
5. Type `add a second load balancer`. You get "That isn't possible".

The right-hand panel shows the live ECS status and refreshes every 5 seconds.

What the chat can change: task count (0-4), CPU, memory, image tag, log retention, health-check
grace period, deployment min/max percent, and environment variables (no secrets).

## Run the tests

```bash
./.venv/bin/python -m pytest -q
```

The 71 tests need no network, AWS login or OpenAI key.

## Files it creates

| File | What it is |
|---|---|
| `chatops/audit.log` | One line per request, plan, apply and cancel (git-ignored) |
| `terraform/ecs/chatops.auto.tfvars.json` | Settings applied through the chat. Committed on purpose. |
| `terraform/ecs/chatops.tfplan` | The saved plan waiting for Apply, removed after apply or cancel |
