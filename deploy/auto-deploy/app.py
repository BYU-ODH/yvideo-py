import hmac
import logging
import os
from pathlib import Path
import subprocess
import threading
import tomllib

from flask import Flask
from flask import jsonify
from flask import request

app = Flask(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("auto-deploy")

DEPLOY_SECRET = os.environ["DEPLOY_SECRET"]
JOBS_CONFIG_PATH = Path(
    os.environ.get("JOBS_CONFIG_PATH", Path(__file__).parent / "jobs.toml")
)

with JOBS_CONFIG_PATH.open("rb") as f:
    JOBS = tomllib.load(f).get("jobs", {})

logger.info("Loaded jobs: %s", list(JOBS.keys()))


def run_job(job_name, working_dir, command):
    logger.info("Starting job %s: %s (in %s)", job_name, command, working_dir)
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("Job %s succeeded:\n%s", job_name, result.stdout)
        else:
            logger.error(
                "Job %s failed (exit %d):\nstdout: %s\nstderr: %s",
                job_name,
                result.returncode,
                result.stdout,
                result.stderr,
            )
    except Exception:
        logger.exception("Job %s error", job_name)


@app.post("/deploy")
def deploy():
    data = request.get_json(force=True, silent=True) or {}

    secret = data.get("secret", "")
    if not hmac.compare_digest(secret, DEPLOY_SECRET):
        logger.warning("Rejected request: bad secret")
        return jsonify({"error": "unauthorized"}), 403

    job_name = data.get("job", "")
    if job_name not in JOBS:
        return jsonify({"error": f"unknown job: {job_name}"}), 400

    job = JOBS[job_name]
    logger.info("Accepted request for job %s", job_name)
    threading.Thread(
        target=run_job,
        args=(job_name, job["working_dir"], job["command"]),
        daemon=True,
    ).start()
    return jsonify({"status": "accepted", "job": job_name}), 202
