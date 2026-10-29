from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import sqlite3
from fastapi import Depends, FastAPI, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, ledger
from .errors import ApiError, body
from .schemas import (Account, AccountCreate, Balance, Transaction, TransactionCreate,
                      TransactionPage, Transfer, TransferCreate)

UI = (Path(__file__).parent / "static" / "index.html").read_text()
ERRORS = {404: {"description": "Account not found"}, 422: {"description": "Validation or business rule error"}}


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(db.database_path())
    yield


app = FastAPI(
    title="Transaction Ledger API",
    version="1.0.0",
    description="Accounts, credits, debits, history and transfers. Every error uses the same shape: "
                '`{"error": {"code", "message", "details"}}`.',
    lifespan=lifespan,
)


def get_conn() -> Iterator[sqlite3.Connection]:
    conn = db.connect(db.database_path())
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


@app.exception_handler(ApiError)
async def api_error(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(jsonable_encoder(body(exc.code, exc.message, exc.details)), status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or e["loc"][0], "issue": e["msg"]}
               for e in exc.errors()]
    return JSONResponse(body("VALIDATION_ERROR", "The request is not valid.", details), status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    return JSONResponse(body(codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)),
                        status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(body("INTERNAL_ERROR", "Something went wrong. No changes were saved."), status_code=500)


@app.get("/", include_in_schema=False)
def ui():
    return HTMLResponse(UI)


@app.post("/accounts", status_code=201, response_model=Account, tags=["accounts"])
def create_account(data: AccountCreate, conn: Conn):
    """Create an account. Balance always starts at 0 and cannot be set by the client."""
    return ledger.create_account(conn, data)


@app.get("/accounts/{account_id}", response_model=Account, responses=ERRORS, tags=["accounts"])
def get_account(account_id: UUID, conn: Conn):
    return ledger.get_account(conn, str(account_id))


@app.get("/accounts/{account_id}/balance", response_model=Balance, responses=ERRORS, tags=["accounts"])
def get_balance(account_id: UUID, conn: Conn):
    account = ledger.get_account(conn, str(account_id))
    return {"account_id": account["id"], "balance": account["balance"]}


@app.post("/accounts/{account_id}/transactions", status_code=201, response_model=Transaction,
          responses={**ERRORS, 409: {"description": "Duplicate reference_id for this account"}},
          tags=["transactions"])
def add_transaction(account_id: UUID, data: TransactionCreate, conn: Conn):
    """Add a credit or debit. A debit larger than the balance is rejected and changes nothing.
    Sending the same reference_id again for this account returns 409 and changes nothing."""
    return ledger.add_transaction(conn, str(account_id), data)


@app.get("/accounts/{account_id}/transactions", response_model=TransactionPage, responses=ERRORS,
         tags=["transactions"])
def list_transactions(
    account_id: UUID,
    conn: Conn,
    type: Literal["credit", "debit"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Latest first. Filter with `type`, page with `page` and `limit` (max 100)."""
    return ledger.list_transactions(conn, str(account_id), type, page, limit)


@app.post("/transfers", status_code=201, response_model=Transfer,
          responses={**ERRORS, 409: {"description": "Duplicate reference_id"}}, tags=["transfers"])
def create_transfer(data: TransferCreate, conn: Conn):
    """Move money between two accounts. The debit and the credit are saved together or not at all."""
    return ledger.transfer(conn, data)
