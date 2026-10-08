# Cryo web

React 19 + TypeScript UI. Node 22, matching CI.

```sh
cd web
npm ci
npm run dev
```

Vite serves http://localhost:5173 and proxies `/api`, `/healthz`, and `/metrics` to `VITE_PROXY_TARGET` (default `http://localhost:8000`). The API must be up. From the repo root, `make web-dev` is the same dev server and `make api` starts the API.

Regenerate API types after the API is up:

```sh
npm run gen:api
```

That fetches `http://localhost:8000/openapi.json`. `make web-gen` wraps it.

Checks: `npm run typecheck`, `npm test`, `npm run build`. Playwright: `npm run test:e2e`, `npm run test:visual`, `npm run axe`. There is no ESLint or Prettier script in this package.
