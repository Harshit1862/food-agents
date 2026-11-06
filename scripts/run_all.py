"""Start the whole system with one command: 8 restaurants + Swiggy + Zomato + the gateway.

    python -m scripts.run_all

Logs go to logs/<agent>.log. Press Ctrl-C (or send SIGTERM) and every agent is stopped cleanly.
On Linux the agents also stop if this launcher is killed hard (kill -9).
"""

import ctypes
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from pymongo import MongoClient

from food_agents.common import config

LOG_DIR = Path("logs")
STARTUP_TIMEOUT = 30  # seconds to wait for each agent to answer
CARD_PATH = "/.well-known/agent-card.json"


def build_agents() -> list[dict]:
    agents = [
        {
            "name": rid,
            "port": config.restaurant_port(rid),
            "cmd": [sys.executable, "-m", "food_agents.restaurant.main", "--id", rid],
            "ready_path": CARD_PATH,
        }
        for rid in config.RESTAURANT_IDS
    ]
    agents += [
        {
            "name": pid,
            "port": port,
            "cmd": [sys.executable, "-m", "food_agents.platforms.main", "--id", pid],
            "ready_path": CARD_PATH,
        }
        for pid, port in config.PLATFORM_PORTS.items()
    ]
    agents.append(
        {
            "name": "gateway",
            "port": config.GATEWAY_PORT,
            "cmd": [sys.executable, "-m", "food_agents.gateway.main"],
            "ready_path": "/api/health",
        }
    )
    return agents


def die_with_parent() -> None:
    """Runs in each child just before it starts: ask Linux to send it SIGTERM if this launcher dies."""
    try:
        ctypes.CDLL("libc.so.6").prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
    except Exception:
        pass  # not Linux: Ctrl-C / SIGTERM handling below still works


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def mongo_ok() -> bool:
    try:
        MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=2000).admin.command("ping")
        return True
    except Exception:
        return False


def tail(path: Path, lines: int = 6) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"


stopping = False


def request_stop(signum, frame) -> None:
    global stopping
    stopping = True


def stop_all(procs: list[dict]) -> None:
    alive = [p for p in procs if p["proc"].poll() is None]
    for p in alive:
        p["proc"].terminate()  # SIGTERM: uvicorn finishes in-flight requests and exits
    deadline = time.time() + 8
    for p in alive:
        try:
            p["proc"].wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p["proc"].kill()  # did not exit in time
            p["proc"].wait()
    for p in procs:
        p["log"].close()
    print(f"Stopped {len(alive)} agents.")


def main() -> None:
    agents = build_agents()

    if not mongo_ok():
        sys.exit(f"MongoDB is not reachable at {config.MONGO_URI}. Start it with: docker compose up -d")
    busy = [a for a in agents if port_in_use(a["port"])]
    if busy:
        names = ", ".join(f"{a['name']} (:{a['port']})" for a in busy)
        sys.exit(f"These ports are already in use: {names}\nIs another copy running? Stop it first.")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, request_stop)  # terminal window closed

    LOG_DIR.mkdir(exist_ok=True)
    procs: list[dict] = []
    try:
        print(f"Starting {len(agents)} agents (logs in {LOG_DIR}/) ...")
        for a in agents:
            log_path = LOG_DIR / f"{a['name']}.log"
            log = open(log_path, "w")
            proc = subprocess.Popen(
                a["cmd"],
                stdout=log,
                stderr=subprocess.STDOUT,
                preexec_fn=die_with_parent if os.name == "posix" else None,
            )
            procs.append({**a, "proc": proc, "log": log, "log_path": log_path, "reported_dead": False})

        # Wait until every agent answers (an agent is ready when it serves its Agent Card).
        with httpx.Client(timeout=1) as http:
            for p in procs:
                url = f"http://localhost:{p['port']}{p['ready_path']}"
                start, ok = time.time(), False
                while not stopping and p["proc"].poll() is None and time.time() - start < STARTUP_TIMEOUT:
                    try:
                        ok = http.get(url).status_code == 200
                    except httpx.HTTPError:
                        ok = False
                    if ok:
                        break
                    time.sleep(0.2)
                if stopping:
                    return
                if ok:
                    print(f"  ✓ {p['name']:<18} http://localhost:{p['port']}")
                else:
                    p["reported_dead"] = True
                    print(f"  ✗ {p['name']:<18} did not start. Last log lines:\n{tail(p['log_path'])}")

        if all(p["reported_dead"] for p in procs):
            print("Nothing started. Check the logs above.")
            return
        print(f"\nAll set. The web app will use the gateway:  http://localhost:{config.GATEWAY_PORT}/api/agents")
        print("Press Ctrl-C to stop everything.\n")

        while not stopping:
            for p in procs:
                if p["proc"].poll() is not None and not p["reported_dead"]:
                    p["reported_dead"] = True
                    print(f"  ! {p['name']} exited (code {p['proc'].returncode}). See {p['log_path']}")
            if all(p["proc"].poll() is not None for p in procs):
                print("All agents have exited.")
                break
            time.sleep(0.5)
    finally:
        print("\nStopping agents ...")
        stop_all(procs)


if __name__ == "__main__":
    main()