"""Deploy explorer/ (+ widget/ at /widget/) to Firebase Hosting with the REST API - no Firebase CLI or Node needed.

Uses the active gcloud login: python deploy/hosting_rest.py   (firebase.json stays the source of the config).
"""
import gzip
import hashlib
import json
import subprocess
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE = "sky-task-c4048"
API = "https://firebasehosting.googleapis.com/v1beta1"
TOKEN = subprocess.run("gcloud auth print-access-token", shell=True, capture_output=True, text=True, check=True).stdout.strip()


def call(method, url, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "Content-Type": ctype, "x-goog-user-project": SITE})
    with urllib.request.urlopen(req) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else {}


def files():
    out = {}
    for p in (REPO / "explorer").rglob("*"):
        rel = p.relative_to(REPO / "explorer").as_posix()
        if p.is_file() and not rel.startswith(("dev/", "widget/")) and rel != "README.md" and not p.name.startswith("."):
            out["/" + rel] = p.read_bytes()
    for p in (REPO / "widget").rglob("*"):
        if p.is_file() and p.suffix == ".js":
            out["/widget/" + p.relative_to(REPO / "widget").as_posix()] = p.read_bytes()
    return out


def main():
    fb = json.loads((REPO / "firebase.json").read_text())["hosting"]
    config = {
        "rewrites": [{"glob": r["source"], "run": {"serviceId": r["run"]["serviceId"], "region": r["run"]["region"]}}
                     for r in fb.get("rewrites", []) if "run" in r],
        "headers": [{"glob": h["source"], "headers": {x["key"]: x["value"] for x in h["headers"]}} for h in fb.get("headers", [])],
    }
    v = call("POST", f"{API}/sites/{SITE}/versions", {"config": config})
    name = v["name"]
    gz = {path: gzip.compress(data, mtime=0) for path, data in files().items()}
    hashes = {path: hashlib.sha256(g).hexdigest() for path, g in gz.items()}
    pop = call("POST", f"{API}/{name}:populateFiles", {"files": hashes})
    need = set(pop.get("uploadRequiredHashes", []))
    for path, h in hashes.items():
        if h in need:
            call("POST", f"{pop['uploadUrl']}/{h}", raw=gz[path], ctype="application/octet-stream")
            need.discard(h)
    call("PATCH", f"{API}/{name}?update_mask=status", {"status": "FINALIZED"})
    call("POST", f"{API}/sites/{SITE}/releases?versionName={name}", {})
    print(f"Released {len(hashes)} files: https://{SITE}.web.app")
    for path in sorted(hashes):
        print("  ", path)


if __name__ == "__main__":
    main()
