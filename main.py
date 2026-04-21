from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from models import ActivityEvent
import datetime
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException, Query
from bson import ObjectId
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import uvicorn

load_dotenv()
MONGO_CONNECTION_STRING = os.getenv("MONGO_URI")

db = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db
    # Connect to MongoDB cluster
    client = AsyncIOMotorClient(MONGO_CONNECTION_STRING)
    db = client["personal-media-database"]
    
    yield # Hands control to FastAPI to run web requests
    
    print("App is shutting down!")
    client.close()


app = FastAPI(title="Personal Media Database API", lifespan=lifespan)

# Allow frontend to talk to API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # TODO: In production, put actual frontend URL here
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/activity/", response_model=ActivityEvent)
async def log_activity(event: ActivityEvent):
    # TODO: will put the transaction logic here soon!
    event_dict = event.model_dump()
    event_dict["timestamp"] = datetime.datetime.utcnow()
    
    # Just a basic insert for now
    new_event = await db.activity_log.insert_one(event_dict)
    
    if new_event.inserted_id:
        return event
    raise HTTPException(status_code=500, detail="Failed to log activity")


# Route 1: Get ALL media (with an optional filter for status)
@app.get("/media/")
async def get_all_media(status: str = Query(None, description="Filter by status (e.g., WATCHING, COMPLETED)")):
    query = {}
    if status:
        query["status"] = status
        
    cursor = db.media.find(query)
    media_list = []
    
    async for document in cursor:
        # Convert BSON ObjectIds to standard strings for the frontend
        document["_id"] = str(document["_id"])
        if "parent_id" in document and document["parent_id"]:
            document["parent_id"] = str(document["parent_id"])
        
        media_list.append(document)
        
    return media_list

# Route 2: Get ONE specific media item (for detail page)
@app.get("/media/{media_id}")
async def get_media_by_id(media_id: str):
    # Ensure the string is a valid MongoDB ObjectId
    if not ObjectId.is_valid(media_id):
        raise HTTPException(status_code=400, detail="Invalid ID format")
        
    document = await db.media.find_one({"_id": ObjectId(media_id)})
    
    if document:
        document["_id"] = str(document["_id"])
        if "parent_id" in document and document["parent_id"]:
            document["parent_id"] = str(document["parent_id"])
        return document
        
    raise HTTPException(status_code=404, detail="Media not found")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)