import { useEffect, useRef, useState } from "react";
import { GATEWAY, postJSON, streamSSE } from "../api.js";
import Surface from "../a2ui/Surface.jsx";
import { useSurfaces } from "../a2ui/store.js";

const SUGGESTIONS = [
  "Show me restaurants",
  "Which restaurants have biryani?",
  "2 butter chicken and 3 garlic naan",
  "biryani for 2 under 700 within 45 minutes",
];

// One id per browser, so the gateway remembers this customer's order between messages.
function sessionId() {
  try {
    let id = localStorage.getItem("food-agents-session");
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem("food-agents-session", id);
    }
    return id;
  } catch {
    return crypto.randomUUID();
  }
}

export default function Chat() {
  const [session] = useState(sessionId);
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [surfaces, apply] = useSurfaces();
  const endRef = useRef(null);
  const counter = useRef(0);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  // Send something to the gateway and show whatever streams back as one agent turn.
  async function run(path, body, userText, firstStatus) {
    const id = `t${++counter.current}`;
    setTurns((all) => [
      ...all,
      ...(userText ? [{ id: `${id}u`, role: "user", text: userText }] : []),
      { id, role: "agent", text: "", status: firstStatus, surfaceIds: [] },
    ]);
    setBusy(true);
    const patch = (change) => setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...change(t) } : t)));
    const show = (messages) => {
      if (!messages) return;
      apply(messages);
      const created = messages.filter((m) => m.createSurface).map((m) => m.createSurface.surfaceId);
      if (created.length) patch((t) => ({ surfaceIds: [...t.surfaceIds, ...created] }));
    };

    try {
      await streamSSE(path, { session_id: session, ...body }, (event, data) => {
        if (event === "update") {
          show(data.a2ui);
          if (!data.a2ui && data.text) patch(() => ({ status: data.text }));
        } else if (event === "reply") {
          show(data.a2ui);
          patch(() => ({ status: null, text: data.text, plain: !data.a2ui }));
        } else if (event === "error") {
          patch(() => ({ status: null, error: data.message }));
        }
      });
    } catch {
      patch(() => ({ status: null, error: `Can't reach the gateway at ${GATEWAY}. Is scripts.run_all running?` }));
    } finally {
      patch(() => ({ status: null }));
      setBusy(false);
    }
  }

  const send = (message) => {
    const clean = message.trim();
    if (!clean || busy) return;
    setText("");
    run("/api/chat", { message: clean }, clean, "Asking Swiggy and Zomato");
  };

  // A click inside an A2UI surface arrives here as an A2UI action message.
  const onAction = (message, label) => {
    if (!busy) run("/api/chat/action", message, label, "Working on it");
  };

  async function newOrder() {
    try {
      await postJSON("/api/chat/reset", { session_id: session });
    } catch {
      /* the gateway being down is shown on the next message */
    }
    setTurns([]);
  }

  // A surface that is updated in place (the cart, an order tracker) is drawn once: in the newest turn that mentions it.
  const lastTurnWith = (sid) => turns.reduce((last, t, i) => (t.surfaceIds?.includes(sid) ? i : last), -1);

  return (
    <main className="page chat">
      <div className="thread">
        {turns.length === 0 && (
          <section className="hero">
            <h1>What are you hungry for?</h1>
            <p>
              Ask for anything: browse the restaurants on Swiggy or Zomato, open a menu and add dishes to your cart, or name
              the dishes and I will find the best deal across both.
            </p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </section>
        )}

        {turns.map((t, index) =>
          t.role === "user" ? (
            <div key={t.id} className="turn turn-user">
              <p className="bubble">{t.text}</p>
            </div>
          ) : (
            <div key={t.id} className="turn turn-agent">
              {t.text && <p className={t.plain ? "reply reply-plain" : "reply"}>{t.text}</p>}
              {t.surfaceIds
                .filter((sid) => lastTurnWith(sid) === index)
                .map((sid) => surfaces[sid] && <Surface key={sid} surface={surfaces[sid]} onAction={onAction} />)}
              {t.status && (
                <p className="status">
                  <span className="pulse" />
                  {t.status}
                </p>
              )}
              {t.error && <p className="error">{t.error}</p>}
            </div>
          )
        )}
        <div ref={endRef} className="thread-end" />
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={busy ? "Waiting for the answer…" : "e.g. 2 butter chicken and 3 garlic naan"}
          disabled={busy}
          aria-label="Your order"
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>
          Send
        </button>
        {turns.length > 0 && (
          <button type="button" className="btn btn-quiet" onClick={newOrder} disabled={busy}>
            New order
          </button>
        )}
      </form>
    </main>
  );
}