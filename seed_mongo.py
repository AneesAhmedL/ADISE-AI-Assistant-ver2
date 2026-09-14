import os
import certifi
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "").strip()
if MONGO_URI.startswith("MONGODB_URI="):
    MONGO_URI = MONGO_URI.replace("MONGODB_URI=", "")

if not MONGO_URI:
    print("❌ Error: MONGO_URI not found in your environment or .env file.")
    exit(1)

def seed_mongo():
    try:
        client = MongoClient(
            MONGO_URI,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=5000
        )
        db = client["adise_db"]

        # Define schedule documents
        schedules = [
            {
                "name": "schedule1",
                "details": "09:00 AM - Python Programming\n11:00 AM - Engineering Graphics\n02:00 PM - Circuit Analysis"
            },
            {
                "name": "schedule2",
                "details": "10:00 AM - Flask API Testing\n01:00 PM - UI Optimization\n04:00 PM - Model Verification"
            }
        ]

        # Use update_one with upsert=True to insert or update safely based on the 'name' field
        for sch in schedules:
            db.schedules.update_one(
                {"name": sch["name"]},
                {"$set": {"details": sch["details"]}},
                upsert=True
            )

        print("✅ Schedules successfully added to MongoDB (adise_db -> schedules)!")
    except Exception as e:
        print(f"❌ Database connection or seeding failed: {e}")

if __name__ == "__main__":
    seed_mongo()
