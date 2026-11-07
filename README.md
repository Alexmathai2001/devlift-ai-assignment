# Health API on AWS ECS 

A small health-check API, packaged as a Docker image, pushed to **Amazon ECR**, and
hosted on **Amazon ECS** behind an Application Load Balancer. All AWS
infrastructure is defined with **Terraform**.

> This README is a running log. Each step records what was done, why, and the
> concepts involved. Steps marked ✅ are complete; ⏳ are planned.

---

## Architecture

```
 You ──docker build──▶ Image ──docker push──▶ ECR (image registry)
                                                 │
 Terraform ──creates──▶ VPC + ALB + ECS Cluster + Service ──pulls image──┘
                                   │
 Internet ──▶ ALB ──▶ Fargate task (your container) ──▶ GET /health → 200 OK
```

The health endpoint is used twice: by you (`curl`), and by the ALB and ECS to decide
whether a container is healthy and should keep receiving traffic.

## Glossary

| Term | Meaning | Role here |
|---|---|---|
| **Docker image** | Read-only package of the app, runtime and dependencies | The artifact we deploy |
| **Container** | A running instance of an image | What ECS runs |
| **ECR** | Elastic Container Registry, AWS's private Docker registry | Stores the image |
| **ECS** | Elastic Container Service, AWS's container orchestrator | Runs and restarts containers |
| **Fargate** | Serverless compute for ECS, so there are no servers to manage | Our launch type |
| **Task definition** | Blueprint for a container: image, CPU/memory, ports, env vars, logs | Describes how to run the app |
| **Task** | One running copy of a task definition | The live container |
| **Service** | Keeps N tasks running and replaces unhealthy ones | Self-healing |
| **Cluster** | Logical group of tasks and services | The ECS "home" |
| **VPC / subnets** | Private network in AWS, split into public and private zones | Network isolation |
| **Security group** | Stateful firewall attached to resources | ALB allows :80 from the internet; task allows its port only from the ALB |
| **ALB** | Application Load Balancer: spreads HTTP traffic and runs health checks | Public entry point |
| **Target group** | The set of tasks an ALB routes to | Where the ALB health check is configured |
| **IAM role** | Permissions assumed by a service, with no passwords | Task execution role lets ECS pull from ECR and write logs |
| **CloudWatch Logs** | AWS log storage | Receives container stdout |
| **Terraform** | Infrastructure as code: declare desired state, it reconciles AWS | Builds all of the above |
| **Terraform state** | Terraform's record of the resources it created | Local first, remote (S3) later |
| **Region** | A geographic AWS location; resources live in one region | `eu-north-1` |
| **Temporary credentials** | Short-lived, auto-refreshing credentials | What `aws login` issues |

---

## Plan

1. ✅ Tooling: install AWS CLI and Terraform, authenticate
2. ✅ App: Spring Boot service with `GET /health`
3. ✅ Dockerize: multi-stage Dockerfile, test locally
4. ✅ Terraform layer 1: ECR repository, then build and push the image
5. ✅ Terraform layer 2: VPC, ALB, ECS cluster/service, IAM roles, log group
6. ✅ Deploy and verify with `curl http://<alb-dns>/health`
7. ✅ ChatOps: a local chat UI that turns plain-English instructions into reviewed Terraform changes (see [Step 7](#step-8-chatops-chat-to-terraform-))

### Design decisions

- **Public subnets.** Tasks get public IPs. This avoids about
  $32/month for a NAT gateway and is fine for an assignment. The production pattern
  is private subnets plus NAT or VPC endpoints.
- **Two-layer Terraform.** ECR is created first, the image is pushed, and only then is
  the ECS service created. ECS can't start a service whose image doesn't exist yet.
- **Java 21 / Spring Boot**, as requested. JDK 21 and Maven were already installed.

---

## Step 1: Tooling and authentication ✅

### 1.1 Environment check

Before installing anything, the machine was inspected:

| Tool | Status |
|---|---|
| Docker 29.9.2 (daemon running) | already installed |
| Python 3.12.3, Node 22 | already installed |
| AWS CLI | **missing** |
| Terraform | **missing** |

### 1.2 Install Terraform and AWS CLI (no sudo)

Both were installed under `~/.local` (which was already on `PATH`), so root access
wasn't needed.

```bash
# Terraform 1.17.1: download, verify checksum, install
curl -fsSLO https://releases.hashicorp.com/terraform/1.17.1/terraform_1.17.1_linux_amd64.zip
curl -fsSLO https://releases.hashicorp.com/terraform/1.17.1/terraform_1.17.1_SHA256SUMS
grep linux_amd64.zip terraform_1.17.1_SHA256SUMS | sha256sum -c -     # must print OK
unzip -o terraform_1.17.1_linux_amd64.zip terraform -d ~/.local/bin

# AWS CLI v2
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o awscliv2.zip
unzip -q awscliv2.zip
./aws/install -i ~/.local/aws-cli -b ~/.local/bin --update
```

Verify:

```bash
terraform version   # Terraform v1.17.1
aws --version       # aws-cli/2.39.5
```

> **Concept:** the AWS CLI sends commands to AWS. Terraform reads `.tf` files and
> creates or changes AWS resources to match them. Terraform reuses the AWS CLI's
> credentials, so configuring them once covers both.

### 1.3 Authenticate to AWS

The admin supplied only an **IAM username and password**. Those work for the web
console, not the CLI, which needs programmatic credentials. There were two routes:

| Option | How | Notes |
|---|---|---|
| **A: `aws login`** (used) | Browser-based sign-in with the console username/password; the CLI receives temporary credentials | Needs the `signin:AuthorizeOAuth2Access` permission (managed policy `AWSSignInLocalDevelopmentAccess`) |
| B: access key | Console → *Security credentials* → *Create access key*, then `aws configure` | Long-lived secret on disk; may be blocked by the admin |

```bash
aws login --remote
```

Credentials are temporary, so they expire and refresh automatically. No long-lived
secret is stored. **If commands start failing with expired-token errors, run
`aws login` again.**

### 1.4 Fix the region

`aws login` saved the region as `eu-north`, which is not a valid name, and the CLI
failed with:

```
Could not connect to the endpoint URL: "https://sts.eu-north.amazonaws.com/"
```

The valid name is `eu-north-1`. The credentials were first proven good by passing
`--region` explicitly, then the config was fixed:

```bash
aws sts get-caller-identity --region ap-south-1   # proves credentials work
aws configure set region eu-north-1               # persist the correct region
```

### 1.5 Verification

```bash
aws sts get-caller-identity
```

```json
{
    "UserId": "<USER_ID>",
    "Account": "<ACCOUNT_ID>",
    "Arn": "arn:aws:iam::<ACCOUNT_ID>:user/<IAM_USER>"
}
```

Read-only access to ECR and ECS was also confirmed (`aws ecr describe-repositories`
and `aws ecs list-clusters` both return empty lists, so nothing exists yet).

> **Not yet verified:** permission to *create* resources (IAM roles, VPC, ALB). That
> will only surface during `terraform apply`. An `AccessDenied` there means a missing
> permission to request from the admin.

---

## Step 2: App (Java / Spring Boot) ✅

The app is a minimal Spring Boot 3.5.16 service on Java 21, built with Maven. Spring
Boot 3.5.16 was the newest stable release. The newest overall was a 4.2 release
candidate, which was avoided.

```
app/
├── pom.xml                                   # build config + dependencies
└── src/main/
    ├── java/com/example/health/
    │   ├── HealthApiApplication.java         # entry point
    │   └── HealthController.java             # GET /health
    └── resources/application.properties      # port 8080
```

| File | Purpose |
|---|---|
| `pom.xml` | Maven build file. It inherits from `spring-boot-starter-parent`, which pins compatible dependency versions. The only dependency is `spring-boot-starter-web` (embedded Tomcat plus the REST support). `finalName=app` makes the output `target/app.jar`. |
| `HealthApiApplication.java` | `@SpringBootApplication` starts the app and the embedded web server. |
| `HealthController.java` | `@GetMapping("/health")` returns `{"status":"UP"}` with HTTP 200. |
| `application.properties` | Listens on port **8080**. The container, ALB target group and security group must all use this port. |

> **Concept:** the build produces a *fat jar* (`app.jar`), a single file that bundles the
> app, its libraries and the web server. Running `java -jar app.jar` is all it takes,
> which makes it easy to put in a container.

### Local test

```bash
cd app
mvn -q package -DskipTests
java -jar target/app.jar
curl -i localhost:8080/health
```

Result:

```
GET  /health -> HTTP 200   {"status":"UP"}
POST /health -> HTTP 405   (only GET is allowed)
```

> **Concept:** *health check.* The load balancer calls this endpoint every few seconds.
> A 200 means the container is healthy. Repeated failures make the ALB stop sending it
> traffic and make ECS replace the task.

A root `.gitignore` was also added to keep Terraform state, `.terraform/`, `target/`,
`.env` and key files out of git.

## Step 3: Dockerize ✅

Two files were added in `app/`:

**`Dockerfile`**, a multi-stage build:

| Stage | Base image | What it does |
|---|---|---|
| `build` | `maven:3.9-eclipse-temurin-21` | Copies `pom.xml` first and downloads dependencies (cached layer), then copies `src/` and runs `mvn package` to produce `app.jar` |
| runtime | `eclipse-temurin:21-jre-alpine` | Copies *only* `app.jar` from the build stage, creates a non-root user `app`, exposes 8080 and starts `java -jar` |

**`.dockerignore`** keeps `target/`, `.git`, IDE files and docs out of the build context.

> **Concepts**
> - **Multi-stage build:** Maven and the full JDK exist only in stage 1 and never reach
>   the final image, so the image is smaller and has less attack surface. It also means
>   the build doesn't depend on tools installed on the host.
> - **Layer caching:** each Dockerfile instruction is a cached layer. Copying `pom.xml`
>   before `src/` means dependencies are re-downloaded only when `pom.xml` changes.
> - **Non-root user:** if the app is compromised, the attacker doesn't get root inside
>   the container.
> - **`EXPOSE 8080`:** documentation only. The port is really opened by the task
>   definition and security group later.
> - **`-XX:MaxRAMPercentage=75`:** makes the JVM size its heap from the container's
>   memory limit (Fargate task memory) instead of the host's.

### Local test

```bash
cd app
docker build -t health-api:local .
docker run -d --rm --name health-test -p 8080:8080 health-api:local
curl -i localhost:8080/health
docker exec health-test id        # confirm non-root
docker stop health-test
```

Result: image size **229 MB**, `GET /health -> HTTP 200 {"status":"UP"}`, and the process
runs as `uid=100(app)`, not root.

> **CPU architecture:** the image was built on `x86_64`, which matches Fargate's default
> (`X86_64`). Building on an ARM machine (e.g. Apple Silicon) would need
> `--platform linux/amd64` or the task set to `ARM64`.

## Step 4: Terraform layer 1 (ECR) and image push ✅

### 4.1 Terraform files (`terraform/ecr/`)

| File | Contents |
|---|---|
| `main.tf` | `terraform` block (version ≥ 1.5, AWS provider `~> 6.0`), the `provider "aws"` block with `default_tags`, the ECR repository and a lifecycle policy |
| `variables.tf` | `aws_region` (`eu-north-1`), `repo_name` (`health-api`), `max_images` (5) |
| `outputs.tf` | `repository_url` and `registry` |

ECR repository settings and why:

| Setting | Value | Reason |
|---|---|---|
| `image_tag_mutability` | `IMMUTABLE` | A tag such as `v1` can never be overwritten, so deploys and rollbacks are reproducible. Every push needs a new tag. |
| `scan_on_push` | `true` | Free basic CVE scan on each push |
| encryption | `AES256` | Encrypted at rest |
| lifecycle policy | keep newest 5 | Old images are expired automatically to limit storage cost |
| `force_delete` | `true` | Lets `terraform destroy` delete a non-empty repo. Convenient for an assignment, not for production. |

### 4.2 Terraform workflow

```bash
cd terraform/ecr
terraform init                  # downloads AWS provider v6.72.0, writes .terraform.lock.hcl
terraform validate
terraform plan -out=tfplan      # dry run: Plan: 2 to add, 0 to change, 0 to destroy
terraform apply tfplan          # applies exactly the reviewed plan
```

- `.terraform.lock.hcl` pins the provider version and **should be committed**.
- `*tfplan*` is in `.gitignore`. Saved plans can contain sensitive values.
- State is **local** (`terraform.tfstate`, git-ignored). Production would use an S3
  backend with locking.

Apply result: `2 added, 0 changed, 0 destroyed`. This also confirmed that the IAM user
has permission to create ECR repositories.

```
registry       = "<ACCOUNT_ID>.dkr.ecr.eu-north-1.amazonaws.com"
repository_url = "<ACCOUNT_ID>.dkr.ecr.eu-north-1.amazonaws.com/health-api"
```

### 4.3 Push the image

```bash
cd terraform/ecr
REG=$(terraform output -raw registry)
URL=$(terraform output -raw repository_url)

aws ecr get-login-password | docker login --username AWS --password-stdin $REG
docker tag health-api:local $URL:v1
docker push $URL:v1
```

1. `get-login-password` swaps the IAM credentials for a Docker auth token, valid for
   **12 hours**. `--password-stdin` keeps it out of shell history.
2. `docker tag` adds a second name for the same image. The registry hostname in the
   name tells Docker where to push.
3. `docker push` uploads only the layers ECR doesn't already have.

Verified in ECR:

```bash
aws ecr describe-images --repository-name health-api
```

```
tag:    v1
digest: sha256:f21eb9a7e2341e862584899a220a5eed331628f14a5147355a3a36c6f433e229
size:   ~93 MB (compressed)
```

> **Concept:** the local image shows 229 MB and ECR shows ~93 MB because the registry
> stores *compressed* layers. A **digest** (`sha256:...`) identifies the exact image
> content and is immutable, while a **tag** is just a label pointing at a digest.

## Step 5: Terraform layer 2 (networking, ALB, ECS) ✅

All files are in `terraform/ecs/`. It is a separate Terraform root with its own state, so
the registry (layer 1) survives a `destroy` of this layer.

| File | Resources |
|---|---|
| `main.tf` | Provider; `data` lookups for the ECR repo and Availability Zones |
| `network.tf` | VPC `10.0.0.0/16`, internet gateway, 2 public subnets (2 AZs), route table `0.0.0.0/0 → IGW` |
| `security.tf` | `alb-sg` (:80 from anywhere) and `task-sg` (:8080 **only from `alb-sg`**) |
| `alb.tf` | ALB, target group (`ip` targets, health check `GET /health` expecting 200), HTTP :80 listener |
| `iam.tf` | Task **execution** role plus `AmazonECSTaskExecutionRolePolicy` |
| `ecs.tf` | CloudWatch log group (7-day retention), cluster, task definition, service |
| `variables.tf` / `outputs.tf` | Image tag, port, sizing; ALB URL, image, log group |

### Key concepts

- **Public subnet:** a subnet is public only because its route table sends `0.0.0.0/0`
  to an internet gateway. Two subnets in two AZs are required by the ALB.
- **Security group chaining:** `task-sg` allows port 8080 *from the ALB's security group*
  rather than from an IP range. The task has a public IP, but nothing except the ALB
  can connect to it.
- **Target group `ip` type:** Fargate tasks (`awsvpc` mode) each have their own network
  interface and IP, and ECS registers them automatically.
- **Task execution role vs. task role:** the execution role is for the ECS agent (pull from
  ECR, write logs). A task role is for the app's own AWS calls. This app makes none, so
  there is no task role (least privilege).
- **Task definition:** immutable blueprint (image, 0.5 vCPU / 1 GB, port 8080, log
  config). Changes create a new revision. **Service:** keeps 1 task running and
  registered with the ALB.
- **`health_check_grace_period_seconds = 60`:** ALB health failures are ignored while the
  JVM starts, so ECS doesn't kill the task prematurely.
- **`depends_on` the listener:** the target group must be attached to a listener before
  the service registers with it.

### Commands

```bash
cd terraform/ecs
terraform init
terraform validate
terraform plan -out=tfplan      # Plan: 22 to add, 0 to change, 0 to destroy
terraform apply tfplan          # Apply complete! Resources: 22 added
```

The ALB took about 2m47s to provision and everything else was quick. The IAM permissions
(`CreateRole`, `AttachRolePolicy`, `PassRole`) turned out to be available to the user.

### Design trade-offs (production would differ)

| This project | Production |
|---|---|
| Public subnets, task has a public IP, no NAT (saves about $32/month) | Private subnets with NAT gateway or VPC endpoints |
| HTTP :80 only | HTTPS :443 with an ACM certificate and a domain |
| 1 task | 2+ tasks across AZs, with auto scaling |
| Local Terraform state | S3 backend with locking |
| `force_delete` on ECR | Off |

## Step 6: Deploy and verify ✅

Outputs after apply:

```
alb_url    = "http://<alb-dns-name>"
health_url = "http://<alb-dns-name>/health"
image      = "<ACCOUNT_ID>.dkr.ecr.eu-north-1.amazonaws.com/health-api:v1"
log_group  = "/ecs/health-api"
```

Verification, polling the health URL every 10 seconds right after `apply`:

```
try 1: HTTP 000   # ALB DNS not resolving/ready yet
try 2: HTTP 503   # ALB up, no healthy targets yet
try 3: HTTP 503
try 4: HTTP 502   # target registered, container still starting
try 5: HTTP 200   {"status":"UP"}
```

This progression is normal. About 1-2 minutes passed between the apply finishing and the
first 200.

```bash
curl http://<alb-dns>/health
aws ecs describe-services --cluster health-api-cluster --services health-api-service
aws elbv2 describe-target-health --target-group-arn <tg-arn>
```

Service state: `desired 1, running 1, ACTIVE`. Target `10.0.0.163` is `healthy`.

### Debugging cheat sheet

| Symptom | Likely cause | Where to look |
|---|---|---|
| 503 | No healthy targets (yet) | Target group → Targets tab |
| 502 | Target registered but not answering | Container logs (`/ecs/health-api`) |
| `CannotPullContainerError` | No route to ECR or missing execution role | ECS → service → Events / stopped task reason |
| Task restarts in a loop | Health check failing or app crashing | CloudWatch logs, grace period |

### Where to see it in the console (region: Europe (Stockholm) `eu-north-1`)

ECR → Repositories → `health-api`; ECS → Clusters → `health-api-cluster`; EC2 → Load
Balancers → `health-api-alb`; EC2 → Target Groups → `health-api-tg`; CloudWatch → Log
groups → `/ecs/health-api`.

> **Cost warning:** while this stack is running it costs roughly $0.08-0.10 per hour
> (ALB, Fargate task, public IPv4 addresses). Run the teardown in step 7 when finished.

## Step 7: Teardown ⏸ (on hold)

Not performed yet. The stack is intentionally left running until requested. It costs
roughly $0.08-0.10 per hour while up (estimate; check current AWS pricing).

When it is time, **destroy `terraform/ecs` first, then `terraform/ecr`**. The ECS layer
looks up the ECR repo with a `data` source, so destroying ECR first would break the ECS
plan and destroy.

```bash
cd terraform/ecs && terraform destroy
cd ../ecr        && terraform destroy     # optional: removes the repo and its images
```

---

## Step 8: ChatOps, chat to Terraform ✅

A local web chat (`chatops/`, Python FastAPI) where you type an instruction such as
*"scale to 2 tasks"* or *"give it 1 vCPU and 2 GB"*, and the running ECS service is changed
through Terraform, **only after you approve a plan**.

```
instruction ─▶ 1 COMPILE  OpenAI (function calling) fills in Terraform variables, e.g. {desired_count: 2}
            ─▶ 2 VALIDATE guardrails.py: allowed variables, ranges, Fargate CPU/memory pairs
            ─▶ 3 WRITE    terraform/ecs/chatops.auto.tfvars.json
            ─▶ 4 PLAN     terraform plan -out, shown in the chat as a before -> after table
            ─▶ 5 APPROVE  you click Apply (nothing has changed before this)
            ─▶ 6 APPLY    terraform apply of that exact saved plan
            ─▶ 7 VERIFY   live status panel (read-only boto3): running/desired, healthy targets
```

**The model never writes HCL or calls AWS.** It only proposes values for a fixed list of
existing Terraform variables. Terraform stays the source of truth and is the only thing that
changes AWS. boto3 is read-only (current state, ECR tags, status).

### What the chat may change

| Setting (Terraform variable) | Limits |
|---|---|
| `desired_count` | 0-4 (0 stops the app; the UI warns) |
| `task_cpu` / `task_memory` | Valid Fargate pairs only, capped at 2 vCPU |
| `image_tag` | Must already exist in ECR |
| `log_retention_days` | A value CloudWatch accepts |
| `health_check_grace_period_seconds` | 0-600 |
| `deployment_minimum_healthy_percent` / `deployment_maximum_percent` | 0-100 / 100-200 |
| `environment` (add or remove variables) | Valid names; names that look like secrets are refused |

Four of these (grace period, the two deployment percentages, `environment`) were added to
`terraform/ecs` for this step, defaulting to the current values. `terraform plan` still reported
**No changes** afterwards. Anything else (ports, VPC, ALB, IAM, region, deleting resources) is
refused.

### Staying in scope: off-topic and impossible requests

The model is **not allowed to chat**. It must call exactly one of five functions
(`tool_choice="required"`), and every reply you see is built in code from fixed templates
(`app/replies.py`), never from model-written text.

| You ask | Function chosen | What you get |
|---|---|---|
| "scale to 2 tasks" | `propose_changes` | Validation, then a Terraform plan and an Apply/Cancel card |
| "what is the CPU set to?" | `show_config` | The values, rendered from live AWS data |
| "add a second load balancer", "change the port", "delete the VPC" | `not_possible` | `That isn't possible: <fixed reason>` plus the list of what can be changed |
| "scale to 50 tasks", "256 CPU with 8 GB" | `propose_changes`, then rejected by `validate()` | `That isn't possible:` with the exact limit that was broken |
| "make it bigger" | `ask_clarification` | One short question, no plan |
| "write me a poem", "hi", "what's the capital of France?", "ignore your rules" | `out_of_context` | One fixed message: *"That's outside what I can help with. I only handle configuration changes to the health-api ECS service..."* |

If the model returns no recognised function call at all, the reply is the same out-of-context
message, so a misbehaving model cannot make the chat talk about something else.

### Safety layers

1. **Forced function calling + `validate()`**: the model can only classify and fill in values,
   and every value it returns is re-checked in code. Model output is never trusted as-is.
2. **Plan allow-list**: a plan that touches anything other than the ECS service, task
   definition or log group is blocked and never offered for approval. Only the task definition
   may be replaced; nothing else may be deleted.
3. **Human approval**: Apply runs the *exact saved plan* that was shown. Terraform rejects it if
   state changed in between. A new message discards an unapplied plan.
4. **Local only**: binds to `127.0.0.1`, checks the `Host` header, and requires a per-run token
   (embedded in the served page) so another website cannot drive it.
5. **Audit log**: every instruction, proposal, plan and apply goes to `chatops/audit.log`
   (git-ignored).

### Run it

```bash
cd chatops
./.venv/bin/pip install -r requirements.txt            # first time (the venv already exists here)
export OPENAI_API_KEY=...                              # in your own terminal, never in git or chat
export OPENAI_MODEL=...                                # optional; default gpt-4.1; must support function calling
aws login                                              # if your AWS session expired
./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
# open http://127.0.0.1:8000
./.venv/bin/python -m pytest -q                        # 71 tests, no network or API key needed
```

### Verified

- 71 unit/API tests pass (guardrails, plan safety, approval flow, scope enforcement, fixed reply
  templates, OpenAI response parsing and error mapping with a fake client, host/token checks).
  The scope tests mock the model's *choice* of function, so they prove the code handles each
  choice correctly, not that the real model always picks the right one.
- Against the real account (**read-only, nothing applied**): live config and status read
  correctly, and real `terraform plan`s for scaling, CPU/memory and an environment variable were
  produced, summarized and discarded. Afterwards Terraform reported no drift.
- A real request to OpenAI with a deliberately invalid key returned the expected clean
  "authentication failed" message, so the SDK, network path and error handling work.
- **Not yet verified:** how accurately the real model classifies prompts (needs a valid key; try
  an off-topic one, an impossible one and a normal change, and tell me any it gets wrong), a
  successful model call with a valid key (none was available), that the
  default model name is available to your key, and a real approved apply through the UI. Test
  with a harmless change first, such as `log_retention_days`.

### Caveats

- `chatops.auto.tfvars.json` is how the chat's changes persist. It is **not** git-ignored, so
  changes show up in `git diff`; commit it, or the next plain `terraform apply` from a fresh
  checkout reverts to the defaults. Do not put secrets in `environment`.
- Provider: OpenAI (Chat Completions with function calling). The default model `gpt-4.1` could
  not be confirmed against current OpenAI docs; if your key can't use it you get a clear error
  naming `OPENAI_MODEL`. Content-filter and truncated replies are shown as errors.
- Changing CPU, memory, image or environment creates a new task definition and a rolling
  redeploy, so expect a minute or two before the status panel shows healthy.

---

## Security notes

- Never commit credentials. `~/.aws/` lives outside this repo.
- A `.gitignore` will exclude Terraform state (`*.tfstate*`, `.terraform/`), since state
  files can contain sensitive values.
