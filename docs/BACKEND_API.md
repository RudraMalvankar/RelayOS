# RelayOS Backend API Contract

The API is provided by `api.py` and does not require the frontend to share implementation details with the batch runner.

## Startup

```powershell
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Set `RELAYOS_ENV=production` and `RELAYOS_API_TOKEN` to protect execution endpoints. Production startup fails if the token is missing. In explicitly configured development mode, an empty token permits local development requests. Health remains public.

## Provider availability

`GET /api/providers` returns provider descriptors without credentials:

```json
{
  "providers": [
    {"name":"fireworks","configured":true,"models":["model-a"],"capabilities":["chat","code"],"reason":null},
    {"name":"openai","configured":false,"models":[],"capabilities":["chat","code","tools"],"reason":"OPENAI_API_KEY is not configured"}
  ]
}
```

`GET /api/models` returns configured model IDs. Model IDs are supplied by environment configuration; they are not inferred from a consumer ChatGPT or Claude subscription.

## Create a task

`POST /api/tasks` returns `202` and starts asynchronous execution.

```json
{
  "task_id": "optional-client-id",
  "prompt": "Calculate 2 + 2",
  "execution_mode": "auto",
  "provider": null,
  "model": null
}
```

`execution_mode` is one of `auto`, `local`, or `cloud`. `provider` may be `fireworks`, `openai`, `anthropic`, or `local_llm`. The old `{prompt, task_id}` request remains valid.

## Task status

`GET /api/tasks/{task_id}` returns:

```json
{
  "task_id":"task-1",
  "status":"success",
  "result": {
    "task_id":"task-1",
    "answer":"4",
    "route_taken":"python",
    "state":"success",
    "validation_passed":true,
    "retry_count":0,
    "fallback_triggered":false,
    "error_code":null
  },
  "error":null,
  "created_at":0,
  "updated_at":0
}
```

Execution states are `success`, `fallback_success`, `failed_retryable`, and `failed_final`. Empty answers and invalid provider responses are never reported as successful.

## Cancellation

`POST /api/tasks/{task_id}/cancel` requests cancellation. API task cancellation stops the orchestration coroutine. A supervised local inference process is terminated before its worker is reused; the API does not claim native work has stopped until that worker exits.

## Metrics

`GET /api/metrics` returns bounded telemetry counters. Detailed telemetry is written atomically to `TELEMETRY_PATH` when the batch runner or service records tasks.

## Error responses

Errors use ordinary HTTP status codes and safe messages. Common statuses are `401` authentication required, `404` task not found, `409` duplicate task ID, `413` prompt too large, and `429` rate limit exceeded. Provider failures are represented in `ExecutionResult.error_code` using categories such as `provider_authentication`, `provider_rate_limit`, `provider_unavailable`, `timeout`, `validation_failed`, and `resource_limit`.

## Compatibility and limitations

The original `python main.py` batch runner remains supported and writes grader-compatible `task_id`/`answer` records. API task history is bounded in memory and is lost when the process restarts. External provider calls are optional; deterministic and local startup does not require cloud credentials.
