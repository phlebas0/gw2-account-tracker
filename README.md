# gw2-account-tracker

A personal account tracking and analytics tool for Guild Wars 2, written in Python.

The project collects account information through the GW2 API and stores historical observations, allowing changes in account wealth, progression and Trading Post activity to be analysed over time.

## Features

- Account net worth, including liquid gold, inventories and Trading Post orders
- Historical wealth, wallet balances and account progression
- Character statistics and estimated inventory values
- Trading Post transactions and realised profit/loss using FIFO accounting
- Filtering by date, item and transaction type, with daily, weekly and monthly aggregation
- Interactive charts and CSV exports
- Automated daily data collection

## How it works

The project has three main components:

1. **Python collector** — Retrieves account data from the official GW2 API and processes it into historical records.
2. **Supabase** — Hosts a PostgreSQL database containing account snapshots, transactions and other collected data.
3. **Streamlit** — Provides a browser-based interface for querying, filtering and visualising the stored data.

GitHub Actions runs the collector automatically each day. The data is written to Supabase, independently of whether the local application is running.

The Streamlit interface runs locally in a browser and connects to the same database. No public web hosting is required.

### Tech stack

| Technology | Purpose |
|---|---|
| Python | Collection and processing |
| GW2 API | Account and Trading Post information |
| Supabase / PostgreSQL | Database hosting and historical storage |
| SQLAlchemy / Psycopg | Database connectivity |
| Pandas | Data processing and aggregation |
| Streamlit | Browser interface |
| Altair | Charts and visualisation |
| GitHub Actions | Scheduled collection |

## Setup

### 1. Supabase

Supabase provides the PostgreSQL database. This stores the historical data so that collection can continue without relying on a local computer.

1. Create a project at [supabase.com](https://supabase.com).
2. Open the project and select **Connect**.
3. Select **Session Pooler** to obtain an IPv4-compatible connection.
4. Copy the PostgreSQL connection URI and insert the database password.

The URI has the following general structure:

`postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:5432/postgres`

Use the actual connection details supplied by Supabase.

The database schema is defined in `tracker_core.py` and is created automatically when the collector first runs successfully. No manual table creation is needed.

### 2. GW2 API

1. Open [ArenaNet account management](https://account.arena.net/applications).
2. Generate an API key with the account permissions required for collecting characters, progression, inventories, wallet balances and trading activity.
3. Keep the key for the GitHub Actions configuration.

The collector uses this key to authenticate requests to the GW2 API.

### 3. GitHub Actions

GitHub Actions runs the collector remotely, meaning historical data can accumulate even when the computer is turned off.

The workflow is defined in `.github/workflows/collect.yml`.

To configure it:

1. Open the GitHub repository.
2. Go to **Settings → Secrets and variables → Actions**.
3. Create two repository secrets:

| Secret | Value |
|---|---|
| `GW2_API_KEY` | Personal GW2 API key |
| `DATABASE_URL` | Supabase PostgreSQL connection URI |

The workflow is configured to run daily at 03:17 Europe/London time. It can also be started manually under **Actions → Collect GW2 data → Run workflow**.

Each run installs the required dependencies, executes `collector.py` and writes the collected observations to PostgreSQL.

Execution history and errors can be inspected through the GitHub Actions logs.

### 4. Local interface

Clone the repository:

`git clone https://github.com/phlebas0/gw2-account-tracker.git`

`cd gw2-account-tracker`

Install dependencies:

`python -m pip install -r requirements.txt`

Create a `.env` file in the project directory containing:

`DATABASE_URL=your_supabase_connection_uri`

This should point to the same PostgreSQL database used by GitHub Actions.

Start Streamlit:

`python -m streamlit run streamlit_app.py --server.address 127.0.0.1`

The interface should open at `http://127.0.0.1:8501`, or another available port.

The interface runs locally, but requires an internet connection to read the Supabase database and retrieve current item information.

## Project structure

| File | Purpose |
|---|---|
| `collector.py` | Entry point for data collection |
| `tracker_core.py` | API access, database schema and processing |
| `streamlit_app.py` | Browser interface |
| `requirements.txt` | Python dependencies |
| `.github/workflows/collect.yml` | Scheduled collection workflow |

## Notes

- Account wealth is an estimate based on available item data and market prices. It does not necessarily represent immediately realisable gold.
- Trading Post profit is calculated using FIFO matching and includes Trading Post fees. Unmatched sales are excluded from realised profit.
- Historical coverage depends on the frequency of successful collection and the information available from the API.
- Database credentials and API keys should be kept private. Local `.env` files should not be committed to Git.

## Disclaimer

This is an independent personal project and is not affiliated with or endorsed by ArenaNet or NCSOFT. Guild Wars 2 and associated trademarks belong to their respective owners.