import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource-variable/figtree";
import "./styles.css";
import App from "./App.jsx";
import { startAuth } from "./auth.js";

const root = document.getElementById("root");

// Nothing is drawn until the customer is logged in. If nobody is, startAuth() sends the browser to
// Keycloak's login page and the app starts again after the login.
startAuth()
  .then(() => {
    createRoot(root).render(
      <React.StrictMode>
        <App />
      </React.StrictMode>
    );
  })
  .catch((error) => {
    root.textContent = `Sign-in failed (${error?.message || error || "no details"}). Is Keycloak running on http://localhost:8080?`;
  });