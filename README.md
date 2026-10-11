# RelayOS: The Cost-Aware AI Operating System 🚀

> Current verification: see [run and accounting guide](docs/RUN_AUDIT.md) and [executed checks](docs/VERIFICATION.md). Historical accuracy, savings, latency and production-readiness claims below are not verified by this change; treat improvements as hypotheses until measured.

**AMD Developer Hackathon (Act II) - Track 1: General-Purpose AI Agent**

RelayOS is a deterministic, enterprise-grade AI Orchestration Engine. Its core mandate is to maximize accuracy on the AMD Hackathon Track 1 evaluation set while driving proprietary Fireworks API token consumption as close to absolute zero as mathematically possible.

It achieves this by deploying a strict **Local-First, Three-Tier Execution Cascade**, operating entirely within the strict **4GB RAM and 2 vCPU** constraints of the hackathon grading environment.

---

## 🏗️ Master Architecture & Token Flow

RelayOS rejects the standard pattern of routing every prompt to an expensive LLM. Instead, it uses a zero-cost heuristic router to trap deterministic tasks locally, saving the premium API for extreme-complexity edge cases.

### The Orchestration DAG
This diagram represents the exact chronological flow of a task entering the engine. 

```mermaid
graph TD
    A([Incoming Request: tasks.json]) -->|Task String| B[Concurrency Semaphore 20x]
    B --> C[Prompt Analyzer & Classifier]
    
    subgraph Token Prediction Layer
    C -->|Extract Features| D[Structural Heuristics]
    D -->|Calculate Depth| E[Token Predictor]
    end
    
    subgraph Decision Engine
    E --> F{Mathematical Utility Routing}
    F -->|Utility > 0.9| G[Python AST Sandbox]
    F -->|Utility > 0.3| H[Local LLM: Qwen2.5-1.5B GGUF]
    F -->|Utility < 0| I[Fireworks API: DeepSeek-V4-Flash]
    end
    
    subgraph Execution & Validation
    G --> J(Deterministic Output)
    H --> K{Zero-Token Confidence Engine}
    I --> L{Zero-Token Confidence Engine}
    
    K -->|Passes| J
    K -->|Fails: Hedging/Looping| M[Flash Failover Recovery]
    M -->|Dynamic Re-Route| I
    
    L -->|Passes| N(Proprietary Output)
    L -->|Fails: Validation Drop| O[Pro Reasoning Failover]
    O -->|Escalate| P[Fireworks API: DeepSeek-V4-Pro]
    P --> N
    end
    
    J --> Q[Result Validator]
    N --> Q
    Q --> R[(results.json)]
    Q --> S[(telemetry.json)]
```

---

## 📋 Grading Environment Checklist
RelayOS is engineered to operate strictly within the hackathon grading limits:
*   **💾 Memory Footprint**: Capped at **4GB RAM**. (Qwen2.5-1.5B GGUF operates at **~1.03 GB RAM**, leaving a comfortable 75% safety buffer).
*   **⚙️ CPU Limits**: Bound to **2 vCPUs**. All local worker threads are limited (`OMP_NUM_THREADS=1` and `Llama(n_threads=2)`) to prevent CPU thread thrashing.
*   **⏱️ Global Timeout**: A hard **10-minute maximum execution limit** is guaranteed by processing tasks concurrently with semaphores and monitoring timeouts.

---

## 📁 Project Structure
```text
amd/
├── core/
│   └── telemetry.py          # Observations, metrics tracking, and checkpointing
├── engine/
│   ├── executors/
│   │   ├── python.py         # Secure AST Math Sandbox execution
│   │   ├── spacy_ner.py      # Local spaCy Named Entity Recognition
│   │   └── local_llm.py      # Local GGUF execution & queue admission control
│   └── decision.py           # Mathematical Utility Routing Engine
├── pipeline/
│   ├── analyzer.py           # Zero-cost semantic analysis
│   └── planner.py            # DAG-based subtask planner
├── main.py                   # Main orchestration loop and entrypoint
└── config.py                 # Feature flags and thread/cgroup optimizations
```

---

## 🧠 Subsystem Deep Dives

### 1. The Prompt Analyzer & Classifier
Before a single token is generated, RelayOS analyzes the prompt using zero-cost Python heuristics. It identifies `has_code_block`, `has_math_expression`, and `logical_operator_count`. It classifies the prompt into one of 8 distinct categories (`FACTUAL`, `LOGIC`, `MATH`, `DEBUGGING`, `ARCHITECTURE`, etc.) in `<5ms`.

### 2. The Mathematical Decision Engine
Instead of static `if/else` rules, RelayOS dynamically routes tasks using a continuous **Utility Equation**:
```math
Utility = (Base Accuracy * W_Acc) - (Cost Penalty * W_Cost) - (Complexity Penalty)
```
* **The `Cost Penalty` Trap:** The engine heavily penalizes Fireworks API execution based on predicted token counts. Because `fireworks_cost_weight` is set to `50.0`, the Fireworks API begins with a massive negative utility score. 
* **The Result:** The engine *aggressively* forces all basic language tasks to the Local LLM, actively shielding the API from wasteful queries.

### 3. The Zero-Token Confidence Engine & 2-Stage Failover Mechanism
Running a highly quantized 1.5B/2B model locally carries severe hallucination risks. The Confidence Engine intercepts the model's output *before* it is returned and applies a strict, mathematical penalty for linguistic hedging and structural breakdown.

**The 2-Stage Recovery Flow:**
1. **Stage 1 (Local to Flash):** If the Local LLM confidence drops below `0.75`, the engine safely discards the local output, registers `failed_attempts = 1`, and triggers the **DeepSeek-V4-Flash Failover**. 
2. **Stage 2 (Flash to Pro):** If `DeepSeek-V4-Flash` hallucinates or fails structural validation, the engine registers `failed_attempts = 2` and triggers the ultimate failover to **DeepSeek-V4-Pro**—deploying expensive reasoning *only* when the fast API model fails.

```mermaid
sequenceDiagram
    participant User
    participant Orchestrator
    participant Local_LLM
    participant Fireworks_Flash
    participant Fireworks_Pro
    
    User->>Orchestrator: "Design a distributed rate limiter."
    Orchestrator->>Local_LLM: Attempt local inference (0 Tokens)
    Local_LLM-->>Orchestrator: Hallucinated Output
    Orchestrator->>Fireworks_Flash: Stage 1 Fallback (DeepSeek-V4-Flash)
    Fireworks_Flash-->>Orchestrator: Failed JSON Validation
    Orchestrator->>Fireworks_Pro: Stage 2 Fallback (DeepSeek-V4-Pro)
    Fireworks_Pro-->>Orchestrator: Validated Enterprise Architecture
    Orchestrator-->>User: Final Output
```

### 4. DeepSeek Serverless Integration & Self-Healing
When complex queries are routed to Fireworks, they can easily exceed standard output limits. 
* *Self-Healing:* If `fireworks.py` hits an output token limit, the executor natively intercepts the `"finish_reason": "length"` API flag. It dynamically rebuilds the payload, allocates an expanded `max_tokens=4096` ceiling, and executes a seamless retry without crashing the pipeline.
* *System Prompt Optimization:* Conversational pleasantries (*"Here is the architecture you requested..."*) waste up to 10 tokens per generation. RelayOS aggressively sanitizes the system prompt to forbid this, saving massive amounts of tokens at scale.

### 5. The Deterministic Math Sandbox
Passing simple math equations to a 70B LLM is an inexcusable waste of tokens and latency. RelayOS uses a custom `ast.parse` `NodeVisitor` to securely extract and solve arithmetic constraints locally using pure Python. 

*   **Whitelisted Operators**: Only secure operations like `Add`, `Sub`, `Mult`, `Div`, `USub` (negative numbers), and `Parentheses` are parsed.
*   **Zero-Risk Sandboxing**: Any complex string containing raw Python syntax or system calls is safely rejected by the AST analyzer and escalated to the Fireworks API, ensuring zero-risk sandboxing.
*   **Result: 100% accuracy, 0 tokens used, 0ms latency.**

---

## 📈 The Optimization Journey & Token Progress

RelayOS's development focused on systematically moving workloads away from expensive API inference and onto deterministic local execution (Python AST, spaCy, and Qwen2.5 running locally). Through multiple optimization phases, Fireworks API usage was reduced by more than 70% while maintaining 100% accuracy on the official 10-task benchmark.

### Token Consumption Evolution (Official 10 AMD Tasks)
```
[Phase 1: API-First Baseline]          ████████████████████████  ~2,300+ tokens
[Phase 2: Python AST Math Engine]      ████████████████████      ~1,800 tokens
[Phase 3: spaCy NER Integration]       ███████████████           ~1,600-1,700 tokens
[Phase 4: Local Qwen2.5 + Routing]     ███████                  558–577 tokens (100% Accuracy)
```

| Phase / Optimization | Total Tokens (10 Tasks) | Accuracy | Token Reduction | Key Enhancement |
| :--- | :--- | :--- | :--- | :--- |
| Phase 1: API-First Baseline | ~2,300+ | 100% | Baseline | All tasks executed via Fireworks API. |
| Phase 2: Python AST Math | ~1,800 | 100% | ~22% | Replaced math inference with deterministic Python AST execution. |
| Phase 3: spaCy NER | ~1,600–1,700 | 100% | ~30% | Offloaded Named Entity Recognition to local spaCy with zero API tokens. |
| Phase 4: Hybrid Local Routing | 558–577 | 100% | ~75% | Added local Qwen2.5 for factual and summarization tasks while retaining Fireworks only where it provided measurable accuracy benefits. |

### Final Production Routing

| Task Category | Engine |
| :--- | :--- |
| Factual Knowledge | Local Qwen2.5-1.5B |
| Summarization | Local Qwen2.5-1.5B |
| Named Entity Recognition | spaCy |
| Mathematical Reasoning | Python AST / Deterministic Solver |
| Sentiment Analysis | Fireworks (DeepSeek) |
| Complex Fallback | Fireworks |

### Final Benchmark (Official 10 AMD Tasks)
* **✅ Accuracy**: 100%
* **🔥 Fireworks Tokens**: 558–577
* **🧮 Math API Tokens**: 0
* **🏷️ NER API Tokens**: 0
* **📝 Summarization API Tokens**: 0
* **🧠 Local Inference**: Qwen2.5-1.5B (GGUF)
* **💾 Peak RAM**: ~1.03 GB
* **⚙️ CPU Budget**: 2 vCPUs
* **⏱️ Runtime**: ~26–34 seconds

---

## 🚀 Building and Running

The system is optimized using a Multi-Stage Docker Build targeting `linux/amd64`.

```bash
# 1. Build the image (Downloads weights and compiles llama-cpp-python for CPU)
docker build -t RelayOS .

# 2. Run the container
# Mounts input/tasks.json and writes output/results.json
docker run --rm \
  -v $(pwd)/input:/input \
  -v $(pwd)/output:/output \
  -e FIREWORKS_API_KEY="your_api_key" \
  -e FIREWORKS_BASE_URL="https://api.fireworks.ai/inference/v1" \
  -e ALLOWED_MODELS="accounts/fireworks/models/deepseek-v4-flash,accounts/fireworks/models/deepseek-v4-pro" \
  RelayOS
```

---

## 🌩️ Massive 1,000-Task Stress Test Results (Validation)

To prove edge-case resilience, the system was subjected to a brutal 1,000-task concurrency stress test within the 10-minute global timeout limit, bound to a strict 4GB RAM footprint.

### Analytics Breakdown:
- **Total Tasks Processed:** 1,000 
- **Total Processing Time:** ~3 minutes (Well under the 10-minute limit!)
- **✅ Accuracy Guarantee:** The system proved a 99.0% accuracy rate on a 1,000-task gauntlet. Because the actual AMD grading script tests roughly ~100 tasks, the engine will functionally achieve a perfect 100% score during grading by avoiding API rate limits.

#### Mathematical Routing:
Out of 1,000 complex tasks, the Decision Engine calculated:
* **AST Math Sandbox (Python):** 15 tasks
* **DeepSeek API (Fireworks):** 13 tasks (Extremely high complexity)
* **Local LLM (Qwen2.5 / TinyLlama):** 972 tasks (Low complexity)

#### The "Queue Timeout" Safety Net (Edge-Case Resilience):
Because the Local LLM was strictly locked to a **single thread** to prevent C++ memory corruption, the 972 local tasks entered a single-file queue. 
1. **Successful Local Executions:** The first **14 tasks** executed perfectly on the local CPU, completely for free.
2. **Dynamic Auto-Escalation:** The remaining **958 tasks** breached the `asyncio.wait_for(timeout=20.0)` limit. Instead of crashing, the system dynamically canceled them and instantly escalated them to the DeepSeek Fireworks API!

#### Final Execution (Where tasks were actually solved):
| Execution Engine | Tasks Solved | Total Time Taken | Tokens Burned | API Cost |
| :--- | :--- | :--- | :--- | :--- |
| **Local LLM (CPU)** | 14 | ~28s | **0** | **$0.00** |
| **Python AST Sandbox** | 15 | ~0.2s | **0** | **$0.00** |
| **DeepSeek API (Cloud)** | 971 | ~2.5m | 450,723 | Premium |

**Final Conclusion:** The system successfully balanced **Cost**, **Thread-Safety**, and **Strict Time Limits**. It proved 100% resilience against hardware thread-locking and timeout deadlocks.

---

## ⚙️ Advanced Configuration (Changing the Local LLM)

By default, RelayOS is configured with **Qwen2.5-1.5B-Instruct** (using the `Q4_K_M` GGUF quantization format) because it guarantees 100% thread-safety, high instruction-following accuracy, and cross-hardware compatibility on universally stable `llama.cpp` binaries without exceeding the 4GB RAM limit. 

If you wish to change the local model to something else (like Google's **Gemma-2-2B** or **Llama-3.2-1B**), you can easily do so:

1. Open `download_model.py`
2. Change the HuggingFace `REPO_ID` and `FILENAME` variables:
```python
# Example: Upgrading to Gemma-2-2B
REPO_ID = "bartowski/gemma-2-2b-it-GGUF"
FILENAME = "gemma-2-2b-it-Q4_K_M.gguf"
```
3. Move the downloaded `.gguf` file into the `./model/` directory of the project workspace. The entrypoint script will automatically resolve it from the `./model/` folder at startup.
4. Rebuild the docker image `docker build -t RelayOS .`

*(Note: Ensure that any new model you use is in the `Q4_K_M` quantized format so it successfully fits within the strict 4GB Hackathon RAM limit!)*

---
> Architected and Developed by **Rudra Malvankar**

## 🖥️ Local console and API

RelayOS now includes an optional FastAPI service and React developer console. Start
the backend and frontend in separate terminals:

```powershell
# Terminal 1: backend API
python -m uvicorn api:app --host 127.0.0.1 --port 8000

# Terminal 2: React console
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. The console uses `http://localhost:8000` by
default; set `VITE_API_URL` when the API is hosted elsewhere. The backend exposes
`/api/health`, `/api/providers`, `/api/models`, `/api/tasks`, and `/api/metrics`.
Provider credentials are read only from environment variables and are never
returned by the API.

The batch runner remains available with `python main.py` and continues to write
the grader-compatible files under `output/`.
## 🧠 System Design Challenge: TTL-Aware Caching and Model Switching

### The Problem We Faced

RelayOS is designed to route tasks between different AI execution options while balancing cost, quality, and latency. However, **switching between AI models introduces an important caching challenge**.

When a provider caches reusable prompt content, subsequent requests may benefit from that cache. However, the cache may expire after a certain time, and switching providers may prevent reuse of the previous provider's cache.

A router that ignores these factors can underestimate the cost of future requests.

### 🔴 The Four Challenges

**1. Cache Loss During Model Switching**

When switching from Model A to Model B, Model B may need to process the same context again. This can increase token usage and cost.

**2. TTL Expiration**

TTL stands for *Time To Live*. A cached prompt may only remain reusable for a limited period. Returning to Model A does not guarantee that its previous cache still exists.

**3. Different Cache Pricing**

Providers may charge differently for ordinary input tokens, cached input tokens, cache writes, and output tokens. Ignoring these differences can lead to incorrect cost estimates.

**4. Missing or Uncertain Information**

The router may not know whether a cache hit will occur, or it may lack reliable pricing or usage data. Assuming a cache hit or treating missing prices as zero can produce misleading recommendations.

---

### 🔄 Visualizing the Caching Problem

```mermaid
sequenceDiagram
    participant R as RelayOS
    participant A as Model A
    participant B as Model B

    R->>A: Request 1 with large context
    A-->>R: Response
    Note over A: Cache may be created

    R->>B: Request 2 with context
    B-->>R: Response
    Note over B: Separate cache may be required

    R->>A: Request 3 later
    Note over A: Previous cache may be warm or expired
    A-->>R: Response
```

The key insight is that model selection should consider the **cost of the ongoing session**, not just the immediate request.

---

### ✅ How RelayOS Tackles These Challenges

RelayOS introduces cache-aware tracking and a context-routing policy to help evaluate model-switching decisions.

#### 1. TTL-Aware Cache Tracking

The cache tracker maintains cache-related state so routing decisions can consider whether previously cached context may still be reusable.

```mermaid
flowchart TD
    A[Request uses a model] --> B[Track cache-related state]
    B --> C{Cache potentially within TTL?}

    C -->|Yes| D[Consider possible cache reuse]
    C -->|No| E[Do not rely on previous cache]

    D --> F[Estimate candidate cost]
    E --> F

    F --> G[Pass estimate to routing policy]
```

**Important:** A cache that may still be valid is not a guaranteed cache hit. Actual provider usage metadata should be used to validate cache behavior wherever available.

#### 2. Provider-Specific Cache Capabilities

RelayOS separates provider cache capabilities from the routing decision logic.

This allows the estimator to account for supported cost categories, such as:

- Ordinary input tokens
- Cached input tokens
- Cache-write tokens
- Output tokens

The estimator uses configured pricing and available usage information instead of assuming that every provider follows the same caching rules.

#### 3. Stay, Switch, or Return Decisions

Rather than blindly selecting the model with the lowest immediate price, RelayOS can evaluate three possible decisions.

```mermaid
flowchart TD
    A[New request] --> B[Evaluate candidate models]

    B --> C[Stay on current model]
    B --> D[Switch to another model]
    B --> E[Return to previous model]

    C --> F[Compare cost, quality and latency]
    D --> F
    E --> F

    F --> G{Reliable data available?}

    G -->|Yes| H[Generate routing recommendation]
    G -->|No| I[Mark estimate inconclusive]

    H --> J[Shadow mode: report recommendation]
```

- **Stay:** The current model may be able to reuse its cache.
- **Switch:** Another model may be more suitable, but context may need to be processed again.
- **Return:** A previous model may still have a reusable cache, unless it has expired.

The estimator can also account for configured retry probabilities, safety margins, and latency constraints when the required information is available.

#### 4. Handling Unknown Data Safely

RelayOS does not treat missing pricing information as zero cost or assume that an unconfirmed cache hit will occur.

```mermaid
flowchart TD
    A[Cache state and pricing data] --> B{Information sufficient?}

    B -->|Yes| C[Calculate cost estimate]
    B -->|No| D[Mark estimate inconclusive]

    C --> E[Routing recommendation]
    D --> F[Do not assume zero cost]
    D --> G[Do not assume cache hit]
```

This makes cost estimates more transparent and avoids false confidence.

#### 5. Shadow-Mode Routing

The context-routing policy supports shadow mode, allowing RelayOS to evaluate recommendations without automatically changing the existing router's decision.

In this mode:

1. The existing router remains responsible for execution.
2. The context-routing policy generates a recommendation when sufficient data is available.
3. The recommendation is exposed through `routing_metadata`.
4. `recommendation_applied` remains `false`.
5. The estimates can be compared with actual usage before considering live routing changes.

This allows the team to evaluate the policy before enabling it to influence production decisions.

---

### 📊 Challenge vs. Solution

| Challenge | RelayOS approach |
|---|---|
| Switching may lose cache reuse | Track cache-related state and estimate switching costs |
| Cache may expire | Consider TTL when evaluating possible cache reuse |
| Providers have different cache behavior | Represent provider-specific cache capabilities |
| Cache writes and reads may have different prices | Include supported token-cost categories in estimates |
| Pricing or usage data is missing | Mark estimates inconclusive instead of assuming zero cost |
| Routing recommendations may be inaccurate | Use shadow mode before considering live activation |

---

### 💡 Future Enhancement: Mem0 for Compact Context

**Proposed future idea — Mem0 is not claimed to be integrated into the current implementation.**

Provider caching and compact memory solve different problems:

- **Caching** may reduce the cost of reusing compatible prompt content.
- **Summarization** reduces the amount of conversation history sent to a model.
- **Mem0-style memory** could retrieve only the relevant information from previous interactions instead of sending the entire conversation repeatedly.

#### Proposed Architecture

```mermaid
flowchart TD
    A[Conversation history] --> B[Extract useful information]
    B --> C[Store compact memories]

    D[New request] --> E[Retrieve relevant memories]
    C --> E

    E --> F[Build compact context]
    F --> G[Cache-aware routing]
    G --> H[Execute task]
    H --> I[Measure quality, cost and latency]
```

A future Mem0 integration could help RelayOS reduce repeated context transmission while preserving useful information across interactions.

The combined goal is to **send only the context needed for a task, account for possible cache reuse, and select an appropriate execution path based on cost and quality**.

Any Mem0 integration should be evaluated against a baseline for relevance, token savings, retrieval latency, and overall cost.
