from pymongo import MongoClient
from pymongo.database import Database

from food_agents.common import config

_client: MongoClient | None = None


def get_db() -> Database:
    """Return the database handle, creating the connection on first use."""
    global _client
    if _client is None:
        _client = MongoClient(config.MONGO_URI)
    return _client[config.MONGO_DB]