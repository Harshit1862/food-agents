// Sign-in with Keycloak (OpenID Connect, "authorization code" flow with PKCE).
//
// The browser never sees the user's password: when nobody is logged in, the page is replaced by
// Keycloak's own login page; after the login Keycloak sends the browser back here with a one-time
// code, and keycloak-js trades that code for tokens. The token is what the gateway checks.

import Keycloak from "keycloak-js";

const keycloak = new Keycloak({
  url: import.meta.env.VITE_KEYCLOAK_URL || "http://localhost:8080",
  realm: "food-agents",
  clientId: "food-web", // the "food-web" client of keycloak/food-agents-realm.json
});

let starting = null;

// Log in, or finish coming back from the login page. Runs once even if React starts twice (StrictMode).
export function startAuth() {
  starting ??= keycloak.init({ onLoad: "login-required", pkceMethod: "S256", checkLoginIframe: false });
  return starting;
}

// A token that is valid right now. Tokens are short-lived, so this renews it when less than 30 s are left.
export async function freshToken() {
  try {
    await keycloak.updateToken(30);
  } catch {
    await keycloak.login(); // the login session is over: ask again
  }
  return keycloak.token;
}

export function currentUser() {
  const t = keycloak.tokenParsed ?? {};
  return {
    name: t.name || t.preferred_username || "",
    username: t.preferred_username || "",
    roles: t.realm_access?.roles ?? [],
  };
}

export const logout = () => keycloak.logout({ redirectUri: window.location.origin });