"""Ledger rules. Every write runs inside one database transaction.

`BEGIN IMMEDIATE` takes SQLite's write lock before reading the balance, so two
requests can never both read the same balance and both spend it. If anything
fails, the whole transaction rolls back: the transaction row and the balance
change are saved together or not at all.
"""

import math
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator

from .errors import ApiError, account_not_found
from .schemas import AccountCreate, TransactionCreate, TransferCreate

Row = dict[str, Any]

# SQLite stores whole numbers up to about 9.2e18. Stay far below that, so a
# balance can never overflow into a float.
MAX_BALANCE_MINOR = 10**17

TX_COLUMNS = (
    "id, account_id, type, amount, description, reference_id, balance_after, transfer_id, created_at"
)


def to_minor(amount: Decimal) -> int:
    return int(amount * 100)  # amounts are validated to 2 decimal places, so this is exact


def to_major(minor: int) -> Decimal:
    return (Decimal(minor) / 100).quantize(Decimal("0.01"))


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def create_account(conn: sqlite3.Connection, data: AccountCreate) -> Row:
    # id, balance and created_at are set by the system, never by the client.
    row = conn.execute(
        "INSERT INTO accounts (id, name) VALUES (?, ?) RETURNING id, name, balance, created_at",
        (str(uuid.uuid4()), data.name),
    ).fetchone()
    return _account(row)


def get_account(conn: sqlite3.Connection, account_id: str) -> Row:
    row = conn.execute(
        "SELECT id, name, balance, created_at FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if row is None:
        raise account_not_found(account_id)
    return _account(row)


def add_transaction(conn: sqlite3.Connection, account_id: str, data: TransactionCreate) -> Row:
    amount = to_minor(data.amount)
    with atomic(conn):
        balance = _balance(conn, account_id)
        _reject_duplicate(conn, account_id, data.reference_id)
        new_balance = _apply(balance, data.type, amount)
        return _write(conn, account_id, data.type, amount, data.description,
                      data.reference_id, new_balance, None)


def transfer(conn: sqlite3.Connection, data: TransferCreate) -> Row:
    source, dest = str(data.from_account_id), str(data.to_account_id)
    if source == dest:
        raise ApiError(422, "SAME_ACCOUNT_TRANSFER", "Source and destination accounts must be different.")
    amount = to_minor(data.amount)
    transfer_id = str(uuid.uuid4())
    with atomic(conn):
        source_balance = _balance(conn, source)
        dest_balance = _balance(conn, dest)
        _reject_duplicate(conn, source, data.reference_id)
        _reject_duplicate(conn, dest, data.reference_id)
        debit = _write(conn, source, "debit", amount, data.description, data.reference_id,
                       _apply(source_balance, "debit", amount), transfer_id)
        credit = _write(conn, dest, "credit", amount, data.description, data.reference_id,
                        _apply(dest_balance, "credit", amount), transfer_id)
    return {"transfer_id": transfer_id, "reference_id": data.reference_id,
            "amount": data.amount, "debit": debit, "credit": credit}


def list_transactions(conn: sqlite3.Connection, account_id: str, tx_type: str | None,
                      page: int, limit: int) -> Row:
    get_account(conn, account_id)
    where, params = "account_id = ?", [account_id]
    if tx_type:
        where += " AND type = ?"
        params.append(tx_type)
    total = conn.execute(f"SELECT count(*) FROM transactions WHERE {where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {TX_COLUMNS} FROM transactions WHERE {where} "
        "ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
        [*params, limit, (page - 1) * limit],
    ).fetchall()
    return {"data": [_transaction(r) for r in rows], "pagination": {
        "page": page, "limit": limit, "total_records": total,
        "total_pages": math.ceil(total / limit),
    }}


def _balance(conn: sqlite3.Connection, account_id: str) -> int:
    row = conn.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise account_not_found(account_id)
    return row["balance"]


def _reject_duplicate(conn: sqlite3.Connection, account_id: str, reference_id: str) -> None:
    # The write lock is held, so no other request can add this reference meanwhile.
    # The UNIQUE constraint is still the final guard (see _write).
    row = conn.execute(
        f"SELECT {TX_COLUMNS} FROM transactions WHERE account_id = ? AND reference_id = ?",
        (account_id, reference_id),
    ).fetchone()
    if row is not None:
        raise _duplicate(account_id, reference_id, _transaction(row))


def _apply(balance: int, tx_type: str, amount: int) -> int:
    if tx_type == "credit":
        new_balance = balance + amount
        if new_balance > MAX_BALANCE_MINOR:
            raise ApiError(422, "BALANCE_LIMIT_EXCEEDED", "This credit would take the balance over the maximum allowed.")
        return new_balance
    if balance < amount:
        raise ApiError(422, "INSUFFICIENT_BALANCE", "The account does not have enough balance for this debit.",
                       {"balance": str(to_major(balance)), "amount": str(to_major(amount))})
    return balance - amount


def _write(conn: sqlite3.Connection, account_id: str, tx_type: str, amount: int, description: str,
           reference_id: str, new_balance: int, transfer_id: str | None) -> Row:
    try:
        row = conn.execute(
            "INSERT INTO transactions (id, account_id, type, amount, description, reference_id, "
            f"balance_after, transfer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING {TX_COLUMNS}",
            (str(uuid.uuid4()), account_id, tx_type, amount, description, reference_id,
             new_balance, transfer_id),
        ).fetchone()
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc) and "reference_id" in str(exc):
            raise _duplicate(account_id, reference_id, None) from None
        raise
    conn.execute("UPDATE accounts SET balance = ? WHERE id = ?", (new_balance, account_id))
    return _transaction(row)


def _duplicate(account_id: str, reference_id: str, existing: Row | None) -> ApiError:
    return ApiError(409, "DUPLICATE_TRANSACTION",
                    f"A transaction with reference_id '{reference_id}' already exists for this account.",
                    {"account_id": account_id, "existing_transaction": existing})


def _account(row: sqlite3.Row) -> Row:
    return {**dict(row), "balance": to_major(row["balance"])}


def _transaction(row: sqlite3.Row) -> Row:
    return {**dict(row), "amount": to_major(row["amount"]), "balance_after": to_major(row["balance_after"])}
