import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

# Configure these in Vercel Project Settings -> Environment Variables.
KV_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
KV_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")


def kv_cmd(cmd, *args):
    """Execute a Redis command through the Upstash REST API."""
    if not KV_URL or not KV_TOKEN:
        raise RuntimeError("Upstash environment variables are not configured")

    payload = json.dumps([cmd, *args]).encode("utf-8")
    request = urllib.request.Request(
        KV_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {KV_TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.loads(response.read().decode("utf-8"))

    if result.get("error"):
        raise RuntimeError(f"Upstash Redis error: {result['error']}")
    if "result" not in result:
        raise RuntimeError("Unexpected response from Upstash Redis")
    return result["result"]


def read_device_scores(device_id):
    values = kv_cmd("HGETALL", f"device:{device_id}") or []
    scores = {}
    for index in range(0, len(values) - 1, 2):
        try:
            scores[str(values[index])] = max(0, int(values[index + 1]))
        except (TypeError, ValueError):
            continue
    return scores


class handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        device_id = params.get("device_id", [""])[0].strip()[:128]
        try:
            device_scores = read_device_scores(device_id) if device_id else {}
            rows = kv_cmd("ZREVRANGE", "global_scores", 0, 9, "WITHSCORES") or []
            global_scores = []
            for index in range(0, len(rows) - 1, 2):
                member = str(rows[index])
                try:
                    score = max(0, int(rows[index + 1]))
                except (TypeError, ValueError):
                    continue
                name = member.rsplit("::", 1)[0] if "::" in member else member
                global_scores.append({"name": name[:12], "score": score})

            self.send_json(200, {
                "device_scores": device_scores,
                "global_scores": global_scores,
            })
        except Exception as exc:
            print(f"Leaderboard GET failed: {exc}")
            self.send_json(503, {"status": "error", "message": "Leaderboard temporarily unavailable"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 8192:
                self.send_json(400, {"status": "error", "message": "Invalid request body"})
                return

            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                self.send_json(400, {"status": "error", "message": "Expected a JSON object"})
                return

            device_id = str(body.get("device_id", "")).strip()
            name = str(body.get("name", "")).strip()[:12]
            try:
                score = int(body.get("score", 0))
            except (TypeError, ValueError):
                self.send_json(400, {"status": "error", "message": "Score must be an integer"})
                return

            if not device_id or len(device_id) > 128 or not name:
                self.send_json(400, {"status": "error", "message": "Device ID and player name are required"})
                return
            if score < 0 or score > 1_000_000_000:
                self.send_json(400, {"status": "error", "message": "Score is outside the allowed range"})
                return

            key = f"device:{device_id}"
            previous_scores = read_device_scores(device_id)
            best_score = max([score, *previous_scores.values()])

            # Remove legacy leaderboard members for this device, regardless of name.
            members = kv_cmd("ZRANGE", "global_scores", 0, -1) or []
            stale_members = [
                str(member) for member in members
                if str(member).endswith("::" + device_id)
            ]
            if stale_members:
                kv_cmd("ZREM", "global_scores", *stale_members)

            # Keep one current display name per device, retaining the personal best.
            kv_cmd("DEL", key)
            kv_cmd("HSET", key, name, best_score)
            kv_cmd("ZADD", "global_scores", best_score, f"{name}::{device_id}")

            self.send_json(200, {"status": "ok", "name": name, "score": best_score})
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"status": "error", "message": "Invalid JSON"})
        except Exception as exc:
            print(f"Leaderboard POST failed: {exc}")
            self.send_json(503, {"status": "error", "message": "Score could not be saved"})


if __name__ == "__main__":
    # Vercel imports this file as a serverless handler.
    pass
