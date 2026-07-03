// Placeholder for local builds before the backend is running.
// `npm run gen:api` replaces this with types from /openapi.json.
export interface paths {
  [path: string]: Record<string, unknown>;
}
