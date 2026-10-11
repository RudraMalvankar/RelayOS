import { FormEvent, useEffect, useState } from "react";

type Mode = "automatic" | "local" | "cloud";
type Task = {
  task_id: string;
  status: string;
  result?: { answer: string; route_taken?: string; state: string; fireworks_tokens: number; error_code?: string; error_message?: string };
  error?: string;
};

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

function errorMessage(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail.map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : "")).filter(Boolean);
    if (msgs.length) return msgs.join("; ");
  }
  return "Task submission failed";
}

function App() {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Mode>("automatic");
  const [task, setTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState("checking");
  const [error, setError] = useState("");

  useEffect(() => {
    const check = () => fetch(`${API}/api/health`)
      .then((res) => setConnection(res.ok ? "online" : "offline"))
      .catch(() => setConnection("offline"));
    check();
    const timer = window.setInterval(check, 10_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    let failures = 0;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API}/api/tasks/${task.task_id}`);
        if (response.status === 404) {
          setError("Task not found on the server (it may have been restarted). Submit it again.");
          setBusy(false); setTask(null);
          return;
        }
        if (!response.ok) throw new Error(`Status check failed (${response.status})`);
        failures = 0;
        const next = await response.json() as Task;
        setTask(next);
        if (!["queued", "running"].includes(next.status)) setBusy(false);
      } catch {
        failures += 1;
        if (failures >= 3) {
          setError("Lost connection to the RelayOS API while waiting for the result.");
          setBusy(false); setTask(null);
        }
      }
    }, 700);
    return () => window.clearInterval(timer);
  }, [task]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || busy) return;
    setBusy(true); setError(""); setTask(null);
    try {
      const response = await fetch(`${API}/api/tasks`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: prompt.trim() }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(errorMessage((body as { detail?: unknown }).detail));
      }
      setTask(await response.json() as Task);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to reach RelayOS API");
      setBusy(false);
    }
  }

  async function cancel() {
    if (!task) return;
    try {
      const response = await fetch(`${API}/api/tasks/${task.task_id}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(`Cancel failed (${response.status})`);
      setTask(await response.json() as Task);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to cancel the task");
    } finally {
      setBusy(false);
    }
  }

  const active = !!task && ["queued", "running"].includes(task.status);
  const result = task?.result;
  const failureTitle = task?.status === "cancelled" ? "Cancelled" : result?.error_code ?? (task?.error ? "Task failed" : "No answer");
  const failureText = result?.error_message ?? task?.error ??
    (task?.status === "cancelled" ? "The task was cancelled before it returned a result." : "No answer was returned.");
  const connectionLabel = connection === "checking" ? "Checking API…" : connection === "online" ? "API online" : "API offline";

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark" aria-hidden="true">R</span><strong>RelayOS</strong></div>
      <button className="new-task" onClick={() => { setPrompt(""); setTask(null); setError(""); setBusy(false); }}><Icon name="plus" />New task</button>
      <nav aria-label="Workspace"><span className="nav-label">Workspace</span>
        <a className="active" aria-current="page"><Icon name="console" />Console</a>
        <a aria-disabled="true" title="Not available yet"><Icon name="history" />Task history<em>Soon</em></a>
        <a aria-disabled="true" title="Not available yet"><Icon name="bars" />Benchmarks<em>Soon</em></a>
      </nav>
    </aside>
    <main className="workspace">
      <header className="page-head">
        <div><h1>Console</h1><p>Submit a task. RelayOS picks an execution route and reports what ran.</p></div>
        <div className={`conn ${connection}`} role="status"><span className="dot" aria-hidden="true" />{connectionLabel}</div>
      </header>
      <section className="work">
        <form className="composer pane" onSubmit={submit}>
          <div className="pane-head">
            <h2>Task</h2>
            <div className="segmented" role="group" aria-label="Execution mode">
              {(["automatic", "local", "cloud"] as Mode[]).map((item) => <button type="button" key={item} className={mode === item ? "seg selected" : "seg"} aria-pressed={mode === item} disabled={item !== "automatic"} title={item !== "automatic" ? "Not available: the API routes automatically" : undefined} onClick={() => setMode(item)}>{item === "automatic" ? "Automatic" : item === "local" ? "Local" : "Cloud"}</button>)}
            </div>
          </div>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Task prompt" placeholder="Describe the task: solve, analyze, summarize, or debug…" maxLength={12000} />
          <div className="composer-foot">
            <span className="count">{prompt.length.toLocaleString()} / 12,000</span>
            <div className="actions">{busy && task && <button type="button" className="cancel" onClick={cancel}>Cancel</button>}<button className="send" disabled={busy || !prompt.trim()}>{busy && <span className="spinner" aria-hidden="true" />}{busy ? "Running…" : "Run task"}</button></div>
          </div>
        </form>
        <section className="result pane" aria-live="polite">
          <div className="pane-head"><h2>Response</h2>{task && <span className={`state ${task.status}`}>{task.status}</span>}</div>
          <div className="result-body">
            {!task && <div className="empty"><h3>Ready when you are</h3><p>The answer, route and execution state appear here once a task runs.</p></div>}
            {task && result?.answer && <pre className="answer">{result.answer}</pre>}
            {task && !result?.answer && active && <p className="waiting"><span className="spinner" aria-hidden="true" />Waiting for an execution result…</p>}
            {task && !result?.answer && !active && <div className={task.status === "cancelled" ? "notice neutral" : "notice"}><strong>{failureTitle}</strong><p>{failureText}</p></div>}
          </div>
        </section>
      </section>
      {error && <div className="alert" role="alert"><Icon name="alert" /><span>{error}</span></div>}
      <section className="inspector" aria-label="Execution inspector">
        <h2>Execution</h2>
        <dl className="inspector-grid">
          <Metric label="Task ID" value={task?.task_id} mono />
          <Metric label="Route" value={result?.route_taken} />
          <Metric label="State" value={result?.state ?? task?.status} />
          <Metric label="Fireworks tokens" value={result ? String(result.fireworks_tokens) : undefined} />
        </dl>
      </section>
    </main>
  </div>;
}

type IconName = "plus" | "console" | "history" | "bars" | "alert";
const ICONS: Record<IconName, string> = {
  plus: "M8 3.5v9M3.5 8h9",
  console: "M2.5 4.5l3 3-3 3M7.5 11.5h5.5",
  history: "M8 4.5V8l2.5 1.5M13 8a5 5 0 1 1-1.5-3.5M13 2.5v2.5h-2.5",
  bars: "M4 12.5V8M8 12.5v-9M12 12.5V6",
  alert: "M8 5v3.5M8 11h.01M8 2l6 11H2z",
};
function Icon({ name }: { name: IconName }) {
  return <svg className="icon" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICONS[name]} /></svg>;
}

function Metric({ label, value, mono = false }: { label: string; value?: string; mono?: boolean }) {
  return <div className="metric"><dt>{label}</dt><dd className={`${mono ? "mono " : ""}${value ? "" : "none"}`.trim()} title={value}>{value ?? "—"}</dd></div>;
}

export default App;
