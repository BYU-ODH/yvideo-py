import hmac
import logging
import os
import subprocess
import threading

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
DEPLOY_ROOT = os.environ.get("DEPLOY_ROOT", "/srv/yvideo")
VALID_ENVIRONMENTS = {"staging", "prod"}


def run_deploy(env_name):
    deploy_dir = os.path.join(DEPLOY_ROOT, env_name)
    logger.info("Starting deploy for %s in %s", env_name, deploy_dir)
    try:
        result = subprocess.run(
            ["bash", "deploy/deploy.sh"],
            cwd=deploy_dir,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode == 0:
            logger.info("Deploy succeeded for %s:\n%s", env_name, result.stdout)
        else:
            logger.error(
                "Deploy failed for %s (exit %d):\nstdout: %s\nstderr: %s",
                env_name,
                result.returncode,
                result.stdout,
                result.stderr,
            )
    except Exception:
        logger.exception("Deploy error for %s", env_name)


@app.post("/deploy")
def deploy():
    data = request.get_json(force=True, silent=True) or {}

    secret = data.get("secret", "")
    if not hmac.compare_digest(secret, DEPLOY_SECRET):
        logger.warning("Rejected deploy request: bad secret")
        return jsonify({"error": "unauthorized"}), 403

    environment = data.get("environment", "")
    if environment not in VALID_ENVIRONMENTS:
        return jsonify({"error": f"invalid environment: {environment}"}), 400

    logger.info("Accepted deploy request for %s", environment)
    threading.Thread(target=run_deploy, args=(environment,), daemon=True).start()
    return jsonify({"status": "accepted", "environment": environment}), 202
