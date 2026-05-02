from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from models import ActivityEvent, MediaItem, DetailsUpdate
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
client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db, client
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


@app.post("/activity/", response_model=MediaItem)
async def log_activity(event: ActivityEvent):
    # TODO: If there are no IN-PROGRESS watch_history elements, we should make a new one, and update the media's status.
    # TODO: Also, if we just reached the end of the media, update the status and change the repeats count if necessary.
    event_dict = event.model_dump()
    event_dict["timestamp"] = datetime.datetime.now(datetime.UTC)
    if "progress_added" not in event_dict.keys():
        event_dict["progress_added"] = 0
    
    async with await client.start_session() as session:
        async with session.start_transaction():
            media_update = await db.media.update_one(
                {"_id": ObjectId(event_dict["media_id"])}, 
                {"$inc": {
                    "progress": event_dict["progress_added"], # Total progress
                    "watch_history.$[elem].progress": event_dict["progress_added"] # Array item progress
                }},
                array_filters=[{"elem.status": "IN-PROGRESS"}],
                session=session
            )
            activity_log_insertion = await db.activity_log.insert_one({
                "media_id": ObjectId(event_dict["media_id"]),
                "media_type": event_dict["media_type"],
                "action_type": event_dict["action_type"],
                "progress_added": event_dict["progress_added"],
                "timestamp": event_dict["timestamp"]
            }, session=session)
    
    if activity_log_insertion.inserted_id:
        if media_update.modified_count > 0:
            item = await db.media.find_one({"_id": ObjectId(event_dict["media_id"])})
            if item:
                item["_id"] = str(item["_id"]) # Convert ObjectId to string
            return item
        raise HTTPException(status_code=500, detail="Failed to update media file")
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

@app.put("/media/{media_id}", response_model=MediaItem)
async def update_media_details(media_id: str, update_data: DetailsUpdate):
    
    # 1. Prepare the fields to update
    update_doc = {
        "score": update_data.new_score,
        "status": update_data.new_status,
        "watch_history": [attempt.model_dump() for attempt in update_data.watch_history]
    }

    async with await client.start_session() as session:
        async with session.start_transaction():
            
            # 2. Update the media document
            media_update = await db.media.update_one(
                {"_id": ObjectId(media_id)}, 
                {"$set": update_doc},
                session=session
            )
            
            # 3. Log the "Bulk Edit" action
            await db.activity_log.insert_one({
                "media_id": ObjectId(media_id),
                "media_type": "UNKNOWN", # You might want to pass this in from the frontend too
                "action_type": "BULK_EDIT",
                "timestamp": datetime.datetime.now(datetime.UTC)
            }, session=session)
            
    # 4. Fetch and return the fresh item
    item = await db.media.find_one({"_id": ObjectId(media_id)})
    if item:
        item["_id"] = str(item["_id"])
        return item
        
    raise HTTPException(status_code=404, detail="Media not found after update")


@app.get("/stats/lists")
async def get_list_stats():
    pipeline = [
        {"$unwind": "$lists"},
        
        {"$lookup": {
            "from": "lists",
            "localField": "lists",
            "foreignField": "name",
            "as": "list_details"
        }},
        
        {"$unwind": "$list_details"},
        
        {"$group": {
            "_id": "$lists", # Group by the list name
            "media_count": {"$sum": 1},
            "average_score": {"$avg": "$score"},
            "color": {"$first": "$list_details.color"},
            "description": {"$first": "$list_details.description"}
        }},
        
        {"$sort": {"media_count": -1}}
    ]

    # Run the aggregation
    cursor = db.media.aggregate(pipeline)
    results = await cursor.to_list(length=100)
    
    return results


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)