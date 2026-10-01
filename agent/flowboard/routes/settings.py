"""Settings routes.

GET returns only whitelisted keys, so account/token/cookie values are never
in the payload — the whitelist is the masking. PUT refuses any key outside
the whitelist, so a caller cannot write credential/account keys even by
guessing their names.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.services import settings_store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings() -> dict[str, Any]:
    """Effective settings (defaults + user overrides), whitelisted."""
    return settings_store.get_all()


@router.get("/defaults")
def get_defaults() -> dict[str, Any]:
    """The shipped defaults only, ignoring user overrides."""
    return settings_store.defaults()


class UpdateBody(BaseModel):
    # Free-form key→value; the service enforces the whitelist.
    values: dict[str, Any]


@router.put("")
def put_settings(body: UpdateBody) -> dict[str, Any]:
    if not body.values:
        raise HTTPException(status_code=400, detail="no values provided")
    updated, rejected = settings_store.set_many(body.values)
    if rejected:
        # Fail loudly rather than silently dropping keys — a client sending
        # a non-whitelisted key has a bug or is probing, and either way the
        # caller should know its write did not fully land.
        raise HTTPException(
            status_code=400,
            detail=f"not settable (whitelisted keys only): {sorted(rejected)}",
        )
    return {"updated": updated}


class ResetBody(BaseModel):
    keys: list[str] | None = None


@router.post("/reset")
def reset_settings(body: ResetBody) -> dict[str, int]:
    """Drop overrides so values fall back to config.json/BASELINE. Omit
    ``keys`` to reset everything."""
    removed = settings_store.reset(body.keys)
    return {"removed": removed}
