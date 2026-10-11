from .base import BaseExecutor
from models.schemas import TaskContext, ExecutionResult
from models.enums import ExecutionRoute, ExecutionState
from core.config import settings
from core.logger import setup_logger
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from providers.base import ProviderError, ProviderResponse

logger = setup_logger("fireworks_executor")

class FireworksExecutor(BaseExecutor):
    name = "fireworks"
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=settings.fireworks_base_url,
            headers={
                "Authorization": f"Bearer {settings.fireworks_api_key}",
                "Content-Type": "application/json"
            },
            timeout=30.0
        )
        
    def _select_model(self, context: TaskContext) -> str:
        if context.request.model:
            return context.request.model
        # Step 4: Retry with Pro if Flash failed (failed_attempts > 1)
        if getattr(context, "failed_attempts", 0) > 1:
            return settings.fireworks_reasoning_model
            
        # Step 3: Default to Fast model for first Fireworks attempt
        return settings.fireworks_fast_model
        
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.RequestError, httpx.HTTPStatusError))
    )
    async def _post_chat(self, payload: dict) -> httpx.Response:
        response = await self.client.post("/chat/completions", json=payload)
        response.raise_for_status()
        return response

    async def execute(self, context: TaskContext) -> ExecutionResult:
        model = self._select_model(context)
        
        from config import FeatureFlags
        if FeatureFlags.ENABLE_SHORT_FIREWORKS_PROMPTS:
            system_prompt = "Output ONLY final answer. No filler. Provide explanations only if explicitly requested."
        else:
            system_prompt = "You are a highly efficient AI. Output ONLY the final answer. Do not use conversational filler, do not say 'Here is...', and do not explain your steps unless explicitly requested."
            
        if FeatureFlags.ENABLE_RESPONSE_COMPRESSION:
            if context.category.value in ["factual", "qa", "definition"]:
                system_prompt += " Be extremely concise. Output only the direct answer. No markdown or filler. Only provide explanations if the prompt specifically asks for them."
            elif context.category.value not in ["sentiment", "ner", "code_gen", "architecture"]:
                system_prompt += " Be extremely concise. Output only the direct answer. No markdown. No explanations. No chain of thought. Use JSON only if explicitly requested."
        
        if getattr(context, "failed_attempts", 0) > 0:
            system_prompt += " Retry: Follow format strictly. No filler."
        
        # Static max_tokens allocation to prevent truncation retries and latency spikes
        if FeatureFlags.ENABLE_DYNAMIC_MAX_TOKENS and getattr(context, "profile", None) and getattr(context.profile, "estimated_output_tokens", 0) > 0:
            max_tokens = int(context.profile.estimated_output_tokens * 1.5)
            max_tokens = min(max(max_tokens, 256), 4096)
        else:
            max_tokens = 4096
            
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context.request.prompt}
            ],
            "temperature": 0.1,
            "max_tokens": max_tokens
        }
        
        input_tokens = None
        output_tokens = None
        tokens = 0
        try:
            logger.info(f"Task {context.request.task_id}: Escalating to Fireworks API ({model})")
            resp = await self._post_chat(payload)
            data = resp.json()
            choice = data["choices"][0]
            answer = choice["message"]["content"].strip()
            usage = data.get("usage") or {}
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
            tokens = usage.get("total_tokens")
            if tokens is None and input_tokens is not None and output_tokens is not None:
                tokens = input_tokens + output_tokens
            tokens = tokens or 0
            
            # --- Output Token Validator (Truncation Recovery) ---
            if choice.get("finish_reason") == "length":
                logger.warning(f"Task {context.request.task_id}: Fireworks truncated output (length). Retrying with expanded limits.")
                payload["max_tokens"] = min(max_tokens * 2, 4096)
                resp2 = await self._post_chat(payload)
                data2 = resp2.json()
                answer = data2["choices"][0]["message"]["content"].strip()
                usage2 = data2.get("usage") or {}
                tokens += usage2.get("total_tokens") or 0
                if usage2.get("prompt_tokens") is not None:
                    input_tokens = (input_tokens or 0) + usage2["prompt_tokens"]
                if usage2.get("completion_tokens") is not None:
                    output_tokens = (output_tokens or 0) + usage2["completion_tokens"]
                
        except Exception as e:
            logger.error(f"Task {context.request.task_id}: Fireworks API failed - {e}")
            answer = ""
            tokens = 0
            return ExecutionResult(
                task_id=context.request.task_id,
                answer=answer,
                route_taken=ExecutionRoute.FIREWORKS,
                state=ExecutionState.FAILED_RETRYABLE,
                fallback_triggered=True,
                fireworks_tokens=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_source="provider" if input_tokens is not None or output_tokens is not None else "unknown",
                error_code="provider_error",
                error_message="Fireworks request failed",
            )
            
        if not answer:
            return ExecutionResult(
                task_id=context.request.task_id,
                answer="",
                route_taken=ExecutionRoute.FIREWORKS,
                state=ExecutionState.FAILED_RETRYABLE,
                fallback_triggered=True,
                fireworks_tokens=tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                usage_source="provider" if input_tokens is not None or output_tokens is not None else "unknown",
                error_code="empty_response",
                error_message="Fireworks returned an empty response",
            )
        return ExecutionResult(
            task_id=context.request.task_id,
            answer=answer,
            route_taken=ExecutionRoute.FIREWORKS,
            state=ExecutionState.SUCCESS,
            fireworks_tokens=tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            usage_source="provider" if input_tokens is not None or output_tokens is not None else "unknown",
        )

    async def shutdown(self):
        await self.client.aclose()

    async def complete(self, prompt: str, model: str, *, timeout=None) -> ProviderResponse:
        """Common provider-interface entry point for integrations and tests."""
        try:
            response = await self._post_chat({"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "max_tokens": 1024})
            data = response.json()
            choice = data["choices"][0]
            text = (choice["message"].get("content") or "").strip()
            if not text:
                raise ProviderError(self.name, "empty_response", "Fireworks returned an empty response", True)
            usage = data.get("usage", {})
            return ProviderResponse(provider=self.name, model=model, text=text, input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"), finish_reason=choice.get("finish_reason"))
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            code = "provider_rate_limit" if exc.response.status_code == 429 else "provider_error"
            raise ProviderError(self.name, code, "Fireworks request failed", exc.response.status_code >= 500 or exc.response.status_code == 429) from exc
        except httpx.RequestError as exc:
            raise ProviderError(self.name, "provider_unavailable", "Fireworks request failed", True) from exc
