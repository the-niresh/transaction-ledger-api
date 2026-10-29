# Transaction Ledger API

A backend API for a simple ledger: create accounts, add credits and debits, read balances and
transaction history, and transfer money between accounts.

Built with **Python 3.12+, FastAPI and SQLite**. SQLite needs no server, so the project runs
locally with one command and nothing else to install.

**Live demo:** https://txn-ledger-api.vercel.app opens a small web page for creating
accounts, adding credits and debits, transferring, and browsing history with the filter and
pagination. Every endpoint can also be tried at `/docs`. The demo runs on Vercel, where SQLite can only write to
`/tmp`, which is wiped when an instance restarts. So data on the demo can reset at any time, and
two requests may land on different instances. Run it locally to see data that persists.

## Run it

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Or with pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The web page runs on http://127.0.0.1:8000 and the API on the same address. The database file `ledger.db` is created on first start.
Set `LEDGER_DB=/path/to/file.db` to use another file.

**Interactive API docs:** http://127.0.0.1:8000/docs (Swagger) or http://127.0.0.1:8000/redoc.
The same spec is saved in [`docs/openapi.json`](docs/openapi.json).

## Run the tests

```bash
uv run pytest        # or: pytest
```

39 tests cover every rule in the brief, including concurrent debits and duplicate requests, and
direct SQL attempts to edit or delete a transaction.

## Project structure

```
app/
  main.py      routes, error handlers, request wiring
  ledger.py    the ledger rules: balances, duplicates, atomic writes, transfers
  schemas.py   request and response models, with validation
  schema.sql   tables, constraints and triggers
  db.py        SQLite connection and startup
  errors.py    the single error shape
  static/
    index.html the web page: one file, plain HTML and JavaScript, no build step
tests/
  test_ledger.py
docs/
  openapi.json
```

## API

All amounts are sent as JSON numbers with up to 2 decimal places, for example `150` or `19.99`.
All amounts in responses are strings with exactly 2 decimal places, for example `"150.00"`, so no
client ever reads money as a float.

| Method | Path | What it does | Success |
|---|---|---|---|
| POST | `/accounts` | Create an account | 201 |
| GET | `/accounts/{account_id}` | Get an account | 200 |
| GET | `/accounts/{account_id}/balance` | Get the current balance | 200 |
| POST | `/accounts/{account_id}/transactions` | Add a credit or debit | 201 |
| GET | `/accounts/{account_id}/transactions` | Transaction history | 200 |
| POST | `/transfers` | Transfer between two accounts (bonus) | 201 |

### Create an account

```bash
curl -X POST localhost:8000/accounts -H 'content-type: application/json' -d '{"name": "Asha"}'
```

```json
{"id": "587351e8-555c-4174-a478-e7a07e34fd0f", "name": "Asha", "balance": "0.00",
 "created_at": "2026-10-29T17:38:19.026Z"}
```

Only `name` is accepted. Sending `balance`, `id` or `created_at` returns a validation error.

### Add a transaction

```bash
curl -X POST localhost:8000/accounts/$ID/transactions -H 'content-type: application/json' \
  -d '{"type": "credit", "amount": 100, "reference_id": "inv-001", "description": "Salary"}'
```

```json
{"id": "709c1acd-...", "account_id": "587351e8-...", "type": "credit", "amount": "100.00",
 "description": "Salary", "reference_id": "inv-001", "balance_after": "100.00",
 "transfer_id": null, "created_at": "2026-10-29T17:38:19.035Z"}
```

| Field | Rule |
|---|---|
| `type` | required, `credit` or `debit` |
| `amount` | required, greater than 0, at most 2 decimal places |
| `reference_id` | required, 1 to 100 characters, unique within the account |
| `description` | optional, up to 500 characters |

`balance_after` is the account balance right after this transaction, which makes the history
easy to audit.

### Balance

```bash
curl localhost:8000/accounts/$ID/balance
```

```json
{"account_id": "587351e8-...", "balance": "100.00"}
```

### Transaction history

```bash
curl "localhost:8000/accounts/$ID/transactions?type=credit&page=1&limit=20"
```

```json
{"data": [ {"id": "...", "type": "credit", "amount": "100.00", "...": "..."} ],
 "pagination": {"page": 1, "limit": 20, "total_records": 1, "total_pages": 1}}
```

| Query | Rule |
|---|---|
| `type` | optional, `credit` or `debit`. Leave it out to get both |
| `page` | optional, whole number from 1, default 1 |
| `limit` | optional, whole number from 1 to 100, default 20 |

Results are latest first by `created_at`. A page past the end returns an empty `data` list with
the correct totals.

### Transfer (bonus)

```bash
curl -X POST localhost:8000/transfers -H 'content-type: application/json' \
  -d '{"from_account_id": "'$A'", "to_account_id": "'$B'", "amount": 30, "reference_id": "tr-001"}'
```

```json
{"transfer_id": "...", "reference_id": "tr-001", "amount": "30.00",
 "debit":  {"account_id": "A...", "type": "debit",  "balance_after": "70.00", "...": "..."},
 "credit": {"account_id": "B...", "type": "credit", "balance_after": "80.00", "...": "..."}}
```

A transfer writes one debit on the source and one credit on the destination, linked by the same
`transfer_id`, inside one database transaction.

### Errors

Every error has the same shape:

```json
{"error": {"code": "INSUFFICIENT_BALANCE",
           "message": "The account does not have enough balance for this debit.",
           "details": {"balance": "100.00", "amount": "150.00"}}}
```

| Status | Code | When |
|---|---|---|
| 404 | `ACCOUNT_NOT_FOUND` | The account id is a valid UUID but no account has it |
| 409 | `DUPLICATE_TRANSACTION` | The `reference_id` already exists for this account. `details.existing_transaction` holds the original |
| 422 | `INSUFFICIENT_BALANCE` | A debit or transfer is larger than the balance |
| 422 | `SAME_ACCOUNT_TRANSFER` | A transfer from an account to itself |
| 422 | `BALANCE_LIMIT_EXCEEDED` | A credit would push the balance over the maximum |
| 422 | `VALIDATION_ERROR` | Bad body, bad field, bad type, bad amount, bad pagination, or a malformed account id. `details` lists each field and what is wrong |
| 404 / 405 | `NOT_FOUND` / `METHOD_NOT_ALLOWED` | Unknown route, or for example `DELETE` on transactions |
| 500 | `INTERNAL_ERROR` | Anything unexpected. Nothing is saved |

## How data integrity is kept

The rules are enforced by the database, not only by the API code, so a bug in the code cannot
break them.

- **Atomic writes.** Each transaction or transfer runs inside one `BEGIN IMMEDIATE ... COMMIT`.
  The transaction row and the balance update are saved together or not at all. Any error rolls
  everything back.
- **No overspending under load.** `BEGIN IMMEDIATE` takes the write lock before the balance is
  read, so two debits can never both read the same balance and both spend it. A test fires 10
  debits of 30 at a balance of 100 at the same time: exactly 3 succeed.
- **Duplicates.** `UNIQUE (account_id, reference_id)` in the table. The API checks first so it can
  return the original transaction, and the constraint is the final guard. A test sends the same
  request 10 times at once: exactly 1 transaction is created.
- **Immutability.** There is no update or delete endpoint for transactions, and two triggers make
  SQLite refuse any `UPDATE` or `DELETE` on the table, even from raw SQL.
- **Balance never negative.** `CHECK (balance >= 0)` on accounts and on `balance_after`.
- **Valid values only.** `CHECK` on `type` and `amount > 0`, `NOT NULL` on required fields,
  foreign key from transactions to accounts.
- **Exact money.** Amounts are stored as whole minor units (paise or cents) in `INTEGER` columns
  and handled as `Decimal` in Python, never as floats. Ten credits of 0.10 add up to exactly 1.00.

## Assumptions

1. **One currency.** The brief has no currency field, so all accounts share one currency.
   Amounts have at most 2 decimal places.
2. **Ids are UUIDs created by the server.** The client cannot choose an account id. A malformed
   id returns 422, a well-formed id that does not exist returns 404.
3. **A duplicate returns 409 Conflict** with the original transaction in `details`, so a client
   retrying after a timeout can see what was saved. Nothing new is created and the balance does
   not change.
4. **A rejected transaction is not stored.** A debit refused for low balance does not use up its
   `reference_id`, so the client can retry the same reference later once the account is funded.
5. **`description` is optional** and defaults to an empty string. The brief lists it as a field
   but does not say it is required.
6. **Insufficient balance is 422,** since the request is well formed but breaks a business rule.
   The error `code` separates it from validation errors.
7. **A transfer uses one `reference_id` for both sides.** It must be unused on both accounts. If
   either side already has it, the whole transfer is refused with 409 and nothing changes.
8. **Maximum amount** is 9,999,999,999,999.99 and the maximum balance is 10^15, to keep every
   value an exact integer in SQLite.
9. **No authentication.** The brief does not ask for users or login, so any caller can use any
   account.
10. **`created_at` is set by the database** in UTC, with millisecond precision. When two
    transactions share a timestamp, the later insert is listed first.
11. **SQLite** was chosen so the project runs with no setup. It allows one writer at a time,
    which keeps the money logic simple and safe. For real traffic I would move to PostgreSQL and
    lock only the account rows involved (`SELECT ... FOR UPDATE`). The SQL and the rules stay
    the same.
