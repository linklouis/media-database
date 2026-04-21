from pydantic import BaseModel, Field
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