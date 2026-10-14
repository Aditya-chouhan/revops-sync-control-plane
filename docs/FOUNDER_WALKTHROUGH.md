# Founder walkthrough: reliable CRM operations in five minutes

Use this when speaking with a founder, VP Sales, RevOps leader or technical hiring manager. It is a **presentation script for the existing synthetic demo**, not evidence of a live deployment or commercial outcome.

## One-sentence positioning

> I built a control plane that makes cross-CRM identity, field ownership and failed-write recovery explicit—so automation can stop unsafe writes instead of silently duplicating or overwriting revenue data.

## Before the call

Install the project once:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Keep these tabs open:

1. this walkthrough;
2. [architecture diagram](../README.md#why-this-project-exists);
3. [demo source](../src/revops_sync/demo.py);
4. [captured execution](HIRING_DEMO_OUTPUT.md);
5. [machine-readable receipt](../evidence/hiring_demo_2026-09-13.json).

Open a terminal at the repository root. Do not describe the simulated CRM transport as a live integration.

## The five-minute talk track

### 0:00–0:35 — Start with the revenue risk

**Say**

> HubSpot and Salesforce integrations usually look simple until two records disagree or a write times out. Then the business risk is duplicate accounts, stale fields, accidental consent changes and sales teams losing trust in CRM data. The hard problem is not making an API call. It is deciding identity, ownership and whether a failed call is safe to retry.

**Show**

The [architecture diagram](../README.md#why-this-project-exists). Point to the identity resolver, field policy, conflict ledger and outbox.

### 0:35–1:05 — Explain the design decision

**Say**

> I put a control plane between the systems. It merges only exact, normalized company domains; isolates records without a reliable identity key; records every disagreement; applies explicit field ownership; and fails safe on consent. Every intended write becomes an auditable, idempotent outbox item.

**Show**

The field-ownership table in the README. Highlight that a consent disagreement resolves to opt-out even when another ownership rule would choose differently.

### 1:05–1:25 — Run the evidence

**Run**

```bash
python -m revops_sync.demo
```

**Say**

> This uses synthetic accounts, a temporary SQLite database and simulated CRM responses. It makes zero external writes. The value of the demo is that the checks run through the real control-plane code and fail the command if an expected safety property does not hold.

### 1:25–2:15 — Prove identity and policy behavior

**Show**

Scenes 1 and 2 in the terminal output.

**Say**

> Two CRM observations share an exact domain, so they resolve to one canonical account. Salesforce wins the account name according to policy, but the restrictive consent value wins regardless of source. The ledger preserves both inputs and the decision. Replayed input creates no new action; stale input is ignored before identity resolution; and an equal-version contradiction is rejected instead of guessed through.

**What this proves**

- deterministic identity behavior;
- explicit ownership and consent policy;
- replay safety for this scenario;
- refusal to hide inconsistent input.

### 2:15–3:25 — Demonstrate timeout-after-success recovery

**Show**

Scene 3 and the simulated `POST → GET` trace.

**Say**

> The simulated CRM accepts a create, but the response is lost. Blindly retrying could create a duplicate. The control plane records the attempt, performs read-back using the canonical stamp, verifies identity and intended fields, and reconciles the item without issuing a second write. It also leaves an ordering hold because matching read-back does not prove that no old request remains in flight.

**What this proves**

- ambiguous outcomes are not treated as simple failures;
- read-back reconciliation can prevent a blind duplicate in the demonstrated case;
- the system distinguishes local acknowledgement from provider settlement.

### 3:25–4:15 — Show restart safety and operator recovery

**Show**

Scenes 4 and 5 and the final `PATCH`.

**Say**

> After reopening the database, replaying the acknowledged item produces no request. A newer update remains blocked behind the uncertain older operation. Releasing that hold requires an explicit operator attestation that is written to the audit trail. The queued create is then rebound to the confirmed CRM ID and dispatched as an update, while the original staged intent and checksum remain preserved.

**What this proves**

- recovery state survives a process restart;
- newer values cannot silently overtake an uncertain older operation in this serial scenario;
- manual intervention is explicit and auditable;
- create-to-update target binding avoids a second create.

### 4:15–4:45 — State the boundaries before being asked

**Say**

> This is software evidence, not production evidence. The CRM responses, accounts and operator attestation are synthetic. The walkthrough is serial SQLite execution and makes no revenue, adoption or time-saved claim. A real implementation would still require an authorized CRM sandbox, signed webhooks, provider-specific conditional-write controls, concurrent-ingestion validation, monitoring and an operator runbook.

**Show**

The receipt’s `boundaries` and `real_external_writes: 0` fields.

### 4:45–5:00 — Translate it to the company

**Say**

> The reusable skill here is not this exact schema. It is designing revenue automation so identity, ownership, delivery uncertainty and recovery are inspectable. I would start by mapping your systems of record and the failure that currently costs the team the most trust or manual work.

Then ask one relevant question:

- **Founder:** “Which GTM handoff still depends on someone checking two tools manually?”
- **VP Sales:** “Where do reps most often distrust or override CRM data?”
- **RevOps:** “Which object or field has the most dangerous ownership ambiguity?”
- **Technical leader:** “What delivery guarantees and webhook-ordering assumptions exist today?”

## If they ask for proof

Use this order:

1. [Captured demo output](HIRING_DEMO_OUTPUT.md) — fastest inspection;
2. [JSON receipt](../evidence/hiring_demo_2026-09-13.json) — exact inputs, states and boundaries;
3. [Walkthrough regression tests](../tests/test_hiring_demo.py) — automated assertions;
4. [Recovery design](RECOVERY.md) and [ordering design](ORDERING.md) — deeper engineering decisions;
5. [Evidence index](../evidence/README.md) — dated test and static-check receipts.

Never substitute test counts for user or business impact. Say “tested software behavior,” not “production-proven reliability.”

## Two-minute version

> Cross-CRM automation fails when identity is uncertain, fields disagree or a write succeeds after the client times out. I built a control plane that uses conservative identity rules, explicit field ownership, a conflict ledger and an idempotent outbox. In the synthetic walkthrough, a simulated Salesforce create is accepted but its response is lost. Instead of blindly retrying, the system reads back provider state, reconciles the accepted write, blocks newer work behind an uncertainty hold, and requires an audited operator release before rebinding the next create into an update. The run makes zero external writes and does not claim production or commercial results. It demonstrates how I reason about the failure modes that make revenue automation trustworthy.

## Follow-up message

> Thanks for the conversation. Here is the five-minute CRM reliability walkthrough we discussed:  
> https://github.com/Aditya-chouhan/revops-sync-control-plane/blob/main/docs/FOUNDER_WALKTHROUGH.md  
>  
> The runnable demo uses synthetic accounts and simulated CRM responses; the repository includes captured output, tests and explicit limitations. The part most relevant to your team is **[replace with their problem: identity / consent / timeout recovery / ordering]**. I would be happy to map that failure mode against your current GTM stack.

## What would upgrade this from portfolio evidence to deployment evidence

- run against an authorized HubSpot or Salesforce sandbox;
- capture signed webhook receipt, deduplication and out-of-order handling;
- validate concurrent ingestion and worker behavior on PostgreSQL;
- document alerts, SLOs and operator recovery time;
- have an external operator execute the runbook;
- measure baseline error rate, manual-review time and recovery time before and after.

Do not add revenue or productivity claims unless a real operator, system of record and measurement window can support them.
