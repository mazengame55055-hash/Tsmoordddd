from __future__ import annotations

from . import crypto
from .db import Database
from .kaggle_client import Credentials


async def load_credentials(db: Database, discord_id: int) -> Credentials | None:
    row = await db.get_user(discord_id)
    if not row:
        return None
    try:
        secret = crypto.decrypt(row["token_enc"])
    except crypto.InvalidToken:
        return None  # key changed or data corrupted -> user must /login again
    return Credentials(row["kaggle_username"], secret, row["token_kind"])
