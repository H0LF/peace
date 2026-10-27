# PEACE dashboard

See the repository README for running and deploying. Quick reference:

```sh
npm ci
npm run dev        # Vite on :5173, proxies /api and /auth to PEACE_API (default http://127.0.0.1:8421)
npm run build      # dist/, served by the FastAPI app
npm run test:e2e   # Playwright against scripts/e2e_server.py (needs a local PostgreSQL, see README)
```
