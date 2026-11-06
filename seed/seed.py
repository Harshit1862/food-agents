import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()  # reads .env from the project root

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "food_agents")
DATA_DIR = Path(__file__).parent / "data"

COLLECTIONS = ["restaurants", "menu_items", "platforms", "platform_listings"]


def main():
    db = MongoClient(MONGO_URI)[MONGO_DB]

    for name in COLLECTIONS:
        docs = json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        db[name].drop()            # wipe the old copy...
        db[name].insert_many(docs)  # ...and load fresh data
        print(f"{name}: inserted {len(docs)}")

    # Indexes make lookups fast, and the unique one blocks duplicate listings.
    db["menu_items"].create_index("restaurant_id")
    db["platform_listings"].create_index(
        [("platform_id", 1), ("restaurant_id", 1)], unique=True
    )


if __name__ == "__main__":
    main()