import { useState } from "react";
import Chat from "./pages/Chat.jsx";
import Agents from "./pages/Agents.jsx";
import { currentUser, logout } from "./auth.js";

export default function App() {
  const [tab, setTab] = useState("order");
  const user = currentUser();
  // Talking to agents directly is a developer tool: the gateway only allows users with the "developer" role.
  const isDeveloper = user.roles.includes("developer");
  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <span className="brand">food agents</span>
          <nav className="tabs" aria-label="Pages">
            <button type="button" className={tab === "order" ? "tab tab-on" : "tab"} onClick={() => setTab("order")}>
              Order food
            </button>
            {isDeveloper && (
              <button type="button" className={tab === "agents" ? "tab tab-on" : "tab"} onClick={() => setTab("agents")}>
                Talk to an agent
              </button>
            )}
          </nav>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
            <span className="muted small">{user.name}</span>
            <button type="button" className="btn btn-quiet" onClick={logout}>
              Log out
            </button>
          </div>
        </div>
      </header>
      {/* both pages stay mounted so switching tabs never loses a conversation */}
      <div hidden={tab !== "order"}>
        <Chat />
      </div>
      {isDeveloper && (
        <div hidden={tab !== "agents"}>
          <Agents />
        </div>
      )}
    </div>
  );
}