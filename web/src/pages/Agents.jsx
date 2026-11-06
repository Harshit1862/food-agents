import { useEffect, useState } from "react";
import { getJSON, streamSSE } from "../api.js";
import Surface from "../a2ui/Surface.jsx";
import { useSurfaces } from "../a2ui/store.js";

// An agent card lists examples for each skill: a sentence ("Show me the menu") and the JSON form.
const isJSON = (x) => x.trim().startsWith("{");
const sentenceOf = (skill) => skill?.examples?.find((x) => !isJSON(x)) || "";
const jsonOf = (skill) => skill?.examples?.find(isJSON) || "";

const pretty = (text) => {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
};

export default function Agents() {
  const [agents, setAgents] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [mode, setMode] = useState("words"); // "words" = plain language, "json" = a structured request
  const [words, setWords] = useState("");
  const [payload, setPayload] = useState("");
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);
  const [surfaces, apply, reset] = useResettableSurfaces();

  async function load() {
    try {
      setAgents(await getJSON("/api/agents"));
      setError(null);
    } catch {
      setError("Can't reach the gateway. Is scripts.run_all running?");
    }
  }
  useEffect(() => {
    load();
  }, []);

  const agent = agents?.find((a) => a.id === selected);

  function choose(a) {
    setSelected(a.id);
    setEvents([]);
    reset();
    const first = a.skills?.[0];
    setWords(sentenceOf(first));
    setPayload(jsonOf(first) ? pretty(jsonOf(first)) : '{"action": ""}');
  }

  async function send() {
    let body;
    if (mode === "words") {
      if (!words.trim()) return;
      body = { text: words.trim() };
    } else {
      try {
        body = { payload: JSON.parse(payload) };
      } catch {
        setEvents([{ kind: "error", text: "That is not valid JSON." }]);
        return;
      }
    }
    setEvents([]);
    reset();
    setRunning(true);
    try {
      await streamSSE(`/api/agents/${selected}/send`, body, (kind, data) => {
        apply(data.a2ui);
        if (kind === "status" && data.data?.tick) return; // the once-a-second countdown: shown by the tracker
        setEvents((all) => [...all, { kind, ...data }]);
      });
    } catch (e) {
      setEvents((all) => [...all, { kind: "error", text: String(e.message || e) }]);
    } finally {
      setRunning(false);
    }
  }

  const groups = [
    ["Restaurants", agents?.filter((a) => a.kind === "restaurant")],
    ["Delivery platforms", agents?.filter((a) => a.kind === "platform")],
  ];

  return (
    <main className="page agents">
      <aside className="agent-list">
        <h2 className="side-title">Agents</h2>
        {error && <p className="error">{error}</p>}
        {!agents && !error && <p className="muted">Loading…</p>}
        {groups.map(([title, list]) =>
          list?.length ? (
            <div key={title} className="agent-group">
              <h3 className="group-title">{title}</h3>
              {list.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className={`agent-pick ${selected === a.id ? "agent-pick-on" : ""}`}
                  onClick={() => choose(a)}
                >
                  <span className={`dot ${a.up ? "dot-up" : "dot-down"}`} title={a.up ? "Running" : "Not answering"} />
                  {a.name || a.id}
                </button>
              ))}
            </div>
          ) : null
        )}
        <button type="button" className="btn btn-quiet" onClick={load}>
          Refresh
        </button>
      </aside>

      <section className="agent-work">
        {!agent && (
          <div className="hero hero-small">
            <h1>Talk to an agent directly</h1>
            <p>
              Pick a restaurant to read its menu, or Swiggy or Zomato to ask for quotes. Write what you want in plain words;
              the agent works out which of its skills you mean (the screen shows how it understood you). Or switch to JSON
              and send the exact request yourself.
            </p>
          </div>
        )}

        {agent && (
          <>
            <header className="agent-head">
              <h2>{agent.name}</h2>
              <p className="muted">{agent.description}</p>
              <p className="muted small">{agent.url}</p>
            </header>
            {!agent.up && <p className="error">This agent is not answering right now.</p>}

            <div className="skills">
              {agent.skills?.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className="chip"
                  title={s.description}
                  onClick={() => {
                    if (mode === "words") setWords(sentenceOf(s) || s.name);
                    else setPayload(jsonOf(s) ? pretty(jsonOf(s)) : `{"action": "${s.id}"}`);
                  }}
                >
                  {s.name}
                </button>
              ))}
            </div>

            <div className="skills">
              {[
                ["words", "Plain words"],
                ["json", "JSON"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  className="chip"
                  style={mode === id ? { borderColor: "var(--primary)", fontWeight: 700 } : undefined}
                  onClick={() => setMode(id)}
                >
                  {label}
                </button>
              ))}
            </div>

            {mode === "words" ? (
              <label className="field">
                <span>Say what you want</span>
                <textarea
                  value={words}
                  onChange={(e) => setWords(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      if (!running && agent.up) send();
                    }
                  }}
                  rows={3}
                  placeholder="e.g. veg dishes under 250"
                  style={{ fontFamily: "inherit", fontSize: "1rem" }}
                />
              </label>
            ) : (
              <label className="field">
                <span>Request (JSON)</span>
                <textarea value={payload} onChange={(e) => setPayload(e.target.value)} rows={8} spellCheck="false" />
              </label>
            )}
            <div>
              <button type="button" className="btn btn-primary" onClick={send} disabled={running || !agent.up}>
                {running ? "Waiting for the agent" : "Send request"}
              </button>
            </div>

            {Object.values(surfaces).map((s) => (
              <Surface key={s.id} surface={s} />
            ))}

            {events.length > 0 && (
              <ol className="events">
                {events.map((e, i) => (
                  <li key={i} className={`event event-${e.kind}`}>
                    <div className="event-head">
                      <span className="event-kind">{e.kind === "artifact" ? `result: ${e.name}` : e.kind}</span>
                      {e.state && <span className="event-state">{e.state}</span>}
                    </div>
                    {(e.text || e.message) && <p className="event-text">{e.text || e.message}</p>}
                    {e.data && (
                      <details>
                        <summary>Data</summary>
                        <pre>{JSON.stringify(e.data, null, 2)}</pre>
                      </details>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </section>
    </main>
  );
}

// useSurfaces plus a way to clear everything when a new request starts
function useResettableSurfaces() {
  const [surfaces, apply] = useSurfaces();
  const [, force] = useState(0);
  const reset = () => {
    for (const id of Object.keys(surfaces)) apply([{ deleteSurface: { surfaceId: id } }]);
    force((n) => n + 1);
  };
  return [surfaces, apply, reset];
}
