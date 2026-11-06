// The renderer's memory: every A2UI surface the server has drawn, and its data.
//
//   surfaces = { [surfaceId]: { id, catalogId, components: { [componentId]: {...} }, data: {...} } }
//
// A2UI v0.9 messages change it:
//   createSurface     starts an empty surface
//   updateComponents  adds or replaces components (matched by id)
//   updateDataModel   sets (or deletes) a value at a JSON-pointer path in the surface's data
//   deleteSurface     removes a surface

import { useCallback, useReducer } from "react";

const keysOf = (path) =>
  path
    .split("/")
    .slice(1)
    .map((k) => k.replace(/~1/g, "/").replace(/~0/g, "~"));

export function getAtPointer(root, path) {
  if (path === "/" || path === "") return root;
  let node = root;
  for (const key of keysOf(path)) {
    if (node === null || typeof node !== "object") return undefined;
    node = node[key];
  }
  return node;
}

function setAtPointer(root, path, value) {
  if (path === "/" || path === "") return value === undefined ? {} : value;
  const next = structuredClone(root ?? {});
  const keys = keysOf(path);
  let node = next;
  for (const key of keys.slice(0, -1)) {
    if (node[key] === null || typeof node[key] !== "object") node[key] = {};
    node = node[key];
  }
  const last = keys[keys.length - 1];
  if (value === undefined) delete node[last];
  else node[last] = value;
  return next;
}

export function applyMessage(state, message) {
  if (message.createSurface) {
    const { surfaceId, catalogId } = message.createSurface;
    return { ...state, [surfaceId]: { id: surfaceId, catalogId, components: {}, data: {} } };
  }
  if (message.updateComponents) {
    const { surfaceId, components } = message.updateComponents;
    const surface = state[surfaceId];
    if (!surface) return state;
    const merged = { ...surface.components };
    for (const component of components) merged[component.id] = component;
    return { ...state, [surfaceId]: { ...surface, components: merged } };
  }
  if (message.updateDataModel) {
    const { surfaceId, path = "/", value } = message.updateDataModel;
    const surface = state[surfaceId];
    if (!surface) return state;
    return { ...state, [surfaceId]: { ...surface, data: setAtPointer(surface.data, path, value) } };
  }
  if (message.deleteSurface) {
    const { [message.deleteSurface.surfaceId]: _gone, ...rest } = state;
    return rest;
  }
  return state;
}

const reducer = (state, messages) => messages.reduce(applyMessage, state);

// const [surfaces, apply] = useSurfaces();   apply(listOfMessages)
export function useSurfaces() {
  const [surfaces, dispatch] = useReducer(reducer, {});
  const apply = useCallback((messages) => messages?.length && dispatch(messages), []);
  return [surfaces, apply];
}