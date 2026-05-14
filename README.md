# monzo-grafana

Personal finance dashboard: Monzo → PostgreSQL → Grafana.

## Prerequisites

- Docker + Docker Compose
- Optional for local development without containers: [Nix](https://nixos.org/) with flakes + [direnv](https://direnv.net/) (`direnv allow` activates a uv-managed Python env via `flake.nix`)

## Project layout

```
.
├── monzo_grafana/            # Application package; installed as a wheel
│   ├── config.py             # Typed env-driven Config
│   ├── cli.py                # argparse dispatch → `monzo-poller` console script
│   ├── scheduler.py          # Trigger server + periodic poll loop
│   ├── grafana_api.py        # Grafana annotation lookup
│   ├── monzo/                # OAuth flow + transactions API client
│   ├── rules/                # YAML loader + rule-matching engine
│   ├── db/                   # Postgres writers (transactions, groups, splits, snapshots)
│   └── editor/               # HTTP rule editor (handlers, store, Jinja2 templates)
├── data/                     # Runtime state (gitignored except .example)
│   ├── categories.yaml.example
│   ├── categories.yaml       # Your personal rules (created from .example)
│   └── tokens.json           # Monzo OAuth tokens (created by `monzo-poller auth`)
├── db/init.sql               # Schema + views; runs once on first Postgres boot
├── grafana/dashboard.json    # Importable Grafana dashboard
├── tests/                    # Pure-logic unit tests
├── docker-compose.yml        # 4 services: postgres, grafana, poller, rule-editor
├── Dockerfile                # Shared image for poller + rule-editor
├── flake.nix                 # Optional Nix dev shell
└── pyproject.toml            # Defines `monzo-poller` and `monzo-rule-editor` scripts
```

## First-time setup

### 1. Environment variables

```sh
cp .env.example .env
```

Edit `.env` to fill in:

- **Monzo OAuth** — `MONZO_CLIENT_ID` and `MONZO_CLIENT_SECRET` from your app at developers.monzo.com
- **`POSTGRES_PASSWORD`** — any strong string (used by Postgres, the poller, and the rule-editor)
- **`GF_SECURITY_ADMIN_PASSWORD`** — Grafana admin password

### 2. Seed the runtime data files

The poller container bind-mounts `./data` from the host. Touch the OAuth token file so the bind-mount has something to grab, and copy the rules template:

```sh
touch data/tokens.json
cp data/categories.yaml.example data/categories.yaml
```

### 3. Start everything

```sh
docker compose up -d
```

Four services come up:

| Service | URL | Purpose |
|---|---|---|
| `postgres` | `localhost:5432` | Transactions store |
| `grafana` | http://localhost:3000 | Dashboard |
| `poller` | (internal trigger on 7925) | Pulls Monzo on a schedule |
| `rule-editor` | http://localhost:7924 | Rule editor for `categories.yaml` |

Postgres runs `db/init.sql` on first boot to create the `transactions` table, the `accounts` / `groups` / `account_balances` tables, six derived views (including `personal_spend_daily` and `net_worth_daily`), and indexes. The poller will start logging "No tokens found" until you complete OAuth in step 4.

### 4. Monzo OAuth

Create an OAuth client at [developers.monzo.com](https://developers.monzo.com):

- Redirect URL: `http://localhost:7923/callback`

Copy the **Client ID** (`oauth2client_...`) and **Client Secret** into `.env`. Then run the one-time auth flow inside the poller container:

```sh
docker compose exec poller uv run monzo-poller auth
```

A URL will be printed. Open it in your browser — Monzo emails you a magic link, click it, then approve in the Monzo app. Tokens are saved to the host's `data/tokens.json` (bind-mounted).

### 5. Initial poll

The scheduled poll fires every 30 minutes. Trigger one immediately, and within the 5-minute SCA window pull full history:

```sh
docker compose exec -e LOOKBACK_DAYS=3650 poller uv run monzo-poller poll
```

Verify rows landed:

```sh
docker compose exec postgres psql -U monzo finance -c "SELECT COUNT(*) FROM transactions;"
```

### 6. Set up Grafana datasource

Open **http://localhost:3000** and log in (`admin` / whatever you set in `.env.grafana`).

**Connections → Data sources → Add → PostgreSQL**

| Field | Value |
|-------|-------|
| Host URL | `postgres:5432` |
| Database | `finance` |
| Username | `monzo` |
| Password | *(value of `POSTGRES_PASSWORD` from `.env.postgres`)* |
| TLS/SSL Mode | `disable` |

> Use `postgres:5432` (the compose service name), not `localhost`, so Grafana can reach Postgres inside the Docker network.

Click **Save & test**.

### 7. Import the dashboard

**Dashboards → New → Import → Upload JSON file** → select `grafana/dashboard.json` → pick the PostgreSQL datasource → **Import**.

## Editing rules from Grafana (recommended)

The `rule-editor` service runs alongside the poller in Docker. Visit **http://localhost:7924** to see all current rules and delete any of them. The **Groups** tab (top nav) lists holiday/lease/project groups and lets you add or remove them.

In every Grafana table panel the merchant column is a link — click it and the editor opens in a new tab with the transaction context pre-filled. The form lets you set:

- **Category** (autocompletes from existing DB and YAML categories)
- **Amortise** window (months / weeks / days) — spreads the cost across that window in the smoothed view
- **My share** (e.g. `1/3`) — for shared bills, only this fraction counts as your spend
- **Group** — tag this transaction into a holiday or lease (autocompletes from defined groups)
- **Offset for transaction** — this row is a refund of a specific older purchase (dropdown of recent outgoings)
- **Offset for group** — this row reduces the net cost of a whole group (e.g. a friend repaying you for a holiday)

**Save** → the editor automatically asks the poller to re-apply rules to existing data, so changes show up in the dashboard within seconds.

The listing page also has manual buttons:

- **Apply rules now** — re-runs `retag` against existing rows in Postgres
- **Fetch from Monzo** — runs a fresh poll

The editor and poller share `categories.yaml` via a bind-mount, so changes are visible from both sides instantly.

## Why retag is now safe

Rules are applied via SQL `UPDATE` against existing rows — no DELETE step. A second retag with no rule changes touches zero rows (the `WHERE ... IS DISTINCT FROM` clause skips no-ops). This replaces the old InfluxDB "purge by time range and rewrite" pattern that lost data when reads or writes timed out.

## Manual commands

```sh
# Run a single poll right now
docker compose exec poller uv run monzo-poller poll

# Re-apply categories.yaml rules to existing rows (no Monzo round-trip)
docker compose exec poller uv run monzo-poller retag

# Push the YAML `groups:` section into the groups table (also runs inside poll/retag)
docker compose exec poller uv run monzo-poller sync-groups

# Record an external account balance snapshot
docker compose exec poller uv run monzo-poller snapshot vanguard_isa 2026-05-10 18450 17000

# Re-do OAuth (Monzo's strong-customer-auth window is 5 minutes)
docker compose exec poller uv run monzo-poller auth

# Tail the poller log
docker compose logs -f poller

# Inspect data
docker compose exec postgres psql -U monzo finance -c "
  SELECT category, COUNT(*) FROM transactions GROUP BY 1 ORDER BY 2 DESC;
"

# Inspect group costs
docker compose exec postgres psql -U monzo finance -c "
  SELECT name, gross_cost, reimbursed, net_cost FROM group_costs;
"
```

## Override rules (`categories.yaml`)

`data/categories.yaml` is the single config file for all per-transaction tweaks — whether you edit it via the rule editor above or by hand. Each rule has one **matcher** and one or more **actions**. See `data/categories.yaml.example` for the full format.

After editing, the rule editor automatically calls `retag` so the change applies to every row in Postgres immediately. If you edited the file manually, run `docker compose exec poller uv run monzo-poller retag`.

### 1. Re-categorise (fix `general`)

The **Uncategorised transactions** panel shows everything Monzo dropped into `general`. Add rules to clean them up:

```yaml
overrides:
  - merchant: "Tesco"
    category: groceries
  - merchant_pattern: "(?i)spotify|netflix"
    category: subscriptions
```

### 2. Exclude internal money movement (`internal` category)

Self-transfers, reimbursements, and refunds shouldn't count as either income or spend. Tag them `internal` and the dashboard filters them out of every total (alongside `savings`, which is excluded by default):

```yaml
overrides:
  - merchant: "MR OLIVER DAVID COSGROVE"
    category: internal
  - transaction_id: "tx_0000B5x123..."
    category: internal
  - description_pattern: "(?i)bob refund"
    category: internal
```

The `transaction_id` matcher is useful for one-offs that you don't want to apply to every transaction from that merchant.

### 3. Amortise large purchases

Spread a big charge evenly across a window in the smoothed view. Pick whichever unit fits — `amortise_months`, `amortise_weeks`, or `amortise_days`:

```yaml
overrides:
  - merchant: "Apple"
    category: subscriptions
    amortise_months: 12     # spread an annual bill across the year
  - transaction_id: "tx_0000B4Zxyz..."
    amortise_weeks: 3       # spread a 3-week holiday booking
  - merchant: "Klarna"
    amortise_days: 14
```

Postgres expands the smoothed view lazily on each query — no row explosion in storage. You can still use Grafana annotations with text `split:6` if you prefer (requires `GRAFANA_TOKEN`).

### 4. Shared bills (`my_share`)

Bills paid by one person and split N ways. Your true cost is always your share, regardless of who paid:

```yaml
overrides:
  - merchant: "Octopus Energy"
    category: bills
    amortise_months: 1
    my_share: "1/3"     # also accepts numeric (0.5, 0.3333)
```

The `personal_spend_daily` view multiplies the amount by `my_share`, so every panel reading that view reflects your true cost. The `transactions` table keeps the gross figure intact for reference.

### 5. Groups (holidays, leases, projects)

A **group** bundles many transactions under one name so you can see a net cost per trip / lease / project, and optionally amortise the whole bundle. Define groups under a top-level `groups:` section and tag transactions with `group: <id>`:

```yaml
groups:
  iceland_2025:
    kind: holiday
    name: Iceland 2025
    starts_at: 2025-08-12
    ends_at: 2025-08-19
    budget: 800

  rent_2025_lease:
    kind: rent
    name: 2025 Lease
    starts_at: 2025-08-01
    ends_at: 2026-07-31
    amortise: true       # sum the payments, then spread evenly across the date range

overrides:
  - merchant: "Booking.com"
    category: holidays
    group: iceland_2025
  - merchant: "Rentals UK Ltd"
    group: rent_2025_lease
```

`amortise: true` on a group is the right answer for things like rent paid in lump sums: the **Group cost timeline** panel shows an even monthly line across the lease, regardless of when each payment actually landed. Member transactions' own `amortise_months` is ignored when the group is amortised, so there's no double counting.

The poller's `sync-groups` step writes the YAML `groups:` section into the `groups` table on every poll/retag. To upsert without a poll: `docker compose exec poller uv run monzo-poller sync-groups`.

### 6. Reimbursements and refunds (`offset_for_tx`, `offset_for_group`)

When money comes back to you — a friend repaying their share of a holiday, a refund from a merchant — tag it as an **offset**. The row drops out of income/spend totals and reduces the net cost of its target instead:

```yaml
overrides:
  # A friend repays £100 toward the Iceland trip
  - transaction_id: "tx_FRIEND_REPAID_ICELAND"
    offset_for_group: iceland_2025

  # The landlord refunded £600 on the rent lump sum
  - transaction_id: "tx_RENT_REFUND_2025"
    offset_for_group: rent_2025_lease

  # Single-transaction refund (older pattern)
  - transaction_id: "tx_REFUND"
    offset_for_tx: "tx_ORIGINAL_PURCHASE"
```

The **Reconciliation queue** panel lists every incoming transfer that's not yet categorised as income / internal / savings or linked to an offset — anything sitting there is unexplained inflow. Tagging it from the panel makes the row vanish. When the queue is empty, you're reconciled.

## Net worth across accounts

The dashboard shows running balances for every defined account on the **Net worth — by account** and **Net worth — total** panels. Monzo's balance is computed from the transactions table; other accounts (Vanguard ISA, Revolut savings, Santander) are tracked via periodic balance snapshots:

```sh
# Record a Vanguard ISA snapshot (balance + total contributions to date)
docker compose exec poller uv run monzo-poller snapshot vanguard_isa 2026-05-10 18450 17000

# Revolut savings snapshot — contributions optional
docker compose exec poller uv run monzo-poller snapshot revolut_savings 2026-05-10 1240
```

The four default accounts (`monzo`, `santander`, `vanguard_isa`, `revolut_savings`) are seeded by `db/init.sql`. The **ISA growth vs contributions** panel plots both balance and `contributions_to_date` so the gap shows market gain.

Snapshot cadence is up to you — monthly is fine; the `net_worth_daily` view forward-fills between observations, so the line stays smooth.

## Common commands

```sh
docker compose up -d       # start containers
docker compose down        # stop containers
docker compose logs -f     # tail logs
docker compose down -v     # nuke everything (including the database)
```

## Security & secrets

The following are gitignored and must never be committed:

| File | Why |
|---|---|
| `.env` | OAuth credentials and database / admin passwords |
| `data/tokens.json`, `data/tokens.json.*` | Live Monzo OAuth access and refresh tokens |
| `data/categories.yaml` | Your personal transaction ids, merchants, and group metadata |

Only `.env.example` and `data/categories.yaml.example` are tracked. If you accidentally commit a secret, rotate it: regenerate the Monzo client secret in the developer portal, change passwords in `.env`, or run `monzo-poller auth` again to mint fresh tokens.

## Troubleshooting

**`No tokens found — run: monzo-poller auth`** — the poller can't find `data/tokens.json`. On a fresh checkout you need to `touch data/tokens.json` (so the bind-mount has a file) and then run the auth flow. The OAuth callback only works within Monzo's 5-minute strong-customer-authentication window — if you miss it, re-run `auth`.

**Grafana datasource "connection refused"** — Grafana must reach Postgres over the Docker network, so the host must be the compose service name `postgres:5432`, not `localhost:5432`.

**Poller logs `relation "transactions" does not exist`** — Postgres only runs `db/init.sql` on first boot of an empty data volume. If you've changed the schema, `docker compose down -v` (which deletes the data volume) and re-up.

**Rule editor saves but the dashboard doesn't update** — `retag` runs as a background thread; check `docker compose logs poller` for the `Retag updated N row(s)` line. If `N` is `0` your rule didn't match anything.

**"Invalid rule" / "Skipping override without an action"** — a rule needs at least one matcher (`merchant`, `transaction_id`, `merchant_pattern`, or `description_pattern`) AND one action (`category`, `amortise_*`, `my_share`, `group`, `offset_for_*`). The editor enforces this; manual YAML edits don't.

## Development

Local Python work uses [uv](https://docs.astral.sh/uv/) (and optionally Nix + direnv to provide it):

```sh
uv sync                    # install runtime + dev deps
uv run pytest              # run the unit tests (pure logic, no DB needed)
uv run ruff check          # lint
uv run ruff format         # auto-format
uv run mypy                # type-check
uv run monzo-poller --help
```

`docker compose build` rebuilds the image after Python changes. The package lives under `monzo_grafana/`; `poller.py` and `rule_editor.py` are 3-line shims, so day-to-day edits happen inside the package.
