"""One command to bring up the whole backend: Docker infra (Keycloak, MongoDB), the sample
data, and every agent (8 restaurants + Swiggy + Zomato + the gateway).

    python run.py                 start everything; seed MongoDB only if it's empty
    python run.py --seed          wipe and reload the sample data first
    python run.py --no-docker     skip `docker compose up` (assume Keycloak/Mongo are already running)

Press Ctrl-C to stop the agents. Docker containers are left running (stop them with
`docker compose down` when you're done) since they're infra, not part of this run.
"""

import argparse
import subprocess
import sys
import time

import httpx
from pymongo import MongoClient

from food_agents.common import config
from scripts.run_all import main as run_agents

WAIT_TIMEOUT = 60  # seconds to wait for Docker services to answer


def docker_compose_up() -> None:
    print("Starting Docker services (Keycloak, MongoDB) ...")
    subprocess.run(["docker", "compose", "up", "-d"], check=True)


def wait_for_mongo(timeout: int = WAIT_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=1000).admin.command("ping")
            print("  ✓ MongoDB is up")
            return
        except Exception:
            time.sleep(0.5)
    sys.exit(f"MongoDB did not answer at {config.MONGO_URI} within {timeout}s.")


def wait_for_keycloak(timeout: int = WAIT_TIMEOUT) -> None:
    if not config.AUTH_ENABLED:
        print("  (AUTH_ENABLED=false: skipping Keycloak)")
        return
    deadline = time.time() + timeout
    with httpx.Client(timeout=2) as http:
        while time.time() < deadline:
            try:
                if http.get(config.KEYCLOAK_ISSUER).status_code == 200:
                    print("  ✓ Keycloak is up")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    sys.exit(f"Keycloak did not answer at {config.KEYCLOAK_ISSUER} within {timeout}s.")


def db_is_empty() -> bool:
    db = MongoClient(config.MONGO_URI)[config.MONGO_DB]
    return db.restaurants.count_documents({}) == 0


def seed_database() -> None:
    print("Seeding MongoDB with sample data ...")
    subprocess.run([sys.executable, "-m", "seed.seed"], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the whole food-agents backend.")
    parser.add_argument("--no-docker", action="store_true", help="assume Keycloak/MongoDB are already running")
    parser.add_argument("--seed", action="store_true", help="reseed MongoDB even if it already has data")
    args = parser.parse_args()

    if not args.no_docker:
        docker_compose_up()

    print("Waiting for Docker services ...")
    wait_for_mongo()
    wait_for_keycloak()

    if args.seed or db_is_empty():
        seed_database()

    run_agents()  # 8 restaurants + Swiggy + Zomato + the gateway (blocks until Ctrl-C)


if __name__ == "__main__":
    main()
