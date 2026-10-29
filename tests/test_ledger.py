import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "test.db")
    monkeypatch.setenv("LEDGER_DB", path)
    return path


@pytest.fixture
def client(db_path):
    with TestClient(app) as c:
        yield c


def new_account(client, name="Asha"):
    res = client.post("/accounts", json={"name": name})
    assert res.status_code == 201
    return res.json()["id"]


def tx(client, account_id, type_, amount, ref, description=""):
    return client.post(f"/accounts/{account_id}/transactions",
                       json={"type": type_, "amount": amount, "reference_id": ref, "description": description})


def balance(client, account_id):
    return client.get(f"/accounts/{account_id}/balance").json()["balance"]


# 1. Accounts

def test_account_starts_at_zero_with_system_fields(client):
    res = client.post("/accounts", json={"name": "Asha"})
    body = res.json()
    assert res.status_code == 201
    assert body["balance"] == "0.00" and body["name"] == "Asha"
    assert len(body["id"]) == 36 and body["created_at"]


def test_client_cannot_set_balance_or_id(client):
    for extra in ({"balance": 500}, {"id": "x"}, {"created_at": "2020-01-01"}):
        res = client.post("/accounts", json={"name": "Asha", **extra})
        assert res.status_code == 422
        assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}, {"name": None}])
def test_name_is_required(client, payload):
    assert client.post("/accounts", json=payload).status_code == 422


def test_account_ids_are_unique(client):
    assert len({new_account(client) for _ in range(20)}) == 20


# 2. Transactions

def test_credit_and_debit_change_balance(client):
    acc = new_account(client)
    assert tx(client, acc, "credit", 100, "r1").json()["balance_after"] == "100.00"
    assert tx(client, acc, "credit", 50, "r2").json()["balance_after"] == "150.00"
    assert tx(client, acc, "debit", 30, "r3").json()["balance_after"] == "120.00"
    assert balance(client, acc) == "120.00"


def test_debit_over_balance_is_rejected_and_changes_nothing(client):
    acc = new_account(client)
    tx(client, acc, "credit", 100, "r1")
    res = tx(client, acc, "debit", 150, "r2")
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INSUFFICIENT_BALANCE"
    assert balance(client, acc) == "100.00"
    assert client.get(f"/accounts/{acc}/transactions").json()["pagination"]["total_records"] == 1


def test_debit_of_exact_balance_is_allowed(client):
    acc = new_account(client)
    tx(client, acc, "credit", 100, "r1")
    assert tx(client, acc, "debit", 100, "r2").status_code == 201
    assert balance(client, acc) == "0.00"


def test_duplicate_reference_is_rejected_and_changes_nothing(client):
    acc = new_account(client)
    first = tx(client, acc, "credit", 100, "ref-1").json()
    res = tx(client, acc, "credit", 100, "ref-1")
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "DUPLICATE_TRANSACTION"
    assert err["details"]["existing_transaction"]["id"] == first["id"]
    assert balance(client, acc) == "100.00"
    assert client.get(f"/accounts/{acc}/transactions").json()["pagination"]["total_records"] == 1


def test_same_reference_allowed_on_different_accounts(client):
    a, b = new_account(client), new_account(client)
    assert tx(client, a, "credit", 10, "shared").status_code == 201
    assert tx(client, b, "credit", 10, "shared").status_code == 201


def test_rejected_debit_does_not_use_up_the_reference(client):
    acc = new_account(client)
    assert tx(client, acc, "debit", 10, "retry-me").status_code == 422
    tx(client, acc, "credit", 10, "fund")
    assert tx(client, acc, "debit", 10, "retry-me").status_code == 201


def test_balance_consistency_example_from_brief(client):
    acc = new_account(client)
    for i, (t, a) in enumerate([("credit", 100), ("credit", 50), ("debit", 30), ("debit", 20)]):
        tx(client, acc, t, a, f"r{i}")
    assert balance(client, acc) == "100.00"


def test_decimal_amounts_are_exact(client):
    acc = new_account(client)
    for i in range(10):
        tx(client, acc, "credit", 0.1, f"r{i}")
    assert balance(client, acc) == "1.00"


@pytest.mark.parametrize("amount", [0, -5, "abc", 1.234, None])
def test_invalid_amount(client, amount):
    acc = new_account(client)
    res = tx(client, acc, "credit", amount, "r1")
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_type_and_missing_reference(client):
    acc = new_account(client)
    assert tx(client, acc, "refund", 10, "r1").status_code == 422
    assert tx(client, acc, "credit", 10, "").status_code == 422
    assert client.post(f"/accounts/{acc}/transactions", json={"type": "credit", "amount": 10}).status_code == 422


def test_invalid_body_is_a_validation_error(client):
    acc = new_account(client)
    res = client.post(f"/accounts/{acc}/transactions", content="not json",
                      headers={"content-type": "application/json"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_and_malformed_account_ids(client):
    missing = "00000000-0000-4000-8000-000000000000"
    for res in (client.get(f"/accounts/{missing}/balance"),
                tx(client, missing, "credit", 10, "r1"),
                client.get(f"/accounts/{missing}/transactions")):
        assert res.status_code == 404 and res.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert client.get("/accounts/not-a-uuid/balance").status_code == 422


# 4. History

def test_history_is_latest_first_filtered_and_paginated(client):
    acc = new_account(client)
    for i in range(5):
        tx(client, acc, "credit", 10, f"c{i}")
    for i in range(2):
        tx(client, acc, "debit", 1, f"d{i}")

    everything = client.get(f"/accounts/{acc}/transactions").json()
    refs = [t["reference_id"] for t in everything["data"]]
    assert refs == ["d1", "d0", "c4", "c3", "c2", "c1", "c0"]

    debits = client.get(f"/accounts/{acc}/transactions?type=debit").json()
    assert {t["type"] for t in debits["data"]} == {"debit"}
    assert debits["pagination"]["total_records"] == 2

    page2 = client.get(f"/accounts/{acc}/transactions?type=credit&page=2&limit=2").json()
    assert [t["reference_id"] for t in page2["data"]] == ["c2", "c1"]
    assert page2["pagination"] == {"page": 2, "limit": 2, "total_records": 5, "total_pages": 3}


@pytest.mark.parametrize("query", ["page=0", "limit=0", "limit=101", "page=abc", "type=refund"])
def test_invalid_history_parameters(client, query):
    acc = new_account(client)
    res = client.get(f"/accounts/{acc}/transactions?{query}")
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_ERROR"


# 5. Integrity

def test_api_has_no_edit_or_delete_for_transactions(client):
    acc = new_account(client)
    t = tx(client, acc, "credit", 10, "r1").json()
    for method in ("put", "patch", "delete"):
        res = getattr(client, method)(f"/accounts/{acc}/transactions")
        assert res.status_code == 405
    assert client.delete(f"/accounts/{acc}/transactions/{t['id']}").status_code == 404


def test_database_refuses_edit_or_delete_of_transactions(client, db_path):
    acc = new_account(client)
    tx(client, acc, "credit", 10, "r1")
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE transactions SET amount = 999")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM transactions")


def test_database_refuses_negative_balance(client, db_path):
    acc = new_account(client)
    conn = sqlite3.connect(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE accounts SET balance = -1 WHERE id = ?", (acc,))


def test_concurrent_debits_never_overspend(client):
    acc = new_account(client)
    tx(client, acc, "credit", 100, "fund")
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(lambda i: tx(client, acc, "debit", 30, f"d{i}").status_code, range(10)))
    assert codes.count(201) == 3 and codes.count(422) == 7
    assert balance(client, acc) == "10.00"


def test_concurrent_duplicates_create_one_transaction(client):
    acc = new_account(client)
    with ThreadPoolExecutor(max_workers=10) as pool:
        codes = list(pool.map(lambda _: tx(client, acc, "credit", 5, "same").status_code, range(10)))
    assert codes.count(201) == 1 and codes.count(409) == 9
    assert balance(client, acc) == "5.00"


# 9. Transfers

def transfer(client, src, dst, amount, ref="t1"):
    return client.post("/transfers", json={"from_account_id": src, "to_account_id": dst,
                                           "amount": amount, "reference_id": ref})


def test_transfer_example_from_brief(client):
    a, b = new_account(client, "A"), new_account(client, "B")
    tx(client, a, "credit", 100, "f")
    tx(client, b, "credit", 50, "f")
    res = transfer(client, a, b, 30)
    assert res.status_code == 201
    body = res.json()
    assert body["debit"]["transfer_id"] == body["credit"]["transfer_id"] == body["transfer_id"]
    assert balance(client, a) == "70.00" and balance(client, b) == "80.00"


def test_failed_transfer_changes_nothing(client):
    a, b = new_account(client), new_account(client)
    tx(client, a, "credit", 20, "f")
    assert transfer(client, a, b, 30).status_code == 422
    assert balance(client, a) == "20.00" and balance(client, b) == "0.00"
    assert client.get(f"/accounts/{b}/transactions").json()["pagination"]["total_records"] == 0


def test_transfer_rolls_back_when_the_credit_side_fails(client):
    # The destination already used this reference, so the credit fails after the
    # debit checks pass. The whole transfer must roll back.
    a, b = new_account(client), new_account(client)
    tx(client, a, "credit", 100, "f")
    tx(client, b, "credit", 1, "t1")
    assert transfer(client, a, b, 30, ref="t1").status_code == 409
    assert balance(client, a) == "100.00" and balance(client, b) == "1.00"


def test_transfer_needs_two_real_different_accounts(client):
    a = new_account(client)
    tx(client, a, "credit", 100, "f")
    missing = "00000000-0000-4000-8000-000000000000"
    assert transfer(client, a, missing, 10).status_code == 404
    assert transfer(client, missing, a, 10).status_code == 404
    assert transfer(client, a, a, 10).json()["error"]["code"] == "SAME_ACCOUNT_TRANSFER"
    assert balance(client, a) == "100.00"
