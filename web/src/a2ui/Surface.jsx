// Draws one A2UI surface: walks the flat component list from "root" down,
// looks each component up in our catalog, and fills in any {"path": ...} bindings.

import { Fragment } from "react";
import { catalog } from "./catalog.jsx";
import { getAtPointer } from "./store.js";

// {"path": "/order/stage"} -> the value in the surface's data model. Everything else stays as is.
function resolve(value, data) {
  if (Array.isArray(value)) return value.map((v) => resolve(v, data));
  if (value && typeof value === "object") {
    const keys = Object.keys(value);
    if (keys.length === 1 && keys[0] === "path") return getAtPointer(data, value.path);
    return Object.fromEntries(keys.map((k) => [k, resolve(value[k], data)]));
  }
  return value;
}

export default function Surface({ surface, onAction }) {
  const { components, data } = surface;

  // A click becomes an A2UI "action" message that goes back to the server.
  const actionMessage = (action, componentId) => ({
    version: "v0.9",
    action: {
      name: action.event.name,
      surfaceId: surface.id,
      sourceComponentId: componentId,
      timestamp: new Date().toISOString(),
      context: resolve(action.event.context ?? {}, data),
    },
  });

  function render(id, trail = []) {
    const component = components[id];
    if (!component || trail.includes(id)) return null;
    const Component = catalog[component.component];
    if (!Component) return <div className="a-unknown">Unknown component: {component.component}</div>;

    const { id: _id, component: _type, children = [], ...props } = component;
    const resolved = resolve(props, data);
    return (
      <Component
        {...resolved}
        onAction={(action, label) => onAction?.(actionMessage(action, id), label)}
      >
        {children.map((childId) => (
          <Fragment key={childId}>{render(childId, [...trail, id])}</Fragment>
        ))}
      </Component>
    );
  }

  return <div className="a2ui-surface">{render("root")}</div>;
}