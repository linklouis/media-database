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
    # TODO: If there are no IN-PROGRESS watch_history elements, make a new one, and update the media's status.
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


@app.get("/lists/")
async def get_all_lists():
    cursor = db.lists.find({})
    results = []
    async for document in cursor:
        document["_id"] = str(document["_id"])
        results.append(document)
    return results




@app.post("/media/", response_model=MediaItem)
async def create_media(title: str, status: str = "PLANNING"):
    new_doc = {
        "title": title,
        "status": status,
        "media_type": "Anime", # Default
        "score": 0,
        "total_units": 0,
        "lists": [],
        "watch_history": []
    }
    result = await db.media.insert_one(new_doc)
    new_doc["_id"] = str(result.inserted_id)
    return new_doc







@app.get("/stats/lists")
async def get_list_stats(list_name: str = None, media_type: str = None):
    match_query = {}
    
    # Only add filters if the user actually selected something other than "All"
    if list_name and list_name != "All":
        match_query["lists"] = list_name
        
    if media_type and media_type != "All":
        match_query["media_type"] = media_type


    pipeline = []
    
    # If dict isn't empty, make $match the first stage of the pipeline
    if match_query:
        pipeline.append({"$match": match_query})

    pipeline.extend([
        {"$unwind": {"path": "$lists", "preserveNullAndEmptyArrays": True}},
        
        {"$lookup": {
            "from": "lists",
            "localField": "lists",
            "foreignField": "name",
            "as": "list_details"
        }},
        
        {"$unwind": {"path": "$list_details", "preserveNullAndEmptyArrays": True}}, 
        
        {"$group": {
            "_id": "$lists", 
            "media_count": {"$sum": 1},
            "average_score": {"$avg": "$score"},
            "color": {"$first": "$list_details.color"},
            "description": {"$first": "$list_details.description"}
        }},
        
        {"$sort": {"media_count": -1}}
    ])

    cursor = db.media.aggregate(pipeline)
    results = await cursor.to_list(length=100)
    
    return results


@app.get("/stats/media-types")
async def get_media_type_stats(list_name: str = None, media_type: str = None):
    match_query = {}
    if list_name and list_name != "All": match_query["lists"] = list_name
    if media_type and media_type != "All": match_query["media_type"] = media_type

    pipeline = []
    if match_query: pipeline.append({"$match": match_query})

    pipeline.extend([
        {"$group": {
            "_id": "$media_type",
            "media_count": {"$sum": 1},
            "average_score": {"$avg": "$score"},
            "max_score": {"$max": "$score"}
        }},
        {"$sort": {"average_score": -1}}
    ])
    cursor = db.media.aggregate(pipeline)
    return await cursor.to_list(length=100)


@app.get("/stats/score-histo")
async def get_score_histo_stats(list_name: str = None, media_type: str = None, bins: int = None):
    match_query = {}
    
    # Only add filters if the user actually selected something other than "All"
    if list_name and list_name != "All":
        match_query["lists"] = list_name
        
    if media_type and media_type != "All":
        match_query["media_type"] = media_type


    pipeline = []
    
    # If dict isn't empty, make $match the first stage of the pipeline
    if match_query:
        pipeline.append({"$match": match_query})

    pipeline.extend([
        {"$match": {"score": {"$gt": 0}}},
        
        {"$bucketAuto": {
            "groupBy": "$score",
            "buckets": bins if bins else 10,
            "output": {
                "media_count": {"$sum": 1}
            }
        }}
    ])

    cursor = db.media.aggregate(pipeline)
    results = await cursor.to_list(length=100)
    
    return results

@app.get("/stats/watch-date")
async def get_watch_date_stats(list_name: str = None, media_type: str = None, precision: str = 'month'):
    # TODO: Actually have this use both the start and end date, but also the activity log
    match_query = {}
    if list_name and list_name != "All": match_query["lists"] = list_name
    if media_type and media_type != "All": match_query["media_type"] = media_type

    # Determine date format based on precision
    date_format = "%Y-%m" if precision == 'month' else "%Y"

    pipeline = []
    if match_query: pipeline.append({"$match": match_query})
    
    pipeline.extend([
        {"$unwind": "$watch_history"},
        # Only count completed attempts that have an end_date
        {"$match": {"watch_history.status": "COMPLETED", "watch_history.end_date": {"$ne": None}}},
        {"$group": {
            "_id": {"$dateToString": {"format": date_format, "date": "$watch_history.end_date"}},
            "count": {"$sum": 1}
        }},
        {"$sort": {"_id": 1}} # Sort chronologically
    ])

    cursor = db.media.aggregate(pipeline)
    return await cursor.to_list(length=500)

# Don't have a release date yet, not sure I'll have time to add it
# @app.get("/stats/release-date")
# async def get_release_date_stats(list_name: str = None, media_type: str = None):
#     match_query = {"release_date": {"$ne": None}} # Only items with a release date
#     if list_name and list_name != "All": match_query["lists"] = list_name
#     if media_type and media_type != "All": match_query["media_type"] = media_type

#     pipeline = [
#         {"$match": match_query},
#         {"$group": {
#             "_id": {"$year": "$release_date"},
#             "count": {"$sum": 1}
#         }},
#         {"$sort": {"_id": 1}}
#     ]
#     cursor = db.media.aggregate(pipeline)
#     return await cursor.to_list(length=500)



@app.get("/stats/status-dist")
async def get_status_dist_stats(list_name: str = None):
    match_query = {}
    
    # Only add filters if the user actually selected something other than "All"
    if list_name and list_name != "All":
        match_query["lists"] = list_name


    pipeline = []
    
    # If dict isn't empty, make $match the first stage of the pipeline
    if match_query:
        pipeline.append({"$match": match_query})

    pipeline.extend([
        {"$group": {
            "_id": "$status",
            "media_count": {"$sum": 1},
        }},
        
        {"$sort": {"media_count": -1}}
    ])

    cursor = db.media.aggregate(pipeline)
    results = await cursor.to_list(length=100)
    
    return results

@app.get("/stats/media-type-dist")
async def get_media_type_dist_stats(list_name: str = None):
    match_query = {}
    
    # Only add filters if the user actually selected something other than "All"
    if list_name and list_name != "All":
        match_query["lists"] = list_name


    pipeline = []
    
    # If dict isn't empty, make $match the first stage of the pipeline
    if match_query:
        pipeline.append({"$match": match_query})

    pipeline.extend([
        {"$group": {
            "_id": "$media_type",
            "media_count": {"$sum": 1},
        }},
        
        {"$sort": {"media_count": -1}}
    ])

    cursor = db.media.aggregate(pipeline)
    results = await cursor.to_list(length=100)
    
    return results


@app.get("/stats/list-dist")
async def get_list_dist_stats(media_type: str = None):
    match_query = {}
    if media_type and media_type != "All":
        match_query["media_type"] = media_type

    pipeline = []
    if match_query:
        pipeline.append({"$match": match_query})

    pipeline.extend([
        {"$unwind": "$lists"},
        
        {"$group": {
            "_id": "$lists",
            "media_count": {"$sum": 1}
        }},
        
        {"$sort": {"media_count": -1}}
    ])

    cursor = db.media.aggregate(pipeline)
    return await cursor.to_list(length=100)

@app.get("/stats/completion-speed")
async def get_completion_speed():
    pipeline = [
        {"$unwind": "$watch_history"},

        {"$match": {
            "watch_history.status": "COMPLETED",
            "watch_history.start_date": {"$ne": None},
            "watch_history.end_date": {"$ne": None}
        }},

        {"$project": {
            "title": 1,
            "days_taken": {
                "$divide": [
                    {"$subtract": ["$watch_history.end_date", "$watch_history.start_date"]},
                    1000 * 60 * 60 * 24
                ]
            }
        }},

        {"$group": {
            "_id": "Average Completion Time",
            "avg_days": {"$avg": "$days_taken"},
            "min_days": {"$min": "$days_taken"},
            "max_days": {"$max": "$days_taken"}
        }}
    ]

    cursor = db.media.aggregate(pipeline)
    return await cursor.to_list(length=1)





if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)