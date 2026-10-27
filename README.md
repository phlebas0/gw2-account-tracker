# gw2-account-tracker

A personal Guild Wars 2 account tracker that automatically collects and stores account data from the official Guild Wars 2 API and visualises it through a browser-based dashboard that can be run locally or hosted in the cloud.

## How it works

The project has three main components:

1. **Python collector:** Retrieves account data from the official GW2 API.
2. **Supabase:** Hosts a PostgreSQL database storing collected retrieved data.
3. **Streamlit:** Provides a browser-based interface for querying, filtering, and visualising the stored data.

GitHub Actions runs the collector automatically each day, writing the retrieved data to Supabase. Streamlit connects to the same database, either locally or through Streamlit Community Cloud.

### Tech stack

| Technology | Purpose |
|---|---|
| Python | Collection and processing |
| GW2 API | Account information |
| Supabase / PostgreSQL | Database hosting and historical storage |
| SQLAlchemy / Psycopg | Database connectivity |
| Pandas | Data processing and aggregation |
| Streamlit | Browser interface |
| Altair | Charts and visualisation |
| GitHub Actions | Scheduled collection |

## Setup

### 1. Supabase

[Supabase](https://supabase.com/) provides the PostgreSQL database used to store account data.

1. Create a Supabase project.
2. Open the project dashboard and select **Connect**.
3. Select **Session Pooler** for an IPv4-compatible connection.
4. Copy the PostgreSQL connection URI and insert the database password.

The database schema is defined in `tracker_core.py` and is initialised when the collector first runs successfully.

### 2. GW2 API

1. Open [ArenaNet account management](https://account.arena.net/applications).
2. Generate an API key with all available account permissions.
3. Retain the key for the collector configuration.

The collector uses this key to authenticate requests to the official GW2 API.

### 3. GitHub Actions

GitHub Actions runs the collector remotely.

The workflow is defined in `.github/workflows/collect.yml`.

To configure it:

1. Open the GitHub repository containing the project.
2. Navigate to **Settings → Secrets and variables → Actions**.
3. Create the following repository secrets:

| Secret | Value |
|---|---|
| `GW2_API_KEY` | Personal GW2 API key generated in Step 2 |
| `DATABASE_URL` | Supabase PostgreSQL connection URI obtained in Step 1 |

The workflow runs daily and can also be started manually under **Actions → Collect GW2 data → Run workflow**.

Each run installs the required dependencies, executes `collector.py`, and writes data to PostgreSQL. Execution history and errors are available in the GitHub Actions logs.

### 4. Running locally

The Streamlit dashboard can be run directly from the local project directory. It connects to the Supabase database configured above, whilst remaining accessible only on the local device.

Clone the repository:

`git clone https://github.com/phlebas0/gw2-account-tracker.git`

`cd gw2-account-tracker`

Install dependencies:

`python -m pip install -r requirements.txt`

Create a `.env` file in the project directory containing:

`DATABASE_URL=your_supabase_connection_uri`

Use the same Supabase connection URI configured for GitHub Actions in Step 3.

Start Streamlit:

`python -m streamlit run streamlit_app.py --server.address 127.0.0.1`

The dashboard will normally open at `http://127.0.0.1:8501`, or another available port.

### 5. Streamlit Community Cloud

Alternatively, the same dashboard can be hosted through [Streamlit Community Cloud](https://share.streamlit.io) (this is my personal preference). This provides a URL for accessing the application without running it locally.

The application is deployed directly from the GitHub repository configured in Step 3, rather than from the local clone.

1. Sign in to Streamlit Community Cloud using GitHub.
2. Create a new application and select the `gw2-account-tracker` repository.
3. Select the `main` branch and set `streamlit_app.py` as the entry point.
4. Deploy the application.
5. Open the application's **Settings → Secrets** and add the Supabase connection URI from Step 1, using TOML syntax:

`DATABASE_URL = 'postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres'`

6. Save the secret and reboot the application if necessary.

#### Note:

Applications deployed to Streamlit Community Cloud inherit their GitHub repository's visibility by default. Applications deployed from public repositories are public, whilst those deployed from private repositories are private.

If you want to restrict access without making the repository private, go to [Streamlit Community Cloud](https://share.streamlit.io) → **⋯** (next to your application) → **Settings → Sharing → Who can view this app**, and select **Only specific people can view this app**. Leave the list of additional viewers empty to restrict access to yourself.

Alternatively, you can make the GitHub repository itself private under **Settings → General → Danger Zone → Change repository visibility**.

### Credentials

Each environment requires its own database configuration:

| Environment | Configuration |
|---|---|
| GitHub Actions | Repository secret |
| Local Streamlit | `.env` file |
| Streamlit Community Cloud | Application secret (TOML) |

## Costs and hosting

The project currently runs on the free tiers of its services:

- **Supabase:** 500 MB of PostgreSQL database storage, subject to data transfer and other usage limits.
- **GitHub Actions:** Free scheduled collection using standard runners in a public repository.
- **Streamlit:** Free to run locally or through Streamlit Community Cloud.

These allowances should be sufficient for a single GW2 account with daily data collection. The only potential constraint is database storage as historical records accumulate. Should this become an issue, the data can be exported, archived or migrated to another provider. There may well be better free hosting options available, although I haven't explored these in any detail.

The project could alternatively run entirely locally, using PostgreSQL for storage and a scheduled Python script for collection. Other database systems could also be used, although this would require some (minor) changes to the code. This would remove the need for cloud hosting, but collection would depend on the computer being switched on, and storage and backups would need to be managed locally.

## Project structure

| File | Purpose |
|---|---|
| `collector.py` | Data collection |
| `tracker_core.py` | API access, database schema and processing |
| `streamlit_app.py` | Browser interface |
| `requirements.txt` | Python dependencies |
| `.github/workflows/collect.yml` | Scheduled collection |
