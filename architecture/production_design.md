# Design Document: Recon Production AWS Deployment

## Overview

This document describes the production AWS architecture that lifts the custodian reconciliation demo from a local-file pipeline into a fully managed, event-driven cloud system. The demo's two-layer design — a deterministic Python pipeline (Layer 1) and a Strands Agents SDK agentic resolver (Layer 2) — maps cleanly onto AWS managed services: S3 event notifications trigger an AWS Lambda normalization step, AWS Step Functions orchestrate the end-to-end workflow, Amazon Bedrock AgentCore Runtime hosts the Strands agent with microVM session isolation and consumption-based pricing, and AgentCore Gateway exposes the `@tool` functions as MCP-compatible endpoints backed by real data sources. DynamoDB stores break state with single-digit-millisecond reads; S3 Object Lock provides the WORM audit trail required for SEC 17a-4 compliance; CloudWatch and X-Ray deliver end-to-end observability across every layer.

## Demo → production mapping

The laptop demo consolidates Layer 2 judgment into one tool — `recommend_disposition` in [`code/tools/recommendation.py`](../code/tools/recommendation.py) — with disposition values `recommend_clear`, `recommend_investigate`, and `require_human`. Outputs are `out/agent_recommendations.json` and `out/human_review_queue.json`: **proposals for ops**, not ledger changes.

Production splits the same intent across Gateway tools: evidence (`lookup_security`, `get_recent_trades`, …), `classify_break` + `propose_resolution` for structured reasoning, and threshold-gated `escalate_to_human` for HITL. Dollar and confidence gates apply before a recommend-clear is treated as approved; humans act in the system of record only after `SendTaskSuccess` on the approval callback.


## Architecture

```mermaid
flowchart TB
    subgraph Custodians["Custodian File Drop"]
        CustA["Custodian A\ncustodian_a.csv"]
        CustB["Custodian B\ncustodian_b.csv"]
    end

    subgraph S3Layer["S3 Ingestion Layer"]
        S3Raw["s3://recon-incoming\n/{custodian}/{date}/\n(versioned, never deleted)"]
        S3Norm["s3://recon-normalized\n/positions/{date}/"]
        S3Artifacts["s3://recon-artifacts\n/breaks/{date}/\n(Object Lock - WORM)"]
    end

    subgraph Trigger["Event Trigger"]
        S3Notif["S3 Event Notification\n(ObjectCreated)"]
        EBScheduler["EventBridge Scheduler\nDaily @ custodian SLA"]
    end

    subgraph Orchestration["Step Functions Standard Workflow"]
        direction TB
        SFIngest["State: IngestLambda\n(run pipeline/ingest.py)"]
        SFReconcile["State: ReconcileLambda\n(run pipeline/reconcile.py)"]
        SFAgent["State: AgentCoreInvoke\n(invoke AgentCore Runtime)"]
        SFNotify["State: NotifyOrApprove\n(escalation routing)"]
        SFDLQ["DLQ + Retry\n(exponential backoff)"]
        SFIngest --> SFReconcile --> SFAgent --> SFNotify
        SFIngest -.->|failure| SFDLQ
        SFReconcile -.->|failure| SFDLQ
        SFAgent -.->|failure| SFDLQ
    end

    subgraph AgentLayer["Bedrock AgentCore Runtime (us-west-2)"]
        ACRuntime["AgentCore Runtime\nStrands Agent\n(code/agent.py)\nmicroVM per session\nup to 8h execution"]
        ACGuardrails["Bedrock Guardrails\nPII redaction\nprompt injection defense"]
        ACRuntime --- ACGuardrails
    end

    subgraph GatewayLayer["AgentCore Gateway (MCP endpoint)"]
        ACGateway["AgentCore Gateway\nIAM SigV4 auth\nMCP aggregation"]
        ToolLookup["Lambda: lookup_security\n→ DynamoDB security-master"]
        ToolTrades["Lambda: get_recent_trades\n→ OMS API"]
        ToolCorpAct["Lambda: get_corporate_actions\n→ Corp Action Vendor API"]
        ToolFX["Lambda: get_fx_rate\n→ FX Rate Service"]
        ToolSettle["Lambda: get_settlement_status\n→ OMS API"]
        ToolClassify["Lambda: classify_break"]
        ToolResolve["Lambda: propose_resolution"]
        ToolEscalate["Lambda: escalate_to_human\n→ EventBridge"]
        ACGateway --> ToolLookup
        ACGateway --> ToolTrades
        ACGateway --> ToolCorpAct
        ACGateway --> ToolFX
        ACGateway --> ToolSettle
        ACGateway --> ToolClassify
        ACGateway --> ToolResolve
        ACGateway --> ToolEscalate
    end

    subgraph StateLayer["State & Audit"]
        DDB["DynamoDB: recon-breaks\nPK: security_id\nSK: as_of_date#custodian\nGSI: as_of_date (ops dashboard)\nGSI: break_type (analytics)"]
        DDBSec["DynamoDB: recon-security-master\n(promoted from securities_reference.csv)"]
        DDBStreams["DynamoDB Streams\n→ Lambda → S3 Object Lock\n(immutable audit trail)"]
        DDB --> DDBStreams
    end

    subgraph HITL["Human-in-the-Loop"]
        EB["EventBridge Rule\n(escalation event)"]
        SNS["SNS Topic"]
        Slack["AWS Chatbot\n→ Slack"]
        SN["ServiceNow Ticket"]
        Approval["Approval Response\n→ DynamoDB + Step Functions\nresume execution"]
        EB --> SNS --> Slack
        SNS --> SN
        Slack --> Approval
        SN --> Approval
    end

    subgraph Observability["Observability"]
        CWLogs["CloudWatch Logs\nstructured JSON\nbreak_id + security_id on every line"]
        CWMetrics["CloudWatch Metrics\nBreaksDetected / AutoCleared\nEscalated / TokensConsumed / CostUSD"]
        CWAlarms["CloudWatch Alarms\ncost ceiling / error rate / SLA breach"]
        XRay["X-Ray Tracing\nLambda → AgentCore → Gateway → Tools"]
        ACTraces["AgentCore Session Traces\nagent reasoning audit"]
    end

    CustA -->|SFTP/API| S3Raw
    CustB -->|SFTP/API| S3Raw
    S3Raw --> S3Notif
    S3Notif --> Orchestration
    EBScheduler --> Orchestration
    SFIngest --> S3Norm
    SFReconcile --> DDB
    SFReconcile --> S3Artifacts
    SFAgent --> ACRuntime
    ACRuntime <-->|MCP tool calls| ACGateway
    ACRuntime --> DDB
    ACRuntime --> S3Artifacts
    SFNotify --> EB
    ToolEscalate --> EB
    ToolLookup --> DDBSec
    Orchestration -.->|logs| CWLogs
    ACRuntime -.->|traces| XRay
    ACRuntime -.->|session traces| ACTraces
    DDB -.->|metrics| CWMetrics
```


## Components and Interfaces

### S3 — Ingestion and Artifact Storage

Three purpose-separated buckets handle the file lifecycle:

| Bucket | Purpose | Key Config |
|---|---|---|
| `recon-incoming` | Raw custodian CSV drops | Versioning enabled; lifecycle rule archives to S3 Glacier after 90 days; never deleted |
| `recon-normalized` | Normalized position JSON emitted by IngestLambda | Standard storage; 30-day lifecycle to IA |
| `recon-artifacts` | Break records, agent recommendations, human-review queue | S3 Object Lock (COMPLIANCE mode, 7-year retention) for SEC 17a-4 WORM compliance |

S3 Event Notifications on `recon-incoming` fire an `ObjectCreated` event that triggers the Step Functions workflow. This replaces the demo's manual `uv run python -m code.run` invocation with a fully automated, file-arrival-driven pipeline.

**Rationale**: S3 is the natural landing zone for custodian file drops (SFTP, API push, or email-to-S3 via SES). Versioning ensures no raw file is ever lost. Object Lock on the artifacts bucket satisfies the immutable audit trail requirement without custom code — the WORM guarantee is enforced at the storage layer.

### AWS Lambda — Normalization and Tool Execution

Lambda functions host two distinct workloads:

**Pipeline Lambdas** (IngestLambda, ReconcileLambda): The existing `code/pipeline/ingest.py` and `code/pipeline/reconcile.py` logic runs inside Lambda functions triggered by Step Functions. Each function reads from S3, writes results to DynamoDB and S3, and emits structured CloudWatch logs. Memory: 512 MB; timeout: 5 minutes (well within the 15-minute Lambda limit for the current ~20-row dataset).

**Tool Lambdas** (one per `@tool` function): Each of the eight agent tools — `lookup_security`, `get_recent_trades`, `get_corporate_actions`, `get_settlement_status`, `get_fx_rate`, `classify_break`, `propose_resolution`, `escalate_to_human` — is deployed as a separate Lambda function registered as an AgentCore Gateway target. In production, these call real data sources (OMS API, corporate action vendor, FX rate service). In staging, they return fixture data identical to the demo's `code/fixtures/` JSON files.

**Rationale**: Lambda is the right choice for the normalization step. The transform is simple Python already written; the dataset is ~20 rows/day at current volume. Glue adds schema catalog overhead, a 10-minute minimum billing unit, and a Spark execution model that is overkill for this workload. Lambda cold starts are sub-second for a 512 MB Python function with no VPC attachment. See the Glue vs Lambda Decision section for the full analysis.

### AWS Step Functions — Workflow Orchestration

A Step Functions Standard Workflow orchestrates the five-state pipeline:

```
IngestLambda → ReconcileLambda → AgentCoreInvoke → NotifyOrApprove → Done
```

Each state is a Lambda invocation or direct SDK integration (AgentCore Runtime invoke). Step Functions provides:
- **Retry with exponential backoff** on each state (3 retries, base interval 2s, backoff rate 2.0)
- **Dead-letter queue** for runs that exhaust retries — ops team is alerted via CloudWatch Alarm
- **Human approval step** in `NotifyOrApprove`: when the agent emits escalations, Step Functions pauses execution and waits for a `SendTaskSuccess` callback from the approval surface (Slack or ServiceNow)
- **Execution history** retained for 90 days — every state transition, input, and output is auditable

A daily EventBridge Scheduler rule triggers the workflow at a configurable time after the custodian file SLA window (e.g., 06:00 UTC). The S3 event notification provides an additional trigger for same-day re-runs when a custodian re-drops a corrected file.

**Rationale**: Step Functions Standard Workflow (not Express) is required because the human approval callback can take hours. Express Workflows have a 5-minute maximum duration. Standard Workflows support the `.waitForTaskToken` pattern needed for HITL.

### Amazon Bedrock AgentCore Runtime — Agent Hosting

The Strands agent (`code/agent.py`) is deployed to AgentCore Runtime via direct code deployment (ZIP upload). Key properties:

- **microVM isolation per session**: each reconciliation run gets a dedicated microVM with isolated CPU, memory, and filesystem. Cross-session data contamination is structurally impossible.
- **Extended execution**: supports workloads up to 8 hours — well beyond the 15-minute Lambda limit that would otherwise constrain complex multi-break agent sessions.
- **Consumption-based pricing**: charges only for active processing time, not I/O wait. Since the agent spends most of its time waiting for Bedrock model responses, this is significantly cheaper than a provisioned container.
- **Built-in observability**: AgentCore session traces capture agent reasoning steps, tool invocations, and model interactions — the audit trail the compliance team needs.
- **Framework agnostic**: the existing Strands `Agent` + `BedrockModel` code deploys unchanged. No rewrite required.

The agent is configured with `BedrockModel(model_id="anthropic.claude-sonnet-4-20250514-v1:0", region_name="us-west-2")` and Bedrock Guardrails attached for PII redaction, denied topics, and prompt injection defense.

**Service maturity and fallback path**: AgentCore Runtime and Gateway are recently GA AWS services with a shorter operational track record than the rest of the stack (S3, Lambda, DynamoDB, Step Functions are all 10+ years in production). If AgentCore proves unstable in `us-west-2`, hits a regional GA gap at deploy time, or its consumption pricing drifts past the per-run budget, the named fallback is **self-hosted Strands on ECS Fargate** behind an ALB (IAM SigV4 inbound, CloudWatch + X-Ray trace plumbing wired manually), with a Lambda-Function-URL aggregator standing in for Gateway's MCP endpoint. The trade-off is real (container patching, autoscaling tuning, no native session-trace UI) but bounded — the agent's value lives in the `@tool` surface, so moving hosts is a hosting-layer migration, not a rewrite. The Layer-1-only fallback in `code/run.py` remains the worst-case backstop if neither agent-hosting option is available.

**Reference**: [AgentCore Runtime documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)

### Amazon Bedrock AgentCore Gateway — Tool Exposure

AgentCore Gateway provides a single MCP-compatible endpoint that aggregates all eight tool Lambdas into a unified virtual MCP server. The agent in AgentCore Runtime calls tools through the Gateway endpoint rather than invoking Lambda functions directly.

Key Gateway properties:
- **MCP aggregation mode**: the Gateway combines all Lambda targets into one `tools/list` response. The agent sees a single endpoint and a unified tool catalog.
- **IAM SigV4 authorization**: inbound calls from AgentCore Runtime are authenticated via AWS Signature Version 4. No API keys or OAuth tokens are needed for the agent-to-gateway leg.
- **Target-level auth**: each Lambda target uses the Gateway's execution role. For OpenAPI targets (OMS API, FX rate service), an AgentCore Credential Provider stores the API key or OAuth credentials.
- **Staging vs production**: the same Gateway configuration is used in both environments. In staging, the Lambda targets return fixture data. In production, they call real systems. The agent code is identical in both environments.

**Reference**: [AgentCore Gateway core concepts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-core-concepts.html)

### DynamoDB — Break State and Security Master

Two tables:

**`recon-breaks`**

Key design follows the access patterns, not the identity. The two hot paths are (1) the agent asking *"everything I know about SEC0001 across the last N days"* and (2) the ops dashboard asking *"all of today's breaks"*. Both must be single-digit-ms `Query` operations, not scans.

- **Partition key:** `security_id` (or the literal `"__ambiguous__"` sentinel for `identifier_ambiguous` breaks where the resolver returned `None`). Serves the agent's hot path in single-digit-ms reads; sets the latency floor for per-break tool calls in Layer 2.
- **Sort key:** `as_of_date#custodian` — composite, `#`-delimited (the canonical DynamoDB idiom for composite SKs). Supports prefix queries like *"all SEC0001 entries on 2026-01-02 across both custodians"* via `begins_with(SK, "2026-01-02#")`.
- **Non-key attribute `break_id`:** the stable SHA-256 hash of `(as_of_date, security_id, custodian)` emitted by the Reconciler. Still used for correlation against `out/raw_breaks.json` and for the §Correctness Properties Property 7 idempotency guarantee — re-runs write to the same `(PK, SK)` item and update in place, so duplicates are still structurally impossible.
- **Ambiguous-break edge case:** when `security_id is None`, the Reconciler writes with `PK="__ambiguous__"` and `SK=as_of_date#custodian#sha256(raw_query)[:12]`. The extra `sha256(raw_query)[:12]` suffix on the SK keeps multiple ambiguous rows on the same day distinguishable (the case-study fixture has two — `"Alphabet Inc"` and `"Berkshire Hathaway Class A Inc"`). Ambiguous breaks remain queryable by date via the `as_of_date-index` GSI without polluting the resolved-security partition space.
- **GSI `as_of_date-index`:** PK=`as_of_date`, SK=`security_id`, projection `ALL`. Powers the daily ops dashboard (*"all of today's breaks, sorted by security"*). Single `Query` returns the whole day's break ledger.
- **GSI `break_type-index`:** PK=`break_type`, SK=`as_of_date`, projection `ALL`. Powers class-level analytics (*"`quantity_mismatch` rate over the quarter"*) with a single `Query` plus an SK range filter.
- **Stores:** Break records (from ReconcileLambda), ResolvedBreak records (from AgentCore), human decisions (from approval callbacks).
- **On-demand capacity mode** — traffic is bursty (one daily run) and unpredictable.
- **KMS encryption at rest** with a customer-managed key; key rotation enabled.

**`recon-security-master`**
- Promoted from `securities_reference.csv`
- Updated via a separate pipeline when FIGI/ISIN/CUSIP master is integrated
- The `lookup_security` tool Lambda queries this table instead of a local CSV
- Single-digit-millisecond reads for the agent's identifier resolution calls

DynamoDB Streams on `recon-breaks` feed a Lambda that writes immutable records to `recon-artifacts` S3 with Object Lock. This provides the WORM audit trail without requiring the application to write to S3 directly on every state change.

### EventBridge + SNS — Human-in-the-Loop Notification

When the agent's `escalate_to_human` tool fires, it emits an EventBridge event. An EventBridge rule routes the event to an SNS topic. SNS fans out to:
- **AWS Chatbot → Slack**: ops team receives a structured Slack message with break details, agent reasoning, and an approval/reject button. The button posts back to an API Gateway endpoint that calls `SendTaskSuccess` on the Step Functions execution.
- **ServiceNow**: an SNS subscription creates a ServiceNow incident with the break record attached.

Dollar threshold and confidence threshold are both required before a recommend-clear is approved for booking. The agent only calls `escalate_to_human` when either threshold is not met — this is enforced at the tool boundary, not via prompting.

### CloudWatch + X-Ray — Observability

Every Lambda function and AgentCore Runtime session emits structured JSON logs with `break_id` and `security_id` on every line. Custom CloudWatch metrics are emitted per run:

| Metric | Description |
|---|---|
| `BreaksDetected` | Total breaks emitted by ReconcileLambda |
| `BreaksRecommendClear` | Breaks where the agent recommended clear without HITL routing |
| `BreaksRequireHuman` | Breaks routed to human review |
| `TokensConsumed` | Total Bedrock tokens used per run |
| `CostUSD` | Estimated Bedrock cost per run |

CloudWatch Alarms fire on: cost per run exceeding a ceiling, Lambda error rate above threshold, Step Functions execution failure, and SLA breach (workflow not completed by market open).

X-Ray traces span the full call chain: Step Functions → Lambda → AgentCore Runtime → Gateway → Tool Lambdas. AgentCore session traces provide the agent-specific reasoning audit.

### IAM and Security

- **Least-privilege roles per component**: IngestLambda has read access to `recon-incoming` and write access to `recon-normalized` only. ReconcileLambda has write access to DynamoDB `recon-breaks` and `recon-artifacts`. AgentCore Runtime has invoke access to the Gateway endpoint only.
- **No hardcoded credentials**: all secrets (OMS API key, FX rate service credentials) are stored in AWS Secrets Manager and injected as environment variables at Lambda invocation time.
- **Bedrock Guardrails**: attached to the AgentCore Runtime agent. Configured for PII redaction (position data may contain fund-identifying information), denied topics (no book mutations), and prompt injection defense.
- **VPC endpoints**: S3, DynamoDB, and Bedrock endpoints are configured so all traffic stays on the AWS private network. No data traverses the public internet.
- **KMS encryption**: all S3 buckets and DynamoDB tables use customer-managed KMS keys. Key rotation is enabled.


## Data Flow

Step-by-step from custodian file drop to resolved break:

1. **File arrival**: Custodian A or B drops an EOD CSV to `s3://recon-incoming/{custodian}/{date}/`. S3 versioning records the exact byte content. An `ObjectCreated` S3 Event Notification fires immediately.

2. **Workflow trigger**: The S3 notification triggers the Step Functions Standard Workflow execution. Alternatively, the daily EventBridge Scheduler rule triggers the workflow at the configured SLA window time (handles cases where the file arrived before the scheduler fired).

3. **Ingest (IngestLambda)**: The Lambda function runs `pipeline/ingest.py` logic. It reads the raw CSV from `recon-incoming`, applies all normalization rules (paren negatives, date coercion, ticker-dot preservation), emits `IngestWarning` records, and writes normalized position JSON to `recon-normalized`. Structured logs include `custodian`, `as_of_date`, and row counts.

4. **Reconcile (ReconcileLambda)**: The Lambda function runs `pipeline/reconcile.py` logic. It reads normalized positions from `recon-normalized`, queries `recon-security-master` DynamoDB for identifier resolution, performs the cross-custodian join, and emits `Break` records. Break records are written to DynamoDB `recon-breaks` and to `recon-artifacts` S3 (Object Lock). The `data_quality.json` artifact is also written to `recon-artifacts`.

5. **Agent invocation (AgentCoreInvoke)**: Step Functions invokes the AgentCore Runtime session with the list of `break_id` values from the current run. The Strands agent iterates over each break, calling tools through the AgentCore Gateway MCP endpoint. For each break, the agent calls `lookup_security`, then one or more of `get_recent_trades`, `get_corporate_actions`, `get_fx_rate`, `get_settlement_status` to gather evidence, then `classify_break` and `propose_resolution` (production split of the demo's single `recommend_disposition` tool). If confidence is below threshold or the dollar value exceeds the recommend-clear ceiling, the agent calls `escalate_to_human`.

6. **Tool execution**: Each tool call from the agent hits the AgentCore Gateway endpoint (IAM SigV4 authenticated). The Gateway routes the call to the appropriate Lambda target. In production, `get_recent_trades` calls the OMS API; `get_corporate_actions` calls the corporate action vendor API; `get_fx_rate` calls the FX rate service. `lookup_security` queries DynamoDB `recon-security-master` directly.

7. **State persistence**: The agent writes `ResolvedBreak` and `Escalation` records to DynamoDB `recon-breaks` (updating the existing Break items). DynamoDB Streams picks up the changes and a downstream Lambda writes immutable copies to `recon-artifacts` S3 with Object Lock.

8. **Notification / HITL**: For escalated breaks, the `escalate_to_human` tool emits an EventBridge event. The EventBridge rule routes to SNS → Slack (via AWS Chatbot) and/or ServiceNow. The Step Functions execution pauses at the `NotifyOrApprove` state, waiting for a `SendTaskSuccess` callback. The ops team reviews the break in Slack or ServiceNow, approves or rejects, and the callback resumes the workflow.

9. **Completion**: The Step Functions execution completes. CloudWatch custom metrics are emitted for the run (`BreaksDetected`, `BreaksRecommendClear`, `BreaksRequireHuman`, `TokensConsumed`, `CostUSD`). A CloudWatch Alarm fires if the workflow did not complete before the market-open SLA.


## Glue vs Lambda Decision

**Decision: Lambda for the normalization step.**

| Criterion | AWS Glue ETL | AWS Lambda |
|---|---|---|
| Current dataset size | ~20 rows/day | ~20 rows/day |
| Transform complexity | Simple Python (already written) | Simple Python (already written) |
| Minimum billing unit | 10 minutes (DPU-hour) | 1 ms |
| Cold start | ~2 minutes (Spark cluster spin-up) | <1 second (512 MB Python) |
| Schema evolution | Built-in Data Catalog, Crawler | Manual schema management |
| Job bookmarks | Yes (deduplication across runs) | Must implement manually |
| Code reuse from demo | Requires Glue-specific PySpark wrapper | Direct reuse of `pipeline/ingest.py` |
| Operational overhead | Glue job definitions, IAM, triggers | Lambda function + IAM role |

**Verdict**: Lambda wins at current volume. The transform is already written as plain Python; wrapping it in a Glue PySpark job adds ~50 lines of boilerplate and a 10-minute minimum billing unit for a job that completes in under 5 seconds. The cold start difference alone (2 minutes vs <1 second) would breach the SLA window at low volume.

**When to revisit**: If daily volume exceeds ~10,000 rows, or if schema evolution becomes a recurring operational burden (multiple custodians with diverging schemas), migrate the normalization step to Glue. The Lambda function's interface (reads from S3, writes to S3/DynamoDB) is identical to what a Glue job would consume — the migration is a drop-in replacement at the Step Functions state level. Glue Data Catalog integration also becomes valuable when a FIGI/ISIN/CUSIP master is integrated, as the Crawler can track schema changes across custodian file formats automatically.


## AgentCore Runtime + Gateway Integration

### Agent Deployment

The Strands agent (`code/agent.py`) is deployed to AgentCore Runtime via direct code deployment:

1. Package `code/agent.py`, `code/models.py`, and `requirements.txt` into a ZIP archive.
2. Use the AgentCore CLI (`agentcore deploy --region us-west-2`) or the AWS SDK to create/update the Runtime agent.
3. The Runtime auto-patches the Python runtime and provisions microVMs on demand.


In production, the `@tool` functions are registered as AgentCore Gateway targets rather than being bundled with the agent. The agent calls them through the Gateway MCP endpoint. This separation means tool implementations can be updated independently of the agent code.

### Gateway Configuration

Each `@tool` function is registered as a Lambda target on the AgentCore Gateway:

```
Gateway: recon-tools-gateway
  Inbound auth: IAM SigV4
  Targets:
    - lookup_security      → Lambda: recon-tool-lookup-security
    - get_recent_trades    → Lambda: recon-tool-get-recent-trades
    - get_corporate_actions → Lambda: recon-tool-get-corporate-actions
    - get_settlement_status → Lambda: recon-tool-get-settlement-status
    - get_fx_rate          → Lambda: recon-tool-get-fx-rate
    - classify_break       → Lambda: recon-tool-classify-break
    - propose_resolution   → Lambda: recon-tool-propose-resolution
    - escalate_to_human    → Lambda: recon-tool-escalate-to-human
```

The Gateway aggregates all targets into a single virtual MCP server. The agent calls `tools/list` once at session start and receives all eight tools in a single response. Tool calls are routed by the Gateway to the appropriate Lambda.

### Staging vs Production Tool Behavior

The same Gateway configuration is used in both environments. Environment-specific behavior is controlled by Lambda environment variables:

| Environment variable | Staging value | Production value |
|---|---|---|
| `DATA_SOURCE` | `fixture` | `live` |
| `OMS_API_ENDPOINT` | (unused) | `https://oms.internal/api/v2` |
| `CORP_ACTION_API_ENDPOINT` | (unused) | `https://corpactions.vendor.com/api` |
| `FX_RATE_API_ENDPOINT` | (unused) | `https://fxrates.internal/api` |

When `DATA_SOURCE=fixture`, the tool Lambda reads from the same `code/fixtures/` JSON files used in the demo. When `DATA_SOURCE=live`, it calls the real system. The agent code and Gateway configuration are identical in both environments — only the Lambda environment variables differ.

### Session Lifecycle

Each Step Functions execution that invokes AgentCore creates one Runtime session. The session processes all breaks for that run (typically 10–20 breaks at current volume). The microVM is terminated after the session completes and memory is sanitized. Session traces are retained in CloudWatch for the configured retention period (90 days recommended for compliance).


## Security and Compliance

### IAM Roles (Least Privilege)

| Component | Permissions |
|---|---|
| IngestLambda | `s3:GetObject` on `recon-incoming/*`; `s3:PutObject` on `recon-normalized/*`; `logs:CreateLogGroup`, `logs:PutLogEvents` |
| ReconcileLambda | `s3:GetObject` on `recon-normalized/*`; `s3:PutObject` on `recon-artifacts/*`; `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:Query` on `recon-breaks`; `dynamodb:Query` on `recon-breaks/index/as_of_date-index` and `recon-breaks/index/break_type-index`; `dynamodb:GetItem`, `dynamodb:Query` on `recon-security-master` |
| AgentCore Runtime | `bedrock-agentcore:InvokeGateway` on the Gateway ARN; `bedrock:InvokeModel` on the Claude Sonnet model ARN; `dynamodb:UpdateItem`, `dynamodb:Query` on `recon-breaks` (agent hot-path query by `security_id`); `dynamodb:Query` on `recon-breaks/index/as_of_date-index` and `recon-breaks/index/break_type-index` (evidence-gathering queries by date or break_type) |
| Tool Lambdas | Per-tool: `dynamodb:GetItem` on `recon-security-master` (lookup_security); `secretsmanager:GetSecretValue` for API credentials (trades, corp actions, FX); `events:PutEvents` (escalate_to_human) |
| DynamoDB Streams Lambda | `s3:PutObject` on `recon-artifacts/*` with Object Lock headers |

**GSI ARN gotcha:** DynamoDB Global Secondary Index ARNs are distinct resources in IAM (`table-arn/index/index-name`). A table-level grant does **not** implicitly extend to GSIs — each GSI must be listed explicitly in the role's resource scope. This catches even experienced AWS engineers because the AWS console UI presents GSIs as "part of" the table.

### Secrets Management

All external API credentials (OMS API key, corporate action vendor OAuth client secret, FX rate service API key) are stored in AWS Secrets Manager. Lambda functions retrieve secrets at cold start and cache them for the function lifetime. Secret rotation is configured per vendor SLA.

### Bedrock Guardrails

The AgentCore Runtime agent has a Guardrails configuration attached:
- **PII redaction**: fund names, account numbers, and counterparty identifiers are redacted from model inputs and outputs before logging.
- **Denied topics**: the agent is blocked from discussing book mutations, payment instructions, or trade execution. Enforced at the Guardrails layer, not via system prompt.
- **Prompt injection defense**: input filtering blocks attempts to override the agent's system prompt via break record content (e.g., a custodian file row containing `"Ignore previous instructions"`).

### Audit Trail (SEC 17a-4 WORM Compliance)

The `recon-artifacts` S3 bucket is configured with S3 Object Lock in COMPLIANCE mode with a 7-year retention period. Objects written to this bucket cannot be deleted or overwritten by any user, including the root account. This satisfies the SEC Rule 17a-4(f) requirement for non-rewritable, non-erasable storage of electronic records.

The audit trail includes:
- Raw custodian CSV files (in `recon-incoming` with versioning)
- Normalized position JSON (in `recon-normalized`)
- Break records with ingest warnings (in `recon-artifacts`)
- Resolved break records with agent reasoning and evidence (in `recon-artifacts` via DynamoDB Streams)
- Human decisions with approver identity and timestamp (in `recon-artifacts` via DynamoDB Streams)

### Network Security

All AWS service calls use VPC endpoints:
- S3 Gateway endpoint (no data egress charges, no public internet)
- DynamoDB Gateway endpoint
- Bedrock Interface endpoint (for model invocations from Lambda)
- Secrets Manager Interface endpoint

Lambda functions run in a private VPC subnet with no internet gateway. Outbound calls to external APIs (OMS, corp actions, FX) route through a NAT Gateway with a fixed Elastic IP that can be allowlisted by the vendor.


## Scalability

The 20-positions / 14-breaks volumes used in the §Cost Model and the per-run estimates are the interview-fixture numbers from the upstream demo — not a production target. Real-world scaling reasoning starts at mid-size-asset-manager volume and walks up two orders of magnitude. Every subsection below assumes a **1% break rate** on the incoming position volume (the conservative upper end of the industry-typical 0.1-0.5% range from the Finantrix daily-recon reference; using 1% as the planning number keeps the architecture honest against a noisier custodian feed).

| Tier | Positions/day | Breaks/day | Real-world analog |
|---|---|---|---|
| Production Baseline | 100,000 | 1,000 | Mid-size asset manager |
| Next | 1,000,000 | 10,000 | Large multi-fund family / mid-tier prime broker |
| Upper | 10,000,000 | 100,000 | Global custodian / top-10 prime broker |

### Production Baseline — 100,000 positions/day, 1,000 breaks/day

The reference architecture as drawn handles this tier without service swaps. Per-layer bottleneck check:

- **IngestLambda (per custodian).** 100k CSV rows is ~5 seconds in pandas and ~50 MB resident. Fits comfortably in a 512 MB / 5-minute Lambda. Cold start dominates the wall time.
- **ReconcileLambda.** O(N+M) dict-based join over ~100k positions is <30 seconds and <500 MB resident. Still Lambda territory.
- **DynamoDB writes.** 1,000 break items per run is trivial for on-demand mode — the account-level default of 4,000 WCU/s is barely exercised (<1%).
- **AgentCore Runtime is the bottleneck.** 1,000 breaks at ~10 seconds per break sequential is 2.7 hours — fails the market-open SLA. **Required architectural change:** Step Functions Map state with `maxConcurrency` ~50 invoking parallel AgentCore Runtime sessions; each session handles ~20 breaks; total wall time drops to ~3 minutes. AgentCore's microVM-per-session isolation makes this fan-out safe.
- **Cost.** Bedrock token spend dominates: ~1,000 breaks × ~5,000 tokens/break × Claude Sonnet on-demand pricing ≈ **$30/run**. Infra cost (Lambda, DDB, S3, Step Functions) is rounding error. Daily run cost well under $50 — green-light.

At this tier, structural fixes (FIGI/ISIN at the source) would cut the break population by perhaps 30-50% — worth doing, but the agent handles the residual without any architecture change.

### Next tier — 1,000,000 positions/day, 10,000 breaks/day

Several layers start creaking. Each one has a named AWS-native remediation:

- **IngestLambda hits its ceiling.** 1M CSV rows is ~500 MB resident in pandas and 5-15 seconds processing; at the edge of the 10 GB Lambda memory cap once intermediate data structures inflate. **Inflection point — migrate ingest to AWS Glue Python Shell jobs** (not PySpark). Glue Python Shell is the right tool here: more CPU/memory headroom than Lambda, no Spark cluster cold start, and the existing `pipeline/ingest.py` Python deploys nearly unchanged. Reserve Glue PySpark for the upper tier where partitioned parallelism actually pays.
- **ReconcileLambda.** Same memory pressure, same migration to Glue Python Shell. The dict-based join logic is unchanged; the runtime just has more headroom.
- **DynamoDB.** 10,000 writes per run; on-demand can throttle on burst writes at this volume. Switch to **provisioned capacity with autoscaling** (~30% cheaper at steady state) or pre-warm the table before the daily run. **Security master inflation:** at 1M positions the underlying universe is likely 10k-100k securities (vs ~20 in the demo). DynamoDB lookups stay single-digit-ms, but cache the security master in the Lambda handler as a warmed dict on cold start (~5 MB) to cut tool-call latency.
- **AgentCore.** 10,000 breaks × ~10 seconds sequential = 28 hours, impossible. Map concurrency of ~100 parallel AgentCore sessions completes in ~17 minutes — tight against market open. **Cost optimization at this tier:** Haiku for `classify_break` (~10x cheaper than Sonnet), Sonnet only for `propose_resolution` on the subset that needs deep reasoning. Two-pass agent loop, ~50-70% Bedrock cost reduction depending on the rule-out rate of the cheap first pass.
- **Cost.** Without Haiku optimization: ~$300/run. With Haiku: ~$100-150/run. Still well within daily budget for an institution at this scale.

Multi-region DR becomes important at this tier, not optional. A single-region Bedrock outage at 7am ET costs real money and reputation when a large fund family depends on the run completing before market open.

### Upper tier — 10,000,000 positions/day, 100,000 breaks/day

Above this volume the architecture *shape* changes, not just the parameters:

- **Daily batch DAG no longer fits.** At this volume custodian feeds are typically continuous through the trading day rather than a single EOD drop. Migrate from a Step Functions Standard Workflow batch orchestration to a **streaming model**: Kinesis Data Streams as the ingest backbone, Lambda consumers (or Kinesis Data Firehose to Glue) for normalization, a DynamoDB-based coordination table tracking run-level state. Breaks emit continuously as positions arrive, not at a single 7am batch.
- **Ingest moves to Glue PySpark with auto-scaling DPUs, or EMR Serverless.** Glue Python Shell is no longer enough; the partitioned parallelism that Spark provides starts paying for its cold-start cost. The transform code becomes a few-screen PySpark job — the existing ingest logic translates directly.
- **DynamoDB hot-partition risk.** 100,000 writes/day averages 1.15/s but bursts at file-arrival waves. The `PK=security_id` design (from the corrected key schema in §State Layer) distributes well on the *typical* workload, but on a major corp-action day a single security can generate thousands of breaks across sub-accounts — a classic hot partition. **Mitigation:** write-shard the hot partition by suffixing `PK=security_id#shardN` where N is a hash bucket (8-16 buckets), and query-fan-out at read time. Standard DDB hot-partition pattern.
- **AgentCore economics break.** 100,000 breaks × ~$0.03/break (with Haiku-first optimization) = ~$3,000/day if the agent touches every break. Unsustainable. **The agent stops being the primary actor.** A deterministic rules engine — built from the labeled eval-harness data (see §11 of the README) — must pre-filter 90%+ of breaks. High-confidence settlement-timing breaks auto-clear without LLM involvement. The agent handles only the long tail (~10% = 10,000 breaks/day), reducing Bedrock spend to ~$300-500/day.
- **Multi-region active-active is mandatory, not optional.** A Bedrock regional outage at 7am ET is operationally fatal at this scale — the deferred DR plan (primary `us-west-2`, secondary `us-east-1`, DynamoDB Global Tables, S3 CRR, ~30 min RTO / ~5 min RPO) becomes a hard requirement, with the Layer-1-only fallback the demo's `code/run.py` already implements as the worst-case backstop.
- **The §8 "Honest Framing of Eliminate" from the README applies at this scale.** The only sustainable answer is structural: FIGI/ISIN everywhere, real-time custodian feeds via FIX or vendor APIs, STP from the OMS. These cut the break population by orders of magnitude *before* the agent sees it. The agent stays in the architecture as a long-tail safety net for the irreducible residual exceptions (corp actions, custodian errors, settlement edge cases), not as the main workflow.

### Summary

The reference architecture (Step Functions + Lambda + AgentCore + DynamoDB) scales cleanly through the **baseline tier** with no service swaps. Through the **next tier** it stays intact with named optimizations — Glue Python Shell for ingest, provisioned DynamoDB capacity, Lambda-layer security-master caching, Haiku-for-classification with Sonnet-for-resolution. Above the **upper tier** the orchestration model itself shifts from batch to streaming, the economics force a deterministic rule-based pre-filter, and structural fixes (FIGI/ISIN, STP, real-time feeds) become economically mandatory. The agent stays in the architecture across all three tiers but moves from being the primary actor at baseline to a long-tail safety net at scale.


## Cost Model

All prices are AWS list prices for `us-west-2` as retrieved from the AWS Pricing API. Actual costs will vary based on usage patterns, reserved capacity, and negotiated rates.

### Per-Run Cost Estimate (Current Volume: ~14 breaks/day)

#### Lambda (Normalization + Tool Lambdas)

- **IngestLambda**: 512 MB × 10s = 5 GB-seconds. Cost: 5 × $0.0000166667 = **$0.000083**
- **ReconcileLambda**: 512 MB × 15s = 7.5 GB-seconds. Cost: 7.5 × $0.0000166667 = **$0.000125**
- **Tool Lambdas**: 14 breaks × 5 tool calls/break × 128 MB × 0.5s = 4.5 GB-seconds. Cost: 4.5 × $0.0000166667 = **$0.000075**
- **Lambda requests**: ~80 invocations × $0.0000002 = **$0.000016**
- **Lambda subtotal**: ~**$0.0003/run**

#### Amazon Bedrock (Claude Sonnet 4)

- Input tokens: 14 breaks × 3,000 input tokens/break = 42,000 tokens. Cost: 42,000 / 1,000,000 × $3.00 = **$0.126**
- Output tokens: 14 breaks × 1,500 output tokens/break = 21,000 tokens. Cost: 21,000 / 1,000,000 × $15.00 = **$0.315**
- **Bedrock subtotal**: ~**$0.44/run**

*Note: Bedrock pricing for Claude Sonnet 4 is $3.00/M input tokens and $15.00/M output tokens. Confirm current rates at the [Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) before deployment.*

#### DynamoDB (On-Demand)

- Write request units: ~100 writes/run × $0.000000625 = **$0.0000625**
- Read request units: ~200 reads/run × $0.000000125 = **$0.000025**
- **DynamoDB subtotal**: ~**$0.0001/run**

#### S3

- Storage: ~1 MB/run × $0.023/GB-month = **$0.000023/month** (negligible)
- PUT requests: ~20 × $0.000005 = **$0.0001/run**
- **S3 subtotal**: ~**$0.0001/run**

#### Step Functions

- State transitions: ~10 transitions/run × $0.000025 = **$0.00025/run**

#### AgentCore Runtime

- AgentCore Runtime pricing is consumption-based (charged for active processing time, not I/O wait). At current volume, the agent session processes 14 breaks with ~5 tool calls each. Estimated active processing time: ~30 seconds. Pricing details are available at the [AgentCore pricing page](https://aws.amazon.com/bedrock/agentcore/pricing/); estimated cost is **$0.01–0.05/run** at current volume.

#### Total Per-Run Estimate

| Component | Cost/run |
|---|---|
| Lambda | $0.0003 |
| Bedrock (Claude Sonnet 4) | $0.44 |
| DynamoDB | $0.0001 |
| S3 | $0.0001 |
| Step Functions | $0.00025 |
| AgentCore Runtime | ~$0.03 |
| **Total** | **~$0.47/run** |

**Bedrock model cost dominates at ~94% of total run cost.** This is expected — the agent is the value-generating component. The infrastructure cost (Lambda, DynamoDB, S3, Step Functions) is negligible.

### Monthly Cost Estimate (1 run/day)

- 30 runs/month × $0.47/run = **~$14/month**
- CloudWatch Logs/Metrics: ~$2/month
- Secrets Manager: ~$0.40/month (4 secrets × $0.10)
- KMS: ~$1/month (2 keys × $1/month)
- **Total monthly estimate: ~$18/month**

### Cost at Production Scale (Mid-Size and Large Asset Manager)

The demo numbers above are the interview-fixture volume. This subsection maps the §Scalability tiers onto cost so the *"is this affordable at scale?"* question gets answered alongside the architecture question. Pricing assumptions match the demo subsection: Claude Sonnet on-demand at $3/M input and $15/M output per the [Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/); Claude Haiku at $0.25/M input and $1.25/M output (used in the optimized rows). Per-break token budget held constant across tiers at ~3,000 input + ~1,500 output for Sonnet — matches the demo's per-break assumption.

| Cost Component | Demo (14 breaks) | Mid-Size (1k breaks) | Large (10k breaks) |
|---|---|---|---|
| Compute (Lambda / Glue Python Shell) | $0.0003 | $0.007 | $0.11 |
| Bedrock (Sonnet only) | $0.44 | $31.50 | $315.00 |
| Bedrock (with Haiku-first optimization) | n/a | ~$12-15 | ~$100 |
| DynamoDB | $0.0001 | $0.006 (on-demand) | $0.50 (provisioned + autoscale) |
| S3 | $0.0001 | $0.0005 | $0.01 |
| Step Functions | $0.00025 | $0.025 | $0.25 |
| AgentCore Runtime | ~$0.03 | ~$1 | ~$10 |
| **Total per run (Sonnet only)** | **~$0.47** | **~$33** | **~$325** |
| **Total per run (Haiku optimized)** | **n/a** | **~$13-17** | **~$110** |
| **Per-position cost** | $0.024 | $0.00033 | $0.000325 |
| **Monthly (1 run/day, Haiku optimized)** | **~$14** | **~$400-500** | **~$3,300-3,500** |

#### Mid-Size (100k positions/day, 1k breaks)

- **Bedrock continues to dominate (~95% of total).** Same pattern as the demo, just scaled. The per-break cost is roughly constant (~$0.03 with Sonnet, ~$0.01-0.015 with Haiku-first), so total Bedrock cost scales linearly with break count.
- **No service swaps required.** Lambda still works for ingest and reconcile per the §Scalability analysis (100k rows / ~5s / ~50 MB). DynamoDB stays on on-demand mode. The only new line item is the Step Functions Map state cost ($0.025/run for ~1,000 Map iterations at $0.000025 per transition).
- **Haiku-first cuts the all-in run cost by ~60%.** From ~$33 down to ~$13-17 depending on the classification-vs-resolution split. Annual savings at 1 run/day: ~$5,800.

#### Large (1M positions/day, 10k breaks)

- **Compute migration to Glue Python Shell** (per §Scalability) costs slightly more than Lambda would in absolute terms ($0.11 vs the ~$0.05 Lambda would cost at this volume *if* it had the memory headroom) but stays comfortably under any reasonable threshold. The migration is driven by the memory ceiling, not by cost.
- **DynamoDB switches to provisioned + autoscaling.** Estimate based on ~100 WCU baseline at $0.00013/WCU-hour over a sustained 1-hour daily run window: ~$0.30/day baseline + burst. Provisioned mode runs ~30% cheaper than on-demand at this steady-state throughput.
- **Step Functions Map cost rises with break count** — 10k Map iterations × $0.000025 = $0.25/run. Still trivial in absolute terms.
- **AgentCore Runtime is the only line item with material estimation uncertainty.** Pricing is consumption-based on active processing time; at 10k breaks across ~100 parallel sessions and ~30s active per break, total session-seconds ≈ 5,000. The ~$10/run estimate is conservative; could be half that depending on the actual per-session-second rate (confirm via the [AgentCore pricing page](https://aws.amazon.com/bedrock/agentcore/pricing/) before committing to a budget).
- **Haiku-first becomes economically forcing, not optional.** $315/run without it is borderline acceptable; $100/run with it sits squarely in the "ops team's discretionary budget" zone. Annual savings at 1 run/day: ~$78,000.

#### Upper tier note (10M positions/day, 100k breaks)

At the §Scalability Upper tier, if the agent touches every break the Bedrock bill is ~$3,150/day on Sonnet-only or ~$1,000/day with Haiku-first — either way operationally untenable for a single fund's daily recon (the Haiku-first number is ~$30k/month for one fund). This is exactly why the §Scalability Upper-tier discussion requires a deterministic rules-based pre-filter to remove ~90% of breaks before they ever reach the agent. With that pre-filter the agent processes ~10k breaks/day and the cost shape collapses back to the Large tier above (~$100/run, ~$3,300-3,500/month). The rules-engine cost itself is negligible — it's stateless Lambda over the same DynamoDB break ledger.

#### Pattern observations

1. **Bedrock dominates at every scale** (>90% of total run cost). Infrastructure costs are noise; cost optimization is fundamentally Bedrock optimization (model tiering, prompt caching, token budgeting).
2. **Per-position cost falls with scale** — $0.024/position at demo, $0.00033 at Mid-Size, $0.000325 at Large. Because cost scales with *breaks* (~1% of positions) and per-break cost is roughly constant, larger funds get a structural per-position cost advantage.
3. **The break rate is the dominant cost driver.** Cost is fundamentally `~$0.03 × (break_rate × positions_per_day)`. A custodian-feed improvement that drops the break rate from 1% to 0.5% halves the monthly Bedrock bill at every tier. This is one quantitative reason the §8 "structural fixes" argument from the README is also a cost argument — FIGI/ISIN and STP are not just data-quality improvements, they are line-item cost reductions.

### Cost Optimization Levers

1. **Bedrock Prompt Caching**: if the system prompt and security master context are repeated across breaks in the same session, prompt caching can reduce input token cost by 50–80%.
2. **Haiku for classification**: use Claude Haiku ($0.25/M input, $1.25/M output) for the `classify_break` step. Only escalate to Sonnet for `propose_resolution`. Estimated savings: 40–60% of Bedrock cost.
3. **CloudWatch cost ceiling alarm**: set a CloudWatch Alarm on the `CostUSD` custom metric. If a run exceeds the ceiling (e.g., $5), the alarm fires and the Step Functions execution is stopped. This prevents runaway costs from agent loops.
4. **Reserved capacity for DynamoDB**: at 10x+ volume, switch to provisioned capacity with reserved capacity pricing for ~30% savings.


## Error Handling

| Failure Mode | Detection | Response |
|---|---|---|
| Custodian file not arrived by SLA | CloudWatch Alarm on EventBridge Scheduler execution | Alert ops team; Step Functions execution does not start |
| IngestLambda failure | Step Functions state error | Retry 3× with exponential backoff; DLQ after exhaustion; CloudWatch Alarm |
| ReconcileLambda failure | Step Functions state error | Same retry/DLQ pattern; raw file preserved in `recon-incoming` for manual reprocessing |
| AgentCore Runtime session timeout | Step Functions state error (8h max session) | Retry once; if second failure, escalate all unresolved breaks to human review |
| Tool Lambda failure (transient) | AgentCore Gateway returns error to agent | Agent retries the tool call up to 3× before treating the evidence as unavailable |
| Tool Lambda failure (persistent) | Agent cannot gather evidence | Agent classifies break as `unknown` with `evidence_unavailable` flag; escalates to human |
| Bedrock throttling | `ThrottlingException` from Bedrock | AgentCore Runtime handles retry with backoff; CloudWatch Alarm on sustained throttling |
| DynamoDB write failure | Lambda exception | Step Functions retry; DLQ after exhaustion |
| Human approval timeout | Step Functions heartbeat timeout (configurable, e.g., 24h) | Auto-escalate to senior ops; CloudWatch Alarm |
| Cost ceiling breach | CloudWatch Alarm on `CostUSD` metric | Stop Step Functions execution; alert ops team |


## Testing Strategy

### Unit Testing

The existing `pytest` suite in `code/tests/` runs unchanged against the Lambda function code. Each Lambda function is a thin wrapper around the existing pipeline modules; the core logic is tested at the module level.

### Integration Testing (Staging Environment)

A staging environment mirrors production with one difference: all Tool Lambdas have `DATA_SOURCE=fixture` set, returning the same fixture data as the demo. This allows end-to-end Step Functions executions to run against real AWS infrastructure without calling external systems.

Staging test cases:
- Drop a test custodian CSV to `recon-incoming` and verify the full workflow completes
- Verify DynamoDB `recon-breaks` contains the expected 14 break records
- Verify `recon-artifacts` S3 contains the expected JSON artifacts with Object Lock headers
- Verify an escalation event triggers the SNS notification
- Verify the Step Functions execution resumes after a simulated approval callback

### Property-Based Testing

The `test_envelope_invariant.py` test (already in the suite) verifies that every JSON artifact has the correct `{metadata, data}` envelope. This test runs against the Lambda output artifacts in staging.

### Evaluation Harness (Pre-Production Gate)

Before enabling auto-clear in production, the Strands Evals SDK is used to evaluate the agent against a labeled break set:
- Precision: fraction of auto-cleared breaks that were correctly resolved
- Recall: fraction of correctly-resolvable breaks that were auto-cleared (not unnecessarily escalated)
- Auto-clear is only enabled when precision ≥ 0.95 AND recall ≥ 0.80 on the labeled set

This gate is enforced in the CI/CD pipeline: the deployment step that sets `AUTO_CLEAR_ENABLED=true` requires a passing eval run as a prerequisite.

## Data Models

The production deployment uses the same Pydantic v2 models defined in `code/models.py` for the demo. The key additions for the cloud deployment are DynamoDB item schemas and S3 artifact envelope contracts.

### DynamoDB Item Schema: `recon-breaks`

```python
# DynamoDB item structure for recon-breaks table
# PK: security_id (str, or "__ambiguous__" sentinel when identifier_ambiguous)
# SK: as_of_date#custodian (str, composite; for ambiguous breaks append
#     "#sha256(raw_query)[:12]" to keep multiple ambiguous rows on the
#     same day distinguishable)
# break_id is a non-key attribute — still the stable SHA-256 hash for
# idempotency (Property 7) and correlation with out/raw_breaks.json.
{
    "break_id":           str,   # SHA-256 hash of (as_of_date|security_id|custodian)
    "as_of_date":         str,   # ISO 8601 date
    "security_id":        str | None,
    "custodian":          str,   # "custodian_a" | "custodian_b" | "both"
    "break_type":         str,   # enum per Break model
    "status":             str,   # "raw" | "resolved" | "escalated" | "approved" | "rejected"
    "resolution":         dict | None,   # ResolvedBreak fields when status = "resolved"
    "escalation":         dict | None,   # Escalation fields when status = "escalated"
    "human_decision":     dict | None,   # {approver, decision, timestamp} when status = "approved"/"rejected"
    "run_id":             str,   # Step Functions execution ARN
    "created_at":         str,   # ISO 8601 datetime
    "updated_at":         str,   # ISO 8601 datetime
}
```

### DynamoDB Item Schema: `recon-security-master`

```python
# DynamoDB item structure for recon-security-master table
# PK: security_id (str)
{
    "security_id":    str,   # e.g. "SEC0001"
    "ticker":         str,   # e.g. "AAPL"
    "name":           str,   # e.g. "APPLE INC COMMON STOCK"
    "asset_class":    str,   # e.g. "equity"
    "figi":           str | None,   # Bloomberg FIGI (future)
    "isin":           str | None,   # ISIN (future)
    "cusip":          str | None,   # CUSIP (future)
    "updated_at":     str,   # ISO 8601 datetime
}
```

### S3 Artifact Envelope

All JSON artifacts written to `recon-artifacts` use the same `OutputArtifact` envelope from the demo:

```python
class OutputArtifact(BaseModel):
    metadata: ArtifactMetadata   # ruleset_version, code_commit, input_file_sha256s, as_of_date, generated_at
    data: list[dict]             # list of Break, ResolvedBreak, or Escalation records
```

The `run_id` (Step Functions execution ARN) is added to `ArtifactMetadata` in the production version to link every artifact to its originating workflow execution.

### EventBridge Escalation Event Schema

```python
# EventBridge event emitted by escalate_to_human tool Lambda
{
    "source": "recon.agent",
    "detail-type": "BreakEscalated",
    "detail": {
        "break_id":        str,
        "security_id":     str | None,
        "break_type":      str,
        "agent_reasoning": str,
        "confidence":      float,
        "dollar_value":    float | None,
        "run_id":          str,   # Step Functions execution ARN + task token
        "task_token":      str,   # Step Functions .waitForTaskToken value
    }
}
```

The `task_token` is passed through to the approval surface (Slack/ServiceNow) so the approval callback can call `SendTaskSuccess` on the correct Step Functions execution.

## Correctness Properties

These properties must hold for every production run. They are verifiable via CloudWatch metrics, DynamoDB queries, and S3 artifact inspection.

### Property 1: Break Completeness

Every break emitted by ReconcileLambda has a corresponding DynamoDB item in `recon-breaks` with `status` in `{raw, resolved, escalated, approved, rejected}` by the time the Step Functions execution completes.

### Property 2: Artifact Immutability

Every item written to `recon-artifacts` S3 has Object Lock headers set. No item in `recon-artifacts` can be deleted or overwritten within the retention period. Verifiable via `s3:GetObjectRetention` on any artifact object.

### Property 3: Audit Completeness

For every break with `status = resolved`, the DynamoDB item contains a non-null `resolution` field with `classification`, `evidence`, `confidence`, and `proposed_resolution`. For every break with `status = escalated`, the item contains a non-null `escalation` field with `agent_reasoning`.

### Property 4: No Silent Auto-Clear

A break is only set to `status = resolved` (auto-cleared) when the agent's `propose_resolution` tool returns `confidence >= AUTO_CLEAR_CONFIDENCE_THRESHOLD` AND `dollar_value <= AUTO_CLEAR_DOLLAR_THRESHOLD`. Both thresholds are required. This is enforced at the tool boundary in `propose_resolution` Lambda, not via prompting.

### Property 5: HITL Callback Integrity

Every Step Functions execution that reaches the `NotifyOrApprove` state with escalations must receive a `SendTaskSuccess` or `SendTaskFailure` callback before the execution can complete. The execution cannot complete in a terminal state with unresolved escalations.

### Property 6: Cost Ceiling Enforcement

No single Step Functions execution emits a `CostUSD` metric value exceeding the configured ceiling. The CloudWatch Alarm on `CostUSD` fires before the ceiling is breached (alarm threshold set at 80% of ceiling).

### Property 7: Idempotency

Re-running the Step Functions workflow for the same `as_of_date` and custodian file (same S3 object version) produces the same set of `break_id` values. Break IDs are stable hashes of `(as_of_date, security_id, custodian)` — re-runs update existing DynamoDB items rather than creating duplicates.



| Dependency | Purpose | Notes |
|---|---|---|
| Amazon Bedrock (Claude Sonnet 4) | Agent foundation model | `us-west-2`; model access must be enabled in Bedrock console |
| Amazon Bedrock AgentCore Runtime | Agent hosting | New service; confirm GA availability and pricing before deployment |
| Amazon Bedrock AgentCore Gateway | Tool MCP endpoint | New service; confirm GA availability before deployment |
| Amazon Bedrock Guardrails | PII redaction, safety | Configure before first production run |
| AWS Step Functions | Workflow orchestration | Standard Workflow (not Express) for HITL support |
| AWS Lambda | Normalization + tool execution | Python 3.13 runtime |
| Amazon DynamoDB | Break state + security master | On-demand capacity mode |
| Amazon S3 | File storage + audit trail | Object Lock requires bucket creation with lock enabled (cannot be added later) |
| Amazon EventBridge | Scheduling + escalation routing | Scheduler for daily trigger; Rules for escalation fan-out |
| Amazon SNS | Notification fan-out | Slack (via AWS Chatbot) + ServiceNow |
| AWS Chatbot | Slack integration | Requires Slack workspace admin approval |
| Amazon CloudWatch | Logs, metrics, alarms | Custom metrics namespace: `Recon/Production` |
| AWS X-Ray | Distributed tracing | Active tracing on all Lambda functions |
| AWS Secrets Manager | API credential storage | One secret per external system |
| AWS KMS | Encryption at rest | Customer-managed keys for S3 and DynamoDB |
| OMS API | Trade history for `get_recent_trades` | Real system; staging uses fixtures |
| Corporate Action Vendor API | Corp actions for `get_corporate_actions` | Real system; staging uses fixtures |
| FX Rate Service | FX rates for `get_fx_rate` | Real system; staging uses fixtures |

