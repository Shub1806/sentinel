# Evaluation

How well does the agent diagnose real incidents, and is it better than a few fixed rules?

**Short answer:** on 18 live incidents across 6 failure types, the agent named the right cause in
**15/18** by the keyword scorer and **11/18** when every answer was checked by hand. It was right every
time on a bad deploy, a database outage, an S3 outage, a slow consumer and a broker outage, and wrong
every time on poison messages. The fixed rules were never wrong, but mostly because they only give
generic hints ("manual", "check Redpanda"). The agent named the exact setting, value or component in
15/18. One repeatable bias explains its failures: it treats a recent change on the alerted deployment
as the cause, even when the new pods are healthy.

- [Method](#method)
- [Results: live runs](#results-live-runs)
- [Results: replay (model comparison)](#results-replay-model-comparison)
- [What the results mean](#what-the-results-mean)
- [Limits of this evaluation](#limits-of-this-evaluation)
- [Reproduce](#reproduce)

## Method

**Scenarios.** The six chaos scenarios in [`scripts/chaos/`](../scripts/chaos/README.md), 3 runs each:

| Scenario | How it is caused | Alert that opens the incident | Correct diagnosis |
|---|---|---|---|
| Bad deploy | `MAX_AMOUNT_CENTS=abc` on payments; new pods crash-loop | `PodRestarted` | the new env var; `rollout_undo payments` |
| Database outage | CloudNativePG hibernation of `orders-db` | `OrdersAvailabilityBudgetBurnFast` | Postgres down; manual fix |
| S3 outage | LocalStack scaled to 0 | `NotificationErrors` | S3/LocalStack down; manual fix |
| Slow consumer | `PROCESSING_DELAY_MS=2000` on payments | `ConsumerLagHigh` | the delay; `rollout_undo payments` or manual |
| Poison messages | 5 malformed events written straight to the topics | `InvalidMessagesReceived` | a producer sends bad events; no action |
| Broker outage | Redpanda scaled to 0 | `OutboxBacklog` | Redpanda down; manual fix |

**Harness** ([`scripts/eval/run_eval.py`](../scripts/eval/run_eval.py)). For each run: wait until no
sentinel alert is pending or firing, start the order generator if the scenario needs traffic, inject,
wait for the agent's first finished investigation of a matching incident, recover, save everything as
JSON in `results/`. Diagnosis only: no action was approved, so every run starts from the same kind of
state.

**Scoring** ([`scripts/eval/scenarios.py`](../scripts/eval/scenarios.py)). Ground truth per scenario:
a cause is correct if it mentions at least one expected word and none of the scenario's red-herring
words (for example "rollout" in a poison-message diagnosis); an action is correct if its kind and
target are on the allowed list. Saved answers are re-scored whenever the ground truth changes.

**Baseline.** The agent's fixed rules ([`services/agent/app/rules.py`](../services/agent/app/rules.py))
read the same initial findings: one strong rule ("the latest rollout changed something, its new pods
fail and the previous revision was healthy → undo it") and a hint per alert name.

**Setup.** Local k3d cluster (Docker Desktop, 7 GB), `qwen3:4b` through Ollama with a 16k context,
temperature 0, at most 8 model turns. Runs on 25 October 2026.

## Results: live runs

Full table with every answer: [`results/summary.md`](results/summary.md).

| Scenario | Agent: cause | Agent: action | Baseline: cause | Baseline: action | Inject → report (median) |
|---|---|---|---|---|---|
| Bad deploy | 3/3 | 3/3 | 3/3 | 3/3 | 1 min 45 s |
| Database outage | 3/3 | 3/3 | 3/3 | 3/3 | 7 min 35 s |
| S3 outage | 3/3 | 3/3 | 3/3 | 3/3 | 7 min 14 s |
| Slow consumer | 3/3 | 3/3 | 3/3 | 3/3 | 17 min 48 s |
| Poison messages | **0/3** | **0/3** | 3/3 | 3/3 | 3 min 42 s |
| Broker outage | 3/3 | 3/3 | 3/3 | 3/3 | 5 min 35 s |
| **All** | **15/18** | **15/18** | 18/18 | 18/18 | |

Time to report is mostly the alert rules' own `for:` durations and lag building up. The agent itself
took 28 s to 3 min per investigation (qwen3 "thinks" before answering).

**Checked by hand, 11/18 were fully right.** Four answers the scorer accepted had the right component
but a wrong detail:

| Run | Agent said | What really happened |
|---|---|---|
| s3-outage-3 | LocalStack pods fail their probes | LocalStack was scaled to 0 |
| slow-consumer-1 | the delay caused readiness probe failures | no probe failed; processing was slow |
| broker-outage-2, -3 | the Redpanda Service is missing | the Service existed; with 0 pods its DNS name doesn't resolve |

**What the agent said vs. the baseline** (one run each):

| Scenario | Agent | Baseline |
|---|---|---|
| Bad deploy | "MAX_AMOUNT_CENTS set to invalid string 'abc' in latest rollout" | undo the payments rollout |
| Slow consumer | "latest rollout added PROCESSING_DELAY_MS=2000 to payments" | consumer too slow |
| Database outage | "Postgres cluster orders-db is hibernated" | check orders and Postgres |
| Poison | "removal of MAX_AMOUNT_CENTS in the latest payments rollout" ✗ | a producer sent invalid events ✓ |

## Results: replay (model comparison)

Live runs can't compare models fairly: every run sees a slightly different cluster. The replay tool
([`scripts/eval/replay.py`](../scripts/eval/replay.py)) rebuilds the exact first message of each recorded
run (same alerts, same findings, same timestamp), runs the agent's own investigation loop against a
model, and answers tool calls from the recording. A call that was not recorded gets an error and is
counted. Full table: [`replay/summary.md`](replay/summary.md).

| | live qwen3:4b | replay qwen3:4b | replay llama3.2:3b |
|---|---|---|---|
| Cause (scorer) | 15/18 | 15/18 | 9/18 |
| Action (scorer) | 15/18 | 13/18 | **1/18** |
| Median time per investigation | — | 53 s | 10 s |
| Tool calls by the model | 0 | 0 | 70 (38 not in the recording) |

- **qwen3:4b is clearly better on identical evidence.** llama3.2:3b suggests `restart_deployment`
  almost everywhere (including "restart redpanda", a StatefulSet the agent may not touch) and misses the
  bad-deploy, database and slow-consumer causes. Its numbers are a lower bound: 38 of its 70 tool calls
  had no recorded answer, so it got an error where the live agent would have got data.
- **Temperature 0 is not fully repeatable.** On the same input qwen3 changed its action in 4 of 18 runs:
  for the S3 outage it said `manual` live and `restart_deployment localstack` on replay (wrong: LocalStack
  was scaled to 0). Causes stayed the same. This is why every scenario was run three times.

## What the results mean

**1. The initial findings do the investigating; the model reads them.** In all 18 live runs qwen3
made **zero tool calls**: every answer came from the evidence collected by fixed rules before its first
turn (unhealthy pods, warning events, crash logs, error logs of the alerted service, rollout diff). The
tool loop exists but this model doesn't use it.

**2. One bias explains both the wins and the losses.**

| Scenario | Recent rollout on the alerted deployment? | Was it the cause? | Model blamed it? | Result |
|---|---|---|---|---|
| Bad deploy | yes, new pods failing | yes | yes | ✓ |
| Slow consumer | yes, new pods healthy | yes | yes | ✓ |
| Poison | yes (the rollback from earlier runs), new pods healthy | **no** | yes | ✗ |
| S3, broker, database | no recent rollout on the alerted deployment | — | — | ✓ |

The model treats "the latest rollout changed something" as the cause whatever `new_pods_healthy` says;
the prompt rule against it had no visible effect. In the poison runs its own summary even named the right
problem ("malformed order.created events with negative amounts") before blaming the rollout. The findings
also still held `BackOff` events from the bad-deploy runs, a second red herring. Approving its suggestion
would have re-broken payments; the fixed rule got all three right.

**3. Some correct answers were primed by the prompt.** The system prompt describes the system, including
`cnpg.io/hibernation=on` for the database. No database run collected evidence of hibernation (the findings
showed failing readiness probes and connection errors), yet every answer said "hibernated", and replayed
answers quoted the annotation word for word. The database score measures recall of the prompt as much
as diagnosis. The prompt also names `MAX_AMOUNT_CENTS` and `PROCESSING_DELAY_MS`; there the rollout diff
did show them, so those answers are backed by evidence.

**4. The design holds up because the model is not trusted alone.** Every failure above would have been
caught before it did harm: actions need a person's approval, the fixed rule is shown next to the model's
suggestion, and the agent's write permissions cover only three deployments.

## Limits of this evaluation

- **Small:** 18 runs, one cluster, one person, one day. Enough to see patterns, not to quote precise rates.
- **Keyword scoring** can't read negation or mechanism; the hand check above corrects for that, but it is
  one person's judgement.
- **Order effects:** scenarios ran back to back, so earlier runs left state behind (the payments rollback,
  old events). That is realistic, and it is what exposed the bias, but it also means runs are not independent.
- **Prompt leakage:** the system prompt names the mechanisms used by the chaos scripts (point 3).
- **Grouping** of related alerts was never exercised live: every run recovered before the 15-minute
  Kubernetes alerts fired.
- **Replay** can only answer tool calls that were recorded, which penalises models that call tools more.

## What to change next

These were deliberately not tried during the evaluation, to avoid tuning to the test cases. Each should
be measured on fresh runs:

1. Leave rollouts with healthy new pods out of the initial findings, or label them "healthy, probably not
   the cause".
2. Let a fixed rule veto the model's action when it has strong grounds (invalid messages → no rollback).
3. Remove scenario-specific details from the system prompt (hibernation, variable names) and add a
   `k8s_get postgres-cluster` finding when Postgres looks unhealthy, so the answer comes from evidence.
4. Drop events older than the incident from the findings.

## Reproduce

Cluster up (`./scripts/up.sh`), agent at http://localhost:18003, Ollama running with `qwen3:4b`:

```bash
python3 scripts/eval/run_eval.py --runs 3 --scenarios bad-deploy   # one scenario at a time
python3 scripts/eval/run_eval.py --report                          # rebuild results/summary.md
```

Replay needs no cluster, only the agent's Python environment and a model server:

```bash
services/agent/.venv/bin/python scripts/eval/replay.py --models qwen3:4b,llama3.2:3b
services/agent/.venv/bin/python scripts/eval/replay.py --report     # rebuild replay/summary.md
```

Tests for both: `services/agent/.venv/bin/python -m pytest -q scripts/eval`.
