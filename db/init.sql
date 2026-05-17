-- Schema for the Monzo personal-finance dashboard.
-- Loaded by the postgres container via /docker-entrypoint-initdb.d/.
-- Idempotent: every statement uses IF NOT EXISTS / CREATE OR REPLACE so this
-- file can be re-applied to an existing DB to pick up additions.

-- ---------------------------------------------------------------------------
-- Core transactions table (extended with account / split / group / offset cols)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS transactions (
    id              TEXT PRIMARY KEY,            -- Monzo tx id (or other-account id)
    occurred_at     TIMESTAMPTZ NOT NULL,
    amount          NUMERIC(12, 2) NOT NULL,     -- £; negative = spend
    merchant        TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    monzo_category  TEXT NOT NULL,               -- Monzo's original category
    category        TEXT NOT NULL,               -- effective category (after rules)
    amortise_days   INTEGER,                     -- NULL or 1 = no amortisation
    raw             JSONB
);

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS account_id       TEXT NOT NULL DEFAULT 'monzo';
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS my_share         NUMERIC(6, 4) NOT NULL DEFAULT 1.0;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS group_id         TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS offset_for_tx    TEXT REFERENCES transactions(id);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS offset_for_group TEXT;

CREATE INDEX IF NOT EXISTS idx_transactions_time     ON transactions (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions (category);
CREATE INDEX IF NOT EXISTS idx_transactions_merchant ON transactions (merchant);
CREATE INDEX IF NOT EXISTS idx_transactions_group     ON transactions (group_id)         WHERE group_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transactions_offset_tx ON transactions (offset_for_tx)    WHERE offset_for_tx IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_transactions_offset_gr ON transactions (offset_for_group) WHERE offset_for_group IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Accounts and snapshots (for net worth across non-Monzo balances)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS accounts (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    kind      TEXT NOT NULL,           -- 'current' | 'investment' | 'savings' | 'synthetic'
    currency  TEXT NOT NULL DEFAULT 'GBP'
);

INSERT INTO accounts (id, name, kind) VALUES
    ('monzo',            'Monzo',            'current'),
    ('santander',        'Santander',        'current'),
    ('revolut',          'Revolut',          'current'),
    ('vanguard_isa',     'Vanguard ISA',     'investment'),
    ('revolut_savings',  'Revolut Savings',  'savings'),
    ('monzo_savings',    'Monzo Savings Pot','savings'),
    ('ledger',           'Ledger (splits)',  'synthetic')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS account_balances (
    account_id             TEXT NOT NULL REFERENCES accounts(id),
    observed_at            DATE NOT NULL,
    balance                NUMERIC(12, 2) NOT NULL,
    contributions_to_date  NUMERIC(12, 2),   -- ISA-only: total deposited; balance - this = growth
    PRIMARY KEY (account_id, observed_at)
);

-- ---------------------------------------------------------------------------
-- Groups (holidays, leases, projects) — each can opt into amortisation
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,         -- 'iceland_2025', 'rent_2025_lease'
    kind        TEXT NOT NULL,            -- 'holiday' | 'rent' | 'project'
    name        TEXT NOT NULL,
    starts_at   DATE,
    ends_at     DATE,
    budget      NUMERIC(12, 2),           -- optional gross budget
    amortise    BOOLEAN NOT NULL DEFAULT FALSE
);

-- ---------------------------------------------------------------------------
-- Smoothed view (legacy: per-transaction expansion of `amount`, no offsets/splits)
-- Kept so any older panels still resolve. New panels point at personal_spend_daily.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW transactions_smoothed AS
SELECT
    t.id          AS transaction_id,
    t.merchant,
    t.category,
    t.description,
    t.occurred_at + (g.offset_days || ' days')::interval AS occurred_at,
    CASE
        WHEN t.amortise_days IS NULL OR t.amortise_days <= 1 THEN t.amount
        ELSE t.amount / t.amortise_days
    END           AS amount
FROM transactions t
CROSS JOIN LATERAL
    generate_series(0, GREATEST(COALESCE(t.amortise_days, 1) - 1, 0)) AS g(offset_days);

-- ---------------------------------------------------------------------------
-- Personal views: apply my_share + per-tx offsets + filter out internal/savings
-- and offset rows themselves. This is the "true cost per transaction" layer.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW transactions_personal AS
WITH offsets_per_tx AS (
    SELECT offset_for_tx AS target_id, SUM(amount) AS offset_total
    FROM transactions
    WHERE offset_for_tx IS NOT NULL
    GROUP BY offset_for_tx
)
-- Regular rows: spend + income, post-split, per-tx offsets folded in.
-- New column `is_reimbursement` is appended last so this view can be
-- re-CREATE-OR-REPLACED over an older version (Postgres only allows
-- appending columns, not inserting).
SELECT
    t.id, t.occurred_at, t.merchant, t.description, t.category,
    t.account_id, t.amortise_days, t.group_id, t.my_share,
    (t.amount * t.my_share) + COALESCE(o.offset_total, 0) AS net_amount,
    FALSE AS is_reimbursement
FROM transactions t
LEFT JOIN offsets_per_tx o ON o.target_id = t.id
WHERE t.offset_for_tx     IS NULL
  AND t.offset_for_group  IS NULL
  AND t.category NOT IN ('internal', 'savings', 'investment', 'split')

UNION ALL

-- Group-level reimbursements: surfaced under the TARGET group so group
-- filters see them. Amount is positive (money coming back); downstream
-- smoothed view drops these when the target group is amortised, since
-- group_personal_smoothed already nets them into the lease line.
SELECT
    t.id, t.occurred_at, t.merchant, t.description, t.category,
    t.account_id, t.amortise_days,
    t.offset_for_group AS group_id,
    t.my_share,
    t.amount * t.my_share AS net_amount,
    TRUE AS is_reimbursement
FROM transactions t
WHERE t.offset_for_group IS NOT NULL
  AND t.offset_for_tx    IS NULL
  AND t.category NOT IN ('internal', 'savings', 'investment', 'split');

-- ---------------------------------------------------------------------------
-- Group cost rollup: gross_cost (post-split) − reimbursed = net_cost.
-- gross_cost is a positive number. reimbursed is a positive number. net_cost
-- is positive too unless reimbursements exceeded spend.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW group_costs AS
WITH gross AS (
    SELECT group_id, SUM(-amount * my_share) AS gross_cost
    FROM transactions
    WHERE group_id IS NOT NULL AND amount < 0
    GROUP BY group_id
),
reimb AS (
    SELECT offset_for_group AS group_id, SUM(amount) AS reimbursed
    FROM transactions
    WHERE offset_for_group IS NOT NULL
    GROUP BY offset_for_group
)
SELECT
    g.id, g.name, g.kind, g.starts_at, g.ends_at, g.budget, g.amortise,
    COALESCE(gross.gross_cost, 0)                                    AS gross_cost,
    COALESCE(reimb.reimbursed, 0)                                    AS reimbursed,
    COALESCE(gross.gross_cost, 0) - COALESCE(reimb.reimbursed, 0)    AS net_cost
FROM groups g
LEFT JOIN gross ON gross.group_id = g.id
LEFT JOIN reimb ON reimb.group_id = g.id;

-- ---------------------------------------------------------------------------
-- Per-transaction smoothing — only for rows NOT in an amortised group, since
-- those get summed-then-spread at the group level instead.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW transactions_personal_smoothed AS
SELECT
    p.id AS transaction_id, p.merchant, p.category, p.description,
    p.account_id, p.group_id,
    p.occurred_at + (g.offset_days || ' days')::interval AS occurred_at,
    CASE WHEN p.amortise_days IS NULL OR p.amortise_days <= 1
         THEN p.net_amount
         ELSE p.net_amount / p.amortise_days
    END AS amount,
    p.is_reimbursement
FROM transactions_personal p
LEFT JOIN groups gr ON gr.id = p.group_id
CROSS JOIN LATERAL
    generate_series(0, GREATEST(COALESCE(p.amortise_days, 1) - 1, 0)) AS g(offset_days)
WHERE gr.id IS NULL
   OR gr.amortise IS NOT TRUE
   OR gr.starts_at IS NULL
   OR gr.ends_at   IS NULL;

-- ---------------------------------------------------------------------------
-- Group-level smoothing — one row per day in [starts_at, ends_at] for each
-- amortised group, carrying its evenly-divided net_cost as a (negative) spend.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW group_personal_smoothed AS
SELECT
    gr.id    AS group_id,
    gr.name  AS merchant,
    gr.kind  AS category,
    gr.name  AS description,
    NULL::TEXT AS account_id,
    (gr.starts_at + (d.offset_days || ' days')::interval)::timestamptz AS occurred_at,
    -gc.net_cost / NULLIF((gr.ends_at - gr.starts_at + 1), 0) AS amount
FROM groups gr
JOIN group_costs gc ON gc.id = gr.id
CROSS JOIN LATERAL
    generate_series(0, (gr.ends_at - gr.starts_at)::int) AS d(offset_days)
WHERE gr.amortise = TRUE
  AND gr.starts_at IS NOT NULL
  AND gr.ends_at   IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Single source of truth for "true daily personal spend"
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW personal_spend_daily AS
SELECT transaction_id AS source_id, 'transaction'::TEXT AS source_kind,
       merchant, category, description, account_id, group_id,
       occurred_at, amount, is_reimbursement
FROM transactions_personal_smoothed
UNION ALL
SELECT group_id AS source_id, 'group'::TEXT AS source_kind,
       merchant, category, description, account_id, group_id,
       occurred_at, amount, FALSE AS is_reimbursement
FROM group_personal_smoothed;

-- ---------------------------------------------------------------------------
-- Monzo savings pot: running balance from category='savings' transactions,
-- with simple daily interest accrual at 2.75% APR, floored at 0.
-- Pot deposits arrive as negative Monzo amounts; we negate to get positive delta.
-- The floor handles the "interest overshoot" when a full withdrawal (including
-- Monzo-paid interest) makes the cumulative sum slightly negative.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW monzo_savings_daily AS
WITH days AS (
    SELECT generate_series(
        COALESCE((SELECT MIN(occurred_at)::date FROM transactions
                  WHERE account_id = 'monzo' AND category = 'savings'), CURRENT_DATE),
        CURRENT_DATE, '1 day'::interval
    )::date AS day
),
pot_delta AS (
    SELECT occurred_at::date AS day, SUM(-amount) AS delta
    FROM transactions
    WHERE account_id = 'monzo' AND category = 'savings'
    GROUP BY 1
),
raw_principal AS (
    SELECT d.day,
           COALESCE(SUM(p.delta) OVER (ORDER BY d.day), 0) AS principal
    FROM days d LEFT JOIN pot_delta p USING (day)
),
with_interest AS (
    SELECT day, principal,
           COALESCE(
               SUM(GREATEST(principal, 0) * 0.0275 / 365.0)
               OVER (ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
               0
           ) AS accrued_interest
    FROM raw_principal
)
SELECT day,
       GREATEST(principal + accrued_interest, 0) AS balance
FROM with_interest;

-- ---------------------------------------------------------------------------
-- Revolut GBP Boosted savings pot: running balance from category='savings'
-- transfers in the Revolut current account, with 4.5% AER simple daily interest.
-- "To GBP Boosted" rows have negative amount (money leaving current account),
-- so SUM(-amount) gives positive savings deltas — same pattern as monzo_savings_daily.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW revolut_savings_daily AS
WITH days AS (
    SELECT generate_series(
        COALESCE((SELECT MIN(occurred_at)::date FROM transactions
                  WHERE account_id = 'revolut' AND category = 'savings'), CURRENT_DATE),
        CURRENT_DATE, '1 day'::interval
    )::date AS day
),
pot_delta AS (
    SELECT occurred_at::date AS day, SUM(-amount) AS delta
    FROM transactions
    WHERE account_id = 'revolut' AND category = 'savings'
    GROUP BY 1
),
raw_principal AS (
    SELECT d.day,
           COALESCE(SUM(p.delta) OVER (ORDER BY d.day), 0) AS principal
    FROM days d LEFT JOIN pot_delta p USING (day)
),
with_interest AS (
    SELECT day, principal,
           COALESCE(
               SUM(GREATEST(principal, 0) * 0.045 / 365.0)
               OVER (ORDER BY day ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
               0
           ) AS accrued_interest
    FROM raw_principal
)
SELECT day,
       GREATEST(principal + accrued_interest, 0) AS balance
FROM with_interest;

-- ---------------------------------------------------------------------------
-- Daily net worth: Monzo running balance + last-known snapshot per other account
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW net_worth_daily AS
WITH days AS (
    SELECT generate_series(
        COALESCE((SELECT MIN(occurred_at)::date FROM transactions), CURRENT_DATE),
        CURRENT_DATE,
        '1 day'::interval
    )::date AS day
),
monzo_daily_delta AS (
    -- Real Monzo balance = cumulative sum of every Monzo transaction, with
    -- no filtering. Internal transfers, offsets, etc. all move real money in
    -- and out of the account, so they must be counted. Net-worth dips between
    -- a Monzo→Santander transfer and the next Santander snapshot are real
    -- (the snapshot is just stale) — they self-correct.
    SELECT occurred_at::date AS day, SUM(amount) AS delta
    FROM transactions
    WHERE account_id = 'monzo'
    GROUP BY 1
),
monzo_balance AS (
    SELECT d.day,
           COALESCE(SUM(m.delta) OVER (ORDER BY d.day), 0) AS balance
    FROM days d
    LEFT JOIN monzo_daily_delta m USING (day)
),
external_balance AS (
    SELECT d.day, a.id AS account_id,
           (SELECT b.balance
              FROM account_balances b
             WHERE b.account_id = a.id AND b.observed_at <= d.day
          ORDER BY b.observed_at DESC LIMIT 1) AS balance
    FROM days d
    CROSS JOIN accounts a
    WHERE a.id NOT IN ('monzo', 'ledger', 'monzo_savings', 'revolut_savings')
)
SELECT day, 'monzo' AS account_id, balance FROM monzo_balance
UNION ALL
SELECT day, account_id, COALESCE(balance, 0) FROM external_balance
UNION ALL
SELECT day, 'monzo_savings' AS account_id, balance FROM monzo_savings_daily
UNION ALL
SELECT day, 'revolut_savings' AS account_id, balance FROM revolut_savings_daily;
