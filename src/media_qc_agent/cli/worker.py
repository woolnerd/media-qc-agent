"""Poll approved runs using a durable synthetic provider and bounded policy."""

import argparse
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from media_qc_agent.domain.ids import validate_run_id
from media_qc_agent.workflow.database import Database
from media_qc_agent.workflow.durable_provider import DurableFakeVideoProvider
from media_qc_agent.workflow.worker import DurableWorker
from media_qc_agent.workflow.worker_models import WorkerPolicy


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path(".local/review.sqlite3"))
    parser.add_argument("--owner", default=f"worker-{os.getpid()}")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-in-flight", type=int, default=2)
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    try:
        policy = WorkerPolicy(
            max_in_flight=args.max_in_flight, max_attempts=args.max_attempts
        )
        validate_run_id(args.owner)
    except ValueError as error:
        parser.error(str(error))
    database = Database(args.database)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with database.repository() as repository:
        repository.initialize()
    provider = DurableFakeVideoProvider(args.database.with_suffix(".provider.sqlite3"))
    worker = DurableWorker(
        database,
        provider,
        owner=args.owner,
        policy=policy,
    )
    try:
        _poll(worker, once=args.once)
    except KeyboardInterrupt:
        pass


def _poll(worker: DurableWorker, *, once: bool) -> None:
    while True:
        result = worker.run_once()
        if result.outcome != "idle" or once:
            print(json.dumps(asdict(result)), flush=True)
        if once:
            return
        time.sleep(0.5)


if __name__ == "__main__":
    main()
