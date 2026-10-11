# Reproducible runs and accounting

The authoritative router remains `DecisionEngine`; the existing orchestrator,
planner, validators, local Qwen worker, and fallback cascade execute every batch.
Shadow recommendations never execute an alternative or change a route.

## Running tasks

From the repository root (PowerShell or a POSIX shell):

```text
python main.py
python main.py --input input/tasks.txt --output-dir output/text-runs --privacy include
python main.py --input input_samples/tasks.json --output-dir output/json-runs --privacy include
python main.py --input input/tasks.txt --output output/custom-results.json --output-dir output/custom-runs
```

No arguments preserve `input/tasks.json` and `output/results.json`, or the mounted
`/input/tasks.json` and `/output/results.json` in Docker. The checked-in
`input/tasks.json` currently contains 1,000 tasks; `input_samples/tasks.json`
contains the 10-task dataset. A unique UUID directory is created for every run.
The legacy results path is refreshed after backing up its previous bytes in that
run directory. Explicit `--output` refuses replacement unless `--overwrite` is
specified. Malformed input is rejected as a whole with exit code 2 and a run report.

Text input uses a delimiter on its own line and an optional ID immediately after it:

```text
---TASK---
task_id: math-001
Calculate 125 * 48.
---TASK---
Explain an API in two sentences.
Use plain language.
```

Multiline prompts and internal blank lines are preserved. Omitted IDs become
`task-0001`, `task-0002`, etc. Empty blocks/IDs/prompts, duplicate IDs (including
collisions with generated IDs), and text before the first delimiter are errors.
An exact delimiter line cannot be embedded in a prompt; use JSON for that case.
JSON remains a list of `TaskRequest` objects; session/provider fields are optional.
Tasks are independent unless their individual prompts establish a planner dependency
or explicitly share a session. Concurrent tasks sharing a session see the snapshot
available at admission; a session ID does not impose execution order.

`FIREWORKS_CONCURRENCY` retains the batch admission bound. Existing 28-second or
experimental 70-second per-task deadlines remain; `BATCH_TIMEOUT_SECONDS=570`
also caps inference admission/execution across the batch. Reporting and shutdown
take additional time, so a strict container wall-clock limit should still be imposed
externally. API deadlines remain `TASK_TIMEOUT_SECONDS`.

## Outputs and privacy

Each `output/runs/<run_id>/` (or the selected root) contains:

* `results.json`: exactly `[{"task_id":"...","answer":"..."}]` for the grader.
* `run_audit.json`: task outcomes, decisions, executor attempts, nested transport
  requests, usage provenance, price version, errors, context and shadow metadata.
* `results.csv`: one task per row, nested details serialized as JSON cells.
  Formula-like text is escaped for spreadsheet safety.
* `run_summary.json`: counts, validation coverage, latency sample counts, routes,
  provider/model requests, token coverage, complete/partial/estimated costs.
* `telemetry.json`: existing telemetry plus bounded execution audits.

`REPORT_PRIVACY=omit` (default) removes prompts/answers from audit and CSV, retaining
a prompt hash. `--privacy include` retains them. Grader results always contain
answers; session summaries may contain answers when context management is enabled.
This is a retention policy, not automatic PII redaction. Provider payloads and
credentials are never copied into the request ledger. API clients can read the
separate ledger at `GET /api/tasks/{task_id}/audit` under existing authentication.

Example structure (values are illustrative, not measurements):

```json
{
  "schema_version": 1,
  "run_id": "uuid",
  "tasks": [{
    "task_id": "math-001",
    "initial_decisions": [{"subtask_id": "math-001_0", "route": "python"}],
    "attempts": [{"attempt_number": 1, "route": "python", "requests": [], "external_request_made": false}],
    "api_cost": {"complete_actual_api_cost": 0, "local_compute_cost": null}
  }]
}
```

## Cost and token semantics

One executor invocation is an attempt. Each HTTP/SDK call inside it is a nested
request. Sum requests for API accounting; never sum both levels. Fireworks retries
and truncation recovery remain separate requests. SDK automatic retries are disabled
and the customary two transient retries are explicitly recorded. A transport call
means a request was attempted, not proof the server received or charged it;
`paid_charge_confirmed` remains unknown. Transport errors without usage retain
unknown charges. Failed validation does not erase the preceding request's usage.

Missing values stay null. `usage.source` distinguishes provider reports, RelayOS
local tokenizer measurements, estimates, fixtures and unknowns. API token totals
show known subtotals and reporting coverage. Input fields retain provider semantics:
Anthropic input excludes cached reads/writes; OpenAI input includes them (writes
are subtracted when supplied). Per-request flags state these semantics. Do not add
cache columns to inclusive input totals. No tokenizer estimate is presented as a
measured token count. `fireworks_tokens` is retained as a legacy known subtotal;
use the ledger/coverage fields for defensible accounting.

Rates are supplied only by the operator in `ROUTING_PRICES_JSON`, keyed by the
exact `provider:model`. Reports do not fall back to a provider-wide rate. Each rate
uses currency per million tokens, a source and a version. No official price defaults
are embedded. For example, this is **synthetic configuration**, not a provider price:

```json
{"fireworks:YOUR_MODEL":{"input_per_million":1,"cached_input_per_million":0.2,"cache_write_per_million":1.25,"output_per_million":2,"source":"operator-example-not-official","version":"example-v1","currency":"USD"}}
```

`ROUTING_CURRENCY` must match the configured currency. Invalid/negative/nonfinite
rates are rejected. Complete actual API cost requires all necessary usage categories
and rates. Missing cached/write usage remains conservative and incomplete even if
ordinary input/output were returned. Known components are partial; estimates and
unknowns remain separate. Zero-valued known components cannot hide missing ones.
Mixed Anthropic cache-write TTL pricing is conservatively flagged unsupported.
This version sends text chat only and does not model tools, audio, batch discounts,
or arbitrary pricing tiers. Use rates applicable to the requested model/tier.
Zero external calls means zero external API charge, **not zero local compute cost**.

Native cache usage is observed without cache-specific extra inference calls. Local
prepared-file cache hits are unrelated to provider cache hits. Native retention,
cache-key reuse and benefits remain provider/model-specific; unsupported/missing
fields are unknown. No cache is assumed transferable between providers. Schema
references checked during this work:

* [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
* [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
* [Fireworks prompt caching](https://docs.fireworks.ai/guides/prompt-caching)

## Optional context and shadow mode

`CONTEXT_MANAGEMENT_ENABLED=false` and `CONTEXT_ROUTING_ENABLED=false` preserve
legacy behavior by default. Context also requires a session ID. Explicit
`context_options.file_paths` select source/tests under `REPOSITORY_ROOT`.
Current bytes are read and SHA-256 checked before any prepared-file cache hit is
reported. Missing/truncated/excluded files have explicit reasons. Normalized root,
relative path, content hash and limits form the key. A process-local LRU stores at
most 32 fragments, each at most 16,000 characters; no remote/cache service is used.
Reads are bounded to 1 MB per file. Traversal, absolute paths, resolved escapes,
environment/credential files, dependency/build/output directories and non-source
extensions are excluded. This is explicit selection, not repository-wide retrieval.

Context defaults are 12,000 characters, four files, 4,000 characters per file;
hard upper bounds are 64,000 characters, 32 files, 16,000 characters per file.
Source sections take priority over summary. Session persistence uses atomic replace,
at most 128 sessions, bounded recent task metadata and a latest-answer snapshot
(not a recursively appended summary). Corrupt/missing/oversized state is ignored.
Locks protect concurrent writers within one process. Multiple API worker processes
should use separate session-store paths; cross-process merging is not implemented.

Shadow mode always sets `recommendation_applied=false`, even if
`CONTEXT_ROUTING_SHADOW=false`. It compares one next-task horizon. The last observed
request can supply a **next-task estimate**, never guaranteed future usage. A candidate
requires explicit `ROUTING_ESTIMATES_JSON` assumptions, for example:

```json
{"candidate":{"provider":"openai","model":"YOUR_MODEL","usage":{"input_tokens":100,"cached_input_tokens":0,"cache_write_tokens":0,"output_tokens":50},"quality":0.8,"quality_evidence":"operator estimate, unvalidated","latency_ms":1000,"retry_probability":0.1,"context_rebuild_cost":0},"current_retry_probability":0.1,"max_latency_ms":3000}
```

Both models need exact prices and complete usage estimates. Specify the candidate's
input inclusion flags when its schema differs. Monetary rebuild cost is explicit;
latency and quality are gates, not dollar-valued weighted scores. Insufficient data
produces `inconclusive`. Safety margin defaults to 10%. No savings are claimed.
To roll back optional behavior, unset `CONTEXT_MANAGEMENT_ENABLED` and
`CONTEXT_ROUTING_ENABLED`, restart the process, and retain the normal router.
No automatic routing activation exists in this change.

## Benchmark and Docker

```text
python benchmark_coding.py
python benchmark_context_routing.py
python -m pytest -q
cd frontend
npm run build
npm run test:e2e
```

The coding benchmark defaults to authored mock HTTP responses through the real
orchestrator. It gives every strategy identical starting source/tests in separate
directories and executes acceptance checks. Its two-task mock scores validate the
harness only. Mock token counts/costs remain unknown and timings are harness overhead.
For explicit live inference, use `--mode live --strong-model MODEL --cheap-model MODEL`;
credentials/models must already be configured. Live acceptance code runs in a
network-disabled, read-only, resource-limited Docker container. Live inference was
not performed during this verification. The older context benchmark is fixture-only
economics and does not measure correctness.

```text
docker build --progress=plain -t relayos:gap-audit-20261011 .
```

The pinned Linux CPython 3.11 CPU wheel must exist before installation. Docker now
uses binary-only selection and retains pip's normal hash verification. A successful
build records the installed artifacts in `/opt/venv/install-report.json`. It currently
fails because the configured index does not offer a compatible 0.3.10 wheel. No pin
was changed and no source compilation was started. The known image was preserved.
See [verification evidence](VERIFICATION.md) for actual outcomes and blockers.
