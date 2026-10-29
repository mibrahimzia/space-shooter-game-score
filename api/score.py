# api/score.py
import json
import os
import urllib.request
import urllib.parse
from http.server import BaseHTTPRequestHandler

# Vercel automatically provides these environment variables when you link Vercel KV
KV_URL = os.environ.get("KV_REST_API_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN")

def kv_cmd(cmd, *args):
    """Helper to execute Vercel KV (Redis) commands via HTTP REST API."""
    if not KV_URL or not KV_TOKEN: return None
    url = KV_URL
    payload = json.dumps([cmd] + list(args)).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {KV_TOKEN}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode()).get("result")
    except Exception:
        return None

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        device_id = params.get("device_id", [""])[0]

        device_scores = {}
        if device_id:
            # HGETALL retrieves the name/score pairs for this specific device
            res = kv_cmd("HGETALL", f"device:{device_id}")
            if res:
                for i in range(0, len(res), 2):
                    device_scores[res[i]] = int(res[i+1])

        # Get Top 10 Global Leaderboard
        top = kv_cmd("ZREVRANGE", "global_scores", 0, 9, "WITHSCORES")
        global_scores = []
        if top:
            for i in range(0, len(top), 2):
                global_scores.append({"name": top[i].split("::")[0], "score": int(top[i+1])})

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"device_scores": device_scores, "global_scores": global_scores}).encode('utf-8'))

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length).decode('utf-8'))

        device_id = body.get("device_id")
        name = body.get("name")
        score = int(body.get("score", 0))

        if device_id and name:
            current_score = kv_cmd("HGET", f"device:{device_id}", name)
            current_score = int(current_score) if current_score else 0

            # Only update online database if the new score is higher
            if score > current_score:
                kv_cmd("HSET", f"device:{device_id}", name, score)
                # Add to global leaderboard (format: "NAME::DEVICE_ID" to ensure uniqueness)
                kv_cmd("ZADD", "global_scores", score, f"{name}::{device_id}")

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
