-- Safe to run on every start. The database enforces the money rules itself,
-- so a bug in the API code cannot break them.
-- Money is stored as INTEGER minor units (paise / cents). SQLite has no exact
-- decimal type, and floats would round.

CREATE TABLE IF NOT EXISTS accounts (
    id          TEXT PRIMARY KEY CHECK (length(id) = 36),
    name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
    balance     INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id             TEXT PRIMARY KEY CHECK (length(id) = 36),
    account_id     TEXT NOT NULL REFERENCES accounts (id) ON DELETE RESTRICT,
    type           TEXT NOT NULL CHECK (type IN ('credit', 'debit')),
    amount         INTEGER NOT NULL CHECK (amount > 0),
    description    TEXT NOT NULL DEFAULT '',
    reference_id   TEXT NOT NULL CHECK (length(trim(reference_id)) > 0),
    balance_after  INTEGER NOT NULL CHECK (balance_after >= 0),
    transfer_id    TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (account_id, reference_id)
);

CREATE INDEX IF NOT EXISTS transactions_history_idx
    ON transactions (account_id, created_at DESC);

-- Transactions are append only. Any UPDATE or DELETE is refused by the database.
CREATE TRIGGER IF NOT EXISTS transactions_no_update
    BEFORE UPDATE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'transactions are immutable');
END;

CREATE TRIGGER IF NOT EXISTS transactions_no_delete
    BEFORE DELETE ON transactions
BEGIN
    SELECT RAISE(ABORT, 'transactions are immutable');
END;
