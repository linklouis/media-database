from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime

class ActivityEvent(BaseModel):
    media_id: str
    media_type: str
    action_type: str # PROGRESS_UPDATE, STATUS_CHANGE, etc.
    progress_added: Optional[int] = None
    
    class Config:
        json_schema_extra = {
            "example": {
                "media_id": "60d5ec49f1b2c86714a5b4a1",
                "media_type": "Anime",
                "action_type": "PROGRESS_UPDATE",
                "progress_added": 2
            }
        }

class WatchAttempt(BaseModel):
    progress: int
    status: str
    watch_notes: Optional[str] = None

class MediaItem(BaseModel):
    media_id: str = Field(alias="_id")
    media_type: str
    score: int
    status: str
    title: str
    total_units: Optional[int]  # Has to be optional for now, because we don't have this data in the database yet.
    lists: List[str]
    watch_history: List[WatchAttempt]

class DetailsUpdate(BaseModel):
    media_id: str
    new_progress: int
    new_score: int
    watch_history: list # TODO: Right type?

    model_config = ConfigDict(populate_by_name=True)