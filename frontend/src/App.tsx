import { FormEvent, useEffect, useState } from "react";

type Mode = "automatic" | "local" | "cloud";
type Task = {
  task_id: string;
  status: string;
  result?: { answer: string; route_taken?: string; state: string; fireworks_tokens: number; error_code?: string };
  error?: string;
};

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

function App() {
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<Mode>("automatic");
  const [task, setTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState("checking");
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API}/api/health`).then((res) => setConnection(res.ok ? "online" : "offline"))
      .catch(() => setConnection("offline"));
  }, []);

  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    const timer = window.setInterval(async () => {
      const response = await fetch(`${API}/api/tasks/${task.task_id}`);
      if (response.ok) {
        const next = await response.json() as Task;
        setTask(next);
        if (!["queued", "running"].includes(next.status)) setBusy(false);
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
      if (!response.ok) throw new Error((await response.json()).detail ?? "Task submission failed");
      setTask(await response.json() as Task);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to reach RelayOS API");
      setBusy(false);
    }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">R</span><div><strong>RelayOS</strong><small>execution console</small></div></div>
      <button className="new-task" onClick={() => { setPrompt(""); setTask(null); setError(""); }}>＋ New task</button>
      <nav><span className="nav-label">WORKSPACE</span><a className="active">⌁ Console</a><a>◷ Task history</a><a>⌘ Benchmarks</a></nav>
      <div className="sidebar-foot"><span className={`status-dot ${connection}`} /> API {connection}</div>
    </aside>
    <main className="workspace">
      <header><div><span className="eyebrow">DEVELOPER WORKSPACE</span><h1>Route work with confidence.</h1><p>RelayOS selects deterministic, local, or cloud execution and shows what actually happened.</p></div><div className="header-chip">● local-first routing</div></header>
      <section className="content-grid">
        <form className="composer panel" onSubmit={submit}>
          <div className="panel-head"><span>New task</span><span className="mono">POST /api/tasks</span></div>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Ask RelayOS to solve, analyze, summarize, or debug…" maxLength={12000} />
          <div className="composer-foot"><span>{prompt.length.toLocaleString()} / 12,000</span><button className="send" disabled={busy || !prompt.trim()}>{busy ? "Running…" : "Run task  ↗"}</button></div>
          <div className="mode-row"><span>Execution mode</span>{(["automatic", "local", "cloud"] as Mode[]).map((item) => <button type="button" key={item} className={mode === item ? "mode selected" : "mode"} onClick={() => setMode(item)}>{item === "automatic" ? "✦ Automatic" : item === "local" ? "⌂ Local" : "◇ Cloud"}</button>)}</div>
        </form>
        <section className="panel result-panel"><div className="panel-head"><span>Response</span>{task && <span className={`state ${task.status}`}>{task.status}</span>}</div>{!task && <div className="empty"><div className="empty-icon">✦</div><h2>Ready when you are</h2><p>Submit a task to inspect its route, validation, latency, and result.</p></div>}{task && <div className="result-body">{task.result?.answer ? <pre>{task.result.answer}</pre> : <p className="muted">{task.error ?? "Waiting for an execution result…"}</p>}</div>}</section>
      </section>
      {error && <div className="alert">⚠ {error}</div>}
      <section className="inspector panel"><div className="panel-head"><span>Execution inspector</span><span className="mono">observable events only</span></div><div className="inspector-grid"><Metric label="Task ID" value={task?.task_id ?? "—"} mono /><Metric label="Route" value={task?.result?.route_taken ?? "pending"} /><Metric label="State" value={task?.result?.state ?? task?.status ?? "idle"} /><Metric label="Tokens" value={task?.result ? String(task.result.fireworks_tokens) : "—"} /></div></section>
      <footer>RelayOS · deterministic where possible · cloud only when configured</footer>
    </main>
  </div>;
}

function Metric({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div className="metric"><span>{label}</span><strong className={mono ? "mono" : ""}>{value}</strong></div>; }

export default App;
