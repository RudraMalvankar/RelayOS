# RelayOS implementation plan

## Milestone 1: audit baseline

Current repository state:

- The application entry point is `main.py`, which processes `input/tasks.json` and writes batch results.
- Prompt analysis, planning, routing, confidence checks, validation, deterministic math, spaCy NER, local GGUF inference, Fireworks execution, and telemetry already exist.
- There is currently no FastAPI service, React frontend, provider abstraction for Claude/GPT, or persistent task/session API.
- The local model is optional at runtime, but the executor attempts to initialize it during orchestrator construction when `llama-cpp-python` is installed.
- Provider configuration is environment-based through `FIREWORKS_API_KEY`, `FIREWORKS_BASE_URL`, and `ALLOWED_MODELS`.
- No `.env` file or credential file is committed. Model binaries and runtime output are excluded by `.gitignore`.
- No separate `LICENSE` file is present. Existing attribution in `README.md` and `Agents.md` is preserved; publishing remains subject to the original project's licensing terms.

### Baseline verification

The existing test suite was run with spaCy and local-model feature flags disabled. It reported an initial failure and then did not finish within the available observation window, so the baseline is recorded as failing/hanging rather than passing. This must be repaired before claiming a green test suite.

## Implementation order

1. Harden schemas, explicit execution states, validation, fallback transitions, and test isolation.
2. Centralize configuration and make local inference lifecycle/queue behavior bounded and observable.
3. Add a normalized provider interface while preserving Fireworks behavior; add Claude/GPT adapters only when dependencies and credentials are explicitly configured.
4. Add a FastAPI backend with task status, events, health, providers, models, and metrics endpoints.
5. Add a React + TypeScript + Vite frontend with automatic/local/cloud selection, task history, execution inspector, and honest metrics.
6. Add mocked provider/API/frontend coverage, resource measurements, documentation, and deployment configuration.

Each milestone will be tested, committed, and pushed separately. No feature will be represented as available unless the current configuration and runtime verify it.
