# RelayOS frontend
React + TypeScript + Vite. API base: `VITE_API_URL` (default http://localhost:8000).

    npm ci
    npm run dev            # http://localhost:5173
    npm run build          # tsc -b && vite build

## Browser tests (Playwright)
    npm run test:e2e:install   # downloads Chromium
    npm run test:e2e           # 21 tests (last run: 21 passed)
If the download is blocked, use a system Chromium:
`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/chrome npm run test:e2e`

The config starts (1) `tests/e2e_support/fake_server.py` - the real FastAPI app with a FAKE orchestrator on :8000 and (2) a production build via `vite preview` on :4173. `console.spec.ts` uses that fake backend; `resilience.spec.ts` mocks API responses with `page.route`. Neither exercises the real AI pipeline.
