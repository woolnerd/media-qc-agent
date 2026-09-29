# Jev classification comparison

Jev is an optional typed classifier behind the existing `ModelProvider`
interface. Gemini remains the default chat adapter while we gather broader
classification evidence. Jev suits fixed label selection; its adapter supplies
application summaries and clarification templates rather than model-written
explanations.

## Adapter contract

`JevModelProvider` sends one bounded request to OpenRouter's alpha Decisions API.
It uses `typesafe/jev-1.13` rather than the moving `~typesafe/jev-latest` alias.
The recorded comparison resolved to `typesafe/jev-1.13-20260917`. OpenRouter
listed the release at $0.042 per million input tokens with output tokens free
when checked September 29, 2026. The alias, pricing, and alpha API can change.
See the [model page](https://openrouter.ai/typesafe/jev-1.13) and
[Decisions tutorial](https://openrouter.ai/docs/guides/community/jev-tutorial).

A Choice question selects among the five existing failure classes and
`uncertain`. Each supplied fact receives one Noul support question per candidate
class. These independent questions explicitly name their candidate; they cannot
refer to another question's eventual answer. Inferences and uncertainty never
become fact-support questions. The original feedback and evidence remain data.

Pure application functions validate all expected answers, labels, finite
probabilities, probability distributions, and optional usage metadata. Missing
confidence, low confidence, `uncertain`, or absence of a supported fact produces
diagnostic clarification with no repair. Unknown cost remains unknown.

Both Choice confidence and the selected class's evidence-support probability
must reach the existing 0.8 threshold. These are initial heuristics, with no
threshold tuning on this dataset. Choice confidence describes concentration,
not calibrated correctness or permission to act; Noul support is also a model
judgment. See [Typesafe's confidence explanation](https://docs.typesafe.ai/confidence).

For an accepted label, application code cites only supplied facts and labels the
explanation as an application summary. `plan_repair` determines the exact repair
scope. Environment mismatches still require a human script/avatar choice. The
existing strict interpretation validator checks the converted contract, and
version-bound approval remains necessary before execution. Jev cannot choose a
repair, approve it, persist findings, or submit media jobs.

## Matched live run

The [saved comparison](../evals/results/jev-comparison-2026-09-29.json) contains
one request per provider per case on all 15 saved synthetic cases, without
retries. Both providers received identical feedback and evidence. Artifact IDs
were masked to retain only kind prefixes: the original fixture names contained
class and difficulty hints. Evidence references were remapped consistently.
Case IDs, categories, expectations, and fake responses were never sent.

Provider order alternated by case. Chat used its existing prompt and settings;
Jev used the typed questions above. The experiment recorded outputs, resolved
models, usage, elapsed time, and the existing exact-contract scores. The masking
applies only to evaluation inputs and must never create persisted repair plans.

| Provider | Exact cases passed | Median end-to-end seconds | Reported USD, all 15 calls |
| --- | ---: | ---: | ---: |
| Gemini 3.1 Flash Lite | 14/15 | 1.669 | 0.004763250 |
| Jev 1.13 | 14/15 | 0.346 | 0.000687792 |

Both failed the adversarial TTS case, for different reasons. Gemini classified a
spoken-text defect as caption formatting and proposed caption-only repair. Jev
selected TTS input compatibility with confidence 0.89, but its supporting fact
scored 0.65, below the 0.8 gate, so the application requested clarification and
proposed no repair. Abstention still fails that case's expected complete answer.
The earlier unmasked Jev smoke call passed; it is separate from this matched run.

Jev was about 4.8 times faster and 6.9 times cheaper in this run, with equal
complete-case scores. This supports further evaluation as a classifier, rather
than a default-provider switch. Fifteen synthetic cases and one run cannot
establish representative accuracy, calibrated thresholds, or stable latency.
The original unmasked Gemini baseline remains a separate experiment.

## Replay

Set `OPENROUTER_API_KEY` in the process environment. The CLI does not load `.env`
automatically. `OPENROUTER_MODEL` configures chat; `OPENROUTER_JEV_MODEL` separately
overrides the pinned Jev release.

```bash
PYTHONPATH=src python3 -m media_qc_agent.evaluation --live --provider jev --mask-version-labels
PYTHONPATH=src python3 -m media_qc_agent.evaluation --live --provider chat --mask-version-labels
```

Each command makes 15 paid interpretation requests and exits nonzero when any
case fails. `--case-id` restricts the run to one case. Omitting `--live` always
uses saved fake responses, regardless of the provider selection. These commands
replay scoring; they do not collect the paired cost/latency report automatically.
Live responses and scores may vary. No artifact repair or media generation runs.
