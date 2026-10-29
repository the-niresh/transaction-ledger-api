from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Reference = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
# Money is exact decimal with 2 places, never float.
Amount = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=2)]
TxType = Literal["credit", "debit"]


class Strict(BaseModel):
    # Unknown fields are rejected, so a client cannot send "balance" or "id".
    model_config = ConfigDict(extra="forbid")


class AccountCreate(Strict):
    name: Name


class TransactionCreate(Strict):
    type: TxType
    amount: Amount
    reference_id: Reference
    description: Description = ""


class TransferCreate(Strict):
    from_account_id: UUID
    to_account_id: UUID
    amount: Amount
    reference_id: Reference
    description: Description = ""


class Account(BaseModel):
    id: UUID
    name: str
    balance: Decimal
    created_at: datetime


class Balance(BaseModel):
    account_id: UUID
    balance: Decimal


class Transaction(BaseModel):
    id: UUID
    account_id: UUID
    type: TxType
    amount: Decimal
    description: str
    reference_id: str
    balance_after: Decimal
    transfer_id: UUID | None
    created_at: datetime


class Pagination(BaseModel):
    page: int
    limit: int
    total_records: int
    total_pages: int


class TransactionPage(BaseModel):
    data: list[Transaction]
    pagination: Pagination


class Transfer(BaseModel):
    transfer_id: UUID
    reference_id: str
    amount: Decimal
    debit: Transaction
    credit: Transaction
