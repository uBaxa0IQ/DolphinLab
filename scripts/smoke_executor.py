import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4
import httpx


async def main():
    ident = "executor-check-" + uuid4().hex[:8]
    root = Path("/data/runs") / ident
    task = root / "task"
    task.mkdir(parents=True)
    (task / "sales.csv").write_text("amount\n100000\n60000\n80000\n90000\n")
    folder = root / "runtime/jobs" / ident
    code = folder / "code"
    code.mkdir(parents=True)
    folder.chmod(0o770)
    (folder / "tmp").mkdir(mode=0o770)
    script = code / "check.py"
    script.write_text(
        'import csv,json,os\nfrom pathlib import Path\nrows=list(csv.DictReader(Path(os.environ["TASK_ROOT"],"sales.csv").open()))\nassert sum(int(x["amount"]) for x in rows)==330000\nassert os.getuid()==65534\nassert not os.getenv("OPENAI_API_KEY")\nPath(os.environ["JOB_DIR"],"check.json").write_text(json.dumps({"sum":330000,"uid":os.getuid()}))\nprint("isolated CSV sum and clean environment OK")\n'
    )
    spec = {
        "id": ident,
        "token": uuid4().hex,
        "folder": str(folder),
        "script": str(script),
        "args": [],
        "cpus": 1,
        "ram_mb": 128,
        "timeout": 10,
        "deadline_wall": time.time() + 30,
        "log_bytes": 10000,
    }
    manifest = folder / "job.json"
    manifest.write_text(json.dumps(spec))
    ref = {"manifest": str(manifest), "token": spec["token"]}
    async with httpx.AsyncClient(
        base_url=os.environ["EXECUTOR_URL"],
        headers={"X-Executor-Token": os.environ["EXECUTOR_TOKEN"]},
        timeout=15,
    ) as client:
        r = await client.post("/execute", json=ref)
        r.raise_for_status()
        # The first launch response may have been lost; resubmission must reconcile identity.
        duplicate = await client.post("/execute", json=ref)
        duplicate.raise_for_status()
        assert duplicate.json()["state"] in {"launching", "running", "succeeded"}
        for _ in range(30):
            await asyncio.sleep(0.3)
            r = await client.post("/inspect", json=ref)
            r.raise_for_status()
            data = r.json()
            if data.get("result"):
                assert data["state"] == "succeeded", data
                first = (folder / "result.json").read_bytes()
                repeat = await client.post("/execute", json=ref)
                repeat.raise_for_status()
                assert repeat.json()["state"] == "succeeded"
                assert (folder / "result.json").read_bytes() == first
                print(
                    json.dumps(
                        {
                            "job_id": ident,
                            "state": data["state"],
                            "no_duplicate_launch": True,
                            "output": (folder / "check.json").read_text(),
                        }
                    )
                )
                return
        raise RuntimeError("No terminal job result")


if __name__ == "__main__":
    asyncio.run(main())
