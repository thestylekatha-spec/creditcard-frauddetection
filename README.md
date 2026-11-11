# Credit Card Fraud Detection — Next.js + Flask

Web application for credit card fraud detection.

```
fraud-detection/
├── backend/    Flask API + ML training (scikit-learn) + SQLite
└── frontend/   Next.js 14 (App Router) UI: Detect Fraud, Dashboard, Login, Register
```

## Run it

**1. Backend (terminal 1)**
```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
python train_model.py             # optional: app.py trains automatically if model.pkl is missing
FLASK_DEBUG=1 python app.py       # http://localhost:5000  (debug is OFF by default - keep it that way in production)
```

**2. Frontend (terminal 2)**
```bash
cd frontend
npm install
npm run dev                       # http://localhost:3000
```
The frontend reads the API address from `frontend/.env.local` (`NEXT_PUBLIC_API_URL`, default `http://localhost:5000`). Copy `frontend/.env.local.example` to get started.

**3. Tests**
```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q               # API + feature-engineering tests (uses a temporary DB)
```

## Pages
| Route | What it does |
|---|---|
| `/` | Redirects to the dashboard |
| `/detect` | Login-protected transaction form -> Flask `/api/predict` -> Fraud/Legitimate + probability + risk indicators |
| `/dashboard` | Login-protected totals, fraud rate, charts, recent transactions, model comparison, feature importance |
| `/login` | Log in with email and password |
| `/register` | Create an account with name, email, and password |

## API
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/predict` | Validate, preprocess, predict and store a transaction |
| GET | `/api/stats` | Totals, per-category counts, 14-day timeline, risk distribution |
| GET | `/api/transactions?limit=25&label=Fraudulent` | Stored predictions |
| DELETE | `/api/transactions` | Clear history (requires the `x-api-key` header if `ADMIN_API_KEY` is set) |
| GET | `/api/model-info` | Metrics of all trained models |
| GET | `/api/options` | Allowed categories and channels |
| GET | `/api/health` | Health check |
| POST | `/api/auth/register` | Create an account and sign in |
| POST | `/api/auth/login` | Sign in with email and password |
| GET | `/api/auth/me` | Check the current login session |
| POST | `/api/auth/logout` | Sign out |

`POST /api/predict` body:
```json
{ "amount": 48500, "hour": 2, "merchant_category": "electronics", "channel": "online",
  "distance_from_home_km": 640, "international": true, "txn_count_24h": 7, "avg_txn_amount": 1800 }
```
Optional fields (`distance_from_home_km`, `txn_count_24h`, `avg_txn_amount`) may be omitted; they are imputed. The response also includes the `threshold` used to turn the probability into a label.

Invalid input (missing/NaN/inf values, hour outside 0-23 or non-integer, unknown category/channel) returns `400` with a per-field `fields` map.

## Configuration (backend env vars)

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `5000` | Listen port (Render sets this for you) |
| `FLASK_DEBUG` | `0` | Set to `1` locally only - never in production |
| `CORS_ORIGINS` | `http://localhost:3000` | Comma separated allowed origins (frontend URL) |
| `DB_PATH` | `backend/fraud.db` | SQLite file (point at a disk to keep history) |
| `MODEL_PATH` / `METRICS_PATH` | `backend/model.pkl` / `metrics.json` | Model artifacts |
| `FRAUD_THRESHOLD` | threshold from training | Override the decision threshold (0-1) |
| `ADMIN_API_KEY` | unset | If set, `DELETE /api/transactions` needs header `x-api-key` |
| `SESSION_SECRET` | random per process | Flask session signing key; Render blueprint generates a persistent value |
| `SESSION_COOKIE_SAMESITE` | `Lax` | Session cookie SameSite policy; browser talks to the same-origin Next.js API proxy |
| `SESSION_COOKIE_SECURE` | `0` | Set to `1` when serving the API over HTTPS; Render blueprint sets this |

Frontend: `NEXT_PUBLIC_API_URL` (backend URL, inlined at build time), optional `NEXT_PUBLIC_ADMIN_KEY` (mirror of `ADMIN_API_KEY` so the dashboard's "Clear data" button works). API calls use a same-origin Next.js proxy so the browser reliably retains the login session when the UI and API are deployed on different hosts.

**CORS_ORIGIN entries** are comma separated: a plain URL matches exactly (`https://your-app.vercel.app`), and an entry containing `\`, `*`, `[]`, `()` or `?` is treated as a regular expression - anchor it with `$` so suffix tricks like `your-app.vercel.app.evil.com` cannot match. `https://.*\.vercel\.app$` allows every Vercel deployment of your project, including preview URLs.

`/api/health` reports `model_loaded` and the active `threshold`; if the model cannot be loaded the API still starts and `/api/predict` answers `503` instead of crashing.

## Using real data
`train_model.py` generates a **synthetic** dataset by default so the project runs out of the box. Because it is synthetic, the reported scores are optimistic — don't quote them as real-world accuracy.
To train on your own data: `python train_model.py --csv your_data.csv` with columns
`amount, hour, merchant_category, channel, distance_from_home_km, international, txn_count_24h, avg_txn_amount, is_fraud`.
(The popular Kaggle credit-card dataset uses anonymised PCA columns V1–V28 instead, so it needs a small change to `features.py`.)

## Deploy: Vercel (UI) + Render (API)

The repo ships a **Render blueprint** (`render.yaml`) for the backend; the Next.js UI deploys on Vercel. Both are free-tier friendly.

**1. Backend on Render**
1. Push this repository to GitHub.
2. Render dashboard → **New → Blueprint** → select the repo. Render reads `render.yaml` and creates `fraud-api` (Python, `gunicorn app:app`, health check `/api/health`).
3. Copy the service URL, e.g. `https://fraud-api-xxxx.onrender.com`.

**2. Frontend on Vercel**
1. Vercel dashboard → **Add New → Project** → import the same repo.
2. In "Configure Project", set **Root Directory = `frontend`** (this is required - the repo root has no `package.json`). Framework preset is auto-detected as Next.js.
3. Add the environment variable (all environments, especially **Production**):
   ```
   NEXT_PUBLIC_API_URL = https://fraud-api-xxxx.onrender.com
   ```
4. Deploy. Your site is live at `https://your-app.vercel.app`.

**3. Connect them (CORS)**
Back in Render → `fraud-api` → **Environment** → set `CORS_ORIGINS`, then **Manual Deploy → Deploy latest commit**:
```
https://your-app.vercel.app,https://.*\.vercel\.app$
```
The second entry is a regex (the `$` matters) that also allows Vercel **preview deployment** URLs (`https://your-app-git-branch-....vercel.app`) so pull-request previews work too.

**Order matters:** the API URL must exist before the UI builds (it is inlined at build time), and the UI URL must exist before CORS is configured. So: Render first → Vercel second → CORS third.

Notes:
- `NEXT_PUBLIC_API_URL` is inlined at **build** time - change it in Vercel and redeploy the UI.
- The UI calls the API over **https**, so `NEXT_PUBLIC_API_URL` must be `https://...`.
- The Render blueprint generates a persistent `SESSION_SECRET` and sets secure session cookies. Sync the updated blueprint in Render when applying these settings to an existing service.
- Render's free instances **sleep after ~15 min idle**; the first request takes 30-60s to wake the API (`/api/health` keeps waking it on the dashboard's 10s poll while a page is open).
- `model.pkl` and `metrics.json` are committed, so no training runs during deploy (if the file is ever missing, the API trains a model at boot - first start just takes longer).
- **Data persistence:** on the free plan `backend/fraud.db` lives inside the Render instance and is reset on redeploy. To keep history, use a paid instance + persistent disk mounted at `/data` and set `DB_PATH=/data/fraud.db` (see the commented block in `render.yaml`).
- **Optional hardening:** set `ADMIN_API_KEY` on Render (and the same value as `NEXT_PUBLIC_ADMIN_KEY` on Vercel) to lock down `DELETE /api/transactions`.

### CLI alternative
```bash
# backend -> Render (render.yaml is picked up automatically)
git push  # then "New -> Blueprint" in the dashboard, or:
#   render blueprint apply   (requires the Render CLI + API key)

# frontend -> Vercel
cd frontend
vercel                       # first run: link the project (root = frontend/)
vercel --prod                # add NEXT_PUBLIC_API_URL in vercel env first:
#   vercel env add NEXT_PUBLIC_API_URL production
```

### Other hosts
- Backend: `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT app:app`, set `CORS_ORIGINS` to your frontend URL (one worker keeps SQLite writes conflict-free). gunicorn runs on Linux/macOS (Render); on Windows use `python app.py`.
- Frontend: set `NEXT_PUBLIC_API_URL` to the backend URL, then `npm run build && npm start`.
