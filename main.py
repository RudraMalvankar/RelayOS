import os

def _apply_cpu_limits():
    try:
        from config import FeatureFlags
        if getattr(FeatureFlags, "ENABLE_CPU_THREAD_LIMITS", False):
            os.environ["OMP_NUM_THREADS"] = "1"
            os.environ["OPENBLAS_NUM_THREADS"] = "1"
            os.environ["MKL_NUM_THREADS"] = "1"
            os.environ["NUMEXPR_NUM_THREADS"] = "1"
    except ImportError:
        pass
_apply_cpu_limits()

import asyncio
import json
from dataclasses import asdict
from typing import List
from models.schemas import TaskRequest, TaskContext, ExecutionResult
from models.enums import ExecutionRoute, ExecutionState
from pipeline.analyzer import PromptAnalyzer
from pipeline.classifier import TaskClassifier
from pipeline.complexity import ComplexityEstimator
from pipeline.validator import ResultValidator
from engine.decision import DecisionEngine
from engine.confidence import ConfidenceEngine
from engine.dag import DAGEngine, DAGNode
from pipeline.planner import TaskPlanner, ResultAggregator
from core.telemetry import TelemetryService, TaskTrace
from core.context_routing import ContextBuilder, JsonSessionStore, Price, Usage, load_prices, recommend_switch
from core.config import settings
from engine.executors.python import PythonExecutor
from engine.executors.fireworks import FireworksExecutor
from engine.executors.local_llm import LocalLLMExecutor
from engine.executors.cloud import CloudProviderExecutor
from engine.executors.spacy_ner import SpacyNERExecutor
from core.logger import setup_logger

from core.audit import active_audit, new_audit, audit_scope, execute_recorded, decision, validation

logger = setup_logger("RelayOS_main")

class RelayOSOrchestrator:
    def __init__(self):
        self.analyzer = PromptAnalyzer()
        self.decision_engine = DecisionEngine()
        self.python_exec = PythonExecutor()
        
        # Point to the Qwen2.5 model (Supports both Docker and Local dev paths)
        from core.config import settings
        self.local_exec = LocalLLMExecutor(model_path=settings.local_model_path)
        self.api_exec = FireworksExecutor()
        self.openai_exec = CloudProviderExecutor("openai")
        self.anthropic_exec = CloudProviderExecutor("anthropic")
        self.provider_initialization_errors = {}
        for provider, executor in (("openai", self.openai_exec), ("anthropic", self.anthropic_exec)):
            if executor.initialization_error:
                self.provider_initialization_errors[provider] = "initialization_failed"
        
        from config import FeatureFlags
        if FeatureFlags.ENABLE_SPACY_NER:
            self.spacy_exec = SpacyNERExecutor()
        else:
            self.spacy_exec = None

    def _requested_route(self, context: TaskContext):
        request = context.request
        if request.provider:
            try:
                return ExecutionRoute(request.provider)
            except ValueError:
                return None
        if request.execution_mode == "local":
            return ExecutionRoute.LOCAL_LLM
        if request.execution_mode == "cloud":
            return ExecutionRoute.FIREWORKS
        return None

    async def _execute_route(self, context: TaskContext) -> ExecutionResult:
        route = context.route
        if route == ExecutionRoute.FIREWORKS:
            return await execute_recorded(self.api_exec, context, "fireworks")
        if route == ExecutionRoute.OPENAI:
            return await execute_recorded(self.openai_exec, context, "openai")
        if route == ExecutionRoute.ANTHROPIC:
            return await execute_recorded(self.anthropic_exec, context, "anthropic")
        if route == ExecutionRoute.LOCAL_LLM:
            return await execute_recorded(self.local_exec, context, "local_llm")
        return await execute_recorded(self.python_exec, context, "python")
            
    async def _execute_single_route(self, context: TaskContext) -> ExecutionResult:
        """Helper to execute a single atomic task."""
        route = context.route
        fallback_triggered = False
        
        from config import FeatureFlags
        if context.category and context.category.value == "ner" and FeatureFlags.ENABLE_SPACY_NER and self.spacy_exec:
            result = await execute_recorded(self.spacy_exec, context, "spacy")
            if not result.fallback_triggered:
                final_result = self._sanitize(result, context)
                if self._confident(final_result, context):
                    final_result.fallback_triggered = False
                    return final_result
                else:
                    logger.warning(f"Task {context.request.task_id}: Low confidence in spaCy output. Falling back to routed engine.")
        
        # Intercept Sentiment and Summarization for Local LLM experiment
        if context.category:
            cat_val = context.category.value
            if (cat_val == "sentiment" and FeatureFlags.ENABLE_LOCAL_SENTIMENT_QWEN) or \
               (cat_val == "summarization" and FeatureFlags.ENABLE_LOCAL_SUMMARIZATION_QWEN):
                try:
                    local_result = await execute_recorded(self.local_exec, context, "local_llm")
                    if not local_result.fallback_triggered:
                        final_result = self._sanitize(local_result, context)
                        if self._confident(final_result, context):
                            final_result.fallback_triggered = False
                            return final_result
                        else:
                            logger.warning(f"Task {context.request.task_id}: Low confidence in local Qwen output. Falling back to routed engine.")
                except Exception as e:
                    logger.warning(f"Task {context.request.task_id}: Local LLM crashed during experiment ({e}). Falling back.")

        if route in (ExecutionRoute.FIREWORKS, ExecutionRoute.LOCAL_LLM, ExecutionRoute.OPENAI, ExecutionRoute.ANTHROPIC):
            result = await self._execute_route(context)
            if result.state != ExecutionState.SUCCESS and route == ExecutionRoute.LOCAL_LLM:
                fallback_triggered = True
                context.failed_attempts += 1
                context.route = ExecutionRoute.FIREWORKS
                result = await execute_recorded(self.api_exec, context, "fireworks")
        else:
            result = await execute_recorded(self.python_exec, context, "python")
            if result.fallback_triggered and context.category.value == "math" and self.local_exec.is_available():
                ans = await self.local_exec.math_extraction_fallback(context.request.prompt)
                if ans:
                    result.answer = str(ans)
                    result.fallback_triggered = False
            
        final_result = self._sanitize(result, context)
        
        # Fallback Loop
        if final_result.state != ExecutionState.SUCCESS or not self._confident(final_result, context):
            fallback_triggered = True
            logger.warning(f"Task {context.request.task_id}: Low confidence in {route.value}. Recalculating route.")
            context.failed_attempts += 1
            context.route = self.decision_engine.route(context)
            
            result = await self._execute_route(context)
                
            final_result = self._sanitize(result, context)
            if not self._confident(final_result, context) or final_result.validation_passed is False:
                final_result.state = ExecutionState.FAILED_FINAL
                final_result.error_code = final_result.error_code or "validation_failed"
                validation(final_result, context, False)

        final_result.fallback_triggered = fallback_triggered
        return final_result

    @staticmethod
    def _sanitize(result, context):
        result = ResultValidator.sanitize(result, context.category)
        validation(result, context)
        return result

    @staticmethod
    def _confident(result, context):
        passed = ConfidenceEngine.evaluate(result, context)
        validation(result, context, passed)
        return passed

    async def process_task(self, request: TaskRequest) -> ExecutionResult:
        import time
        audit = active_audit.get()
        audit = audit if audit is not None else new_audit(request.task_id)
        started = time.perf_counter()
        with audit_scope(audit):
            try:
                result = await self._process_task(request.model_copy(deep=True))
                successful = [a for a in audit["attempts"] if a["outcome"] in {"success", "fallback_success"} and a.get("validation_passed") is not False]
                routes = list(dict.fromkeys(a["route"] for a in successful))
                audit["final_routes"] = routes if result.state in {ExecutionState.SUCCESS, ExecutionState.FALLBACK_SUCCESS} else []
                audit.update(state=result.state.value, final_route=(routes[0] if len(routes) == 1 else "mixed" if routes else None) if audit["final_routes"] else None,
                             validation_passed=result.validation_passed, routing_metadata=result.routing_metadata,
                             error_code=result.error_code)
                result.routing_metadata = result.routing_metadata or {}
                result.routing_metadata["run_id"] = audit["run_id"]
                return result
            except BaseException as exc:
                audit.update(state="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed_final",
                             error_code=type(exc).__name__, final_route=None, validation_passed=False)
                raise
            finally:
                audit["latency_ms"] = (time.perf_counter() - started) * 1000
                TelemetryService.get_instance().record_audit(audit)

    async def _process_task(self, request: TaskRequest) -> ExecutionResult:
        import time
        start_time = time.perf_counter()
        
        # 1. Analyze
        profile = self.analyzer.analyze(request.prompt)
        context = TaskContext(request=request, profile=profile)
        context.category = profile.primary_category
        context.complexity = ComplexityEstimator.estimate(context)
        context_package = None
        if request.session_id and settings.context_management_enabled:
            previous = JsonSessionStore(settings.session_store_path, settings.session_max_history, settings.session_summary_max_chars).get(request.session_id) or {}
            summary = previous.get("summary", "") if isinstance(previous.get("summary"), str) else ""
            history = previous.get("history", [])
            if isinstance(history, list) and history:
                summary += "\nRECENT OUTCOMES:\n" + json.dumps(history[-settings.session_max_history:])[:settings.session_summary_max_chars]
            options = request.context_options if isinstance(request.context_options, dict) else {}
            context_package = ContextBuilder(
                settings.repository_root,
                max_chars=settings.context_max_chars,
                max_files=settings.context_max_files,
                max_file_chars=settings.context_max_file_chars,
            ).build(request.prompt, summary, options.get("file_paths", []))
            request.prompt = context_package.text
            # Keep category stable, but route using the expanded context's token estimate.
            context.profile.estimated_input_tokens = context_package.estimated_tokens
        # 2. Plan (Heuristic Decomposition)
        planner = TaskPlanner()
        sub_tasks = planner.plan(context)
        
        # 3. Build DAG & Execute
        dag = DAGEngine()
        node_map = {}
        for st in sub_tasks:
            st_req = TaskRequest(task_id=st.id, prompt=st.prompt, execution_mode=request.execution_mode, provider=request.provider, model=request.model, session_id=request.session_id, required_quality=request.required_quality, context_options=request.context_options)
            st_ctx = TaskContext(request=st_req, profile=profile, category=st.category, complexity=context.complexity)
            
            async def _exec_node(node_ref: DAGNode):
                ctx = node_ref.context
                # Dynamic Context Injection
                if node_ref.dependencies:
                    dep_text = "\n\n--- PREVIOUS TASK OUTPUTS ---\n"
                    for d in node_ref.dependencies:
                        if d.result:
                            dep_text += f"[{d.task_id}]: {d.result.answer}\n"
                    ctx.request.prompt += dep_text
                    
                ctx.route = self._requested_route(ctx) or self.decision_engine.route(ctx)
                decision(ctx)
                return await self._execute_single_route(ctx)
                
            node = DAGNode(task_id=st.id, executable=_exec_node, context=st_ctx)
            node_map[st.id] = node
            dag.add_node(node)
            
        # Wire dependencies
        for st in sub_tasks:
            node = node_map[st.id]
            for dep_id in st.dependencies:
                if dep_id in node_map:
                    node.add_dependency(node_map[dep_id])
            
        # Execute all sub-tasks concurrently/sequentially based on edges
        sub_results = await dag.execute_graph()
        
        # 4. Aggregate
        aggregator = ResultAggregator()
        final_result = aggregator.aggregate(request.task_id, sub_results)
        # Legacy fields now aggregate every observed transport attempt, not only the last answer.
        from core.reporting import requests as audit_requests
        remote_records = audit_requests(active_audit.get() or {})
        if remote_records:
            complete = True
            for field in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_tokens"):
                values = [r["usage"].get(field) for r in remote_records]
                setattr(final_result, field, sum(values) if all(v is not None for v in values) else None)
                complete = complete and all(v is not None for v in values)
            final_result.usage_source = "provider" if complete else "partial"
            final_result.fireworks_tokens = sum((r["usage"].get("input_tokens") or 0) + (r["usage"].get("output_tokens") or 0) for r in remote_records)
            final_result.retry_count = sum(max(0, len(a["requests"]) - 1) for a in active_audit.get()["attempts"])
        if request.session_id and settings.context_routing_enabled:
            from core.reporting import shadow_from_observations
            final_result.routing_metadata = shadow_from_observations(active_audit.get(), request)
        if context_package is not None:
            final_result.routing_metadata = final_result.routing_metadata or {}
            final_result.routing_metadata["context"] = {
                "files": list(context_package.files),
                "cache_events": list(context_package.cache_events),
                "omitted": list(context_package.omitted),
                "estimated_tokens": context_package.estimated_tokens,
                "token_source": context_package.token_source,
                "truncated": context_package.truncated,
            }
        
        latency_ms = (time.perf_counter() - start_time) * 1000.0
        fallback_triggered = any(getattr(r, 'fallback_triggered', False) for r in sub_results)
        
        trace = TaskTrace(
            task_id=request.task_id,
            category=context.category.value if context.category else "UNKNOWN",
            latency_ms=latency_ms,
            route=final_result.route_taken.value if final_result.route_taken else "UNKNOWN",
            fallback_triggered=fallback_triggered,
            tokens=final_result.fireworks_tokens,
            state=final_result.state.value,
            provider=final_result.route_taken.value if final_result.route_taken else "unknown",
            model=final_result.model or "unknown",
            retry_count=final_result.retry_count,
            validation_passed=final_result.validation_passed,
            confidence=final_result.confidence,
            queue_wait_ms=final_result.queue_wait_ms,
            error_code=final_result.error_code,
            routing_metadata=final_result.routing_metadata,
            input_tokens=final_result.input_tokens,
            cached_input_tokens=final_result.cached_input_tokens,
            cache_write_tokens=final_result.cache_write_tokens,
            output_tokens=final_result.output_tokens,
            usage_source=final_result.usage_source,
        )
        await TelemetryService.get_instance().record(trace)

        if request.session_id and settings.context_management_enabled:
            JsonSessionStore(settings.session_store_path, settings.session_max_history, settings.session_summary_max_chars).append(
                request.session_id,
                {"task_id": request.task_id, "provider": trace.provider, "model": trace.model, "outcome": trace.state},
                summary=(final_result.answer or "")[:settings.session_summary_max_chars],
            )
        
        return final_result

    async def shutdown(self):
        await self.local_exec.shutdown()
        await self.api_exec.shutdown()
        await self.openai_exec.shutdown()
        await self.anthropic_exec.shutdown()

async def main(argv=None):
    from core.batch import cli
    return await cli(argv)

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
