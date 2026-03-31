"""FastAPI router for server-side shortlist persistence."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from bedrock.services.shortlist_store import ShortlistStore

router = APIRouter(prefix="/shortlist", tags=["shortlist"])
store = ShortlistStore()


class ShortlistAddRequest(BaseModel):
    parcel_id: str


@router.get("")
def list_shortlist() -> list[dict]:
    return store.list_items()


@router.post("")
def add_to_shortlist(request: ShortlistAddRequest) -> dict:
    return store.add(request.parcel_id)


@router.delete("/{parcel_id}")
def remove_from_shortlist(parcel_id: str) -> dict:
    removed = store.remove(parcel_id)
    if not removed:
        raise HTTPException(status_code=404, detail={"error": "not_in_shortlist"})
    return {"removed": parcel_id}


@router.delete("")
def clear_shortlist() -> dict:
    count = store.clear()
    return {"cleared": count}
