"""Minimal Google Cloud REST client (stdlib only): Firestore documents and Cloud Storage objects.

No google-cloud-* packages, so the same code runs on Cloud Run (token from the metadata server), on a developer
machine and in the uploader on Antonio's PC (token from `gcloud auth print-access-token`).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"


class Token:
    """Access token, refreshed before it expires. Cloud Run: metadata server. Elsewhere: the gcloud CLI."""

    def __init__(self):
        self._lock = threading.Lock()
        self._tok, self._exp = None, 0.0

    def get(self) -> str:
        with self._lock:
            if self._tok and time.time() < self._exp - 120:
                return self._tok
            if os.environ.get("K_SERVICE"):
                req = urllib.request.Request(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    d = json.load(r)
                self._tok, self._exp = d["access_token"], time.time() + int(d.get("expires_in", 300))
            else:
                out = subprocess.run("gcloud auth print-access-token", shell=True, capture_output=True,
                                     text=True, timeout=60)
                if out.returncode != 0:
                    raise RuntimeError("gcloud auth print-access-token failed: " + out.stderr.strip()[:300])
                self._tok, self._exp = out.stdout.strip(), time.time() + 1800
            return self._tok


class GcpError(RuntimeError):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status


class Client:
    def __init__(self, project: str, bucket: str | None = None, token: Token | None = None, timeout: float = 30):
        self.project, self.bucket, self.token, self.timeout = project, bucket, token or Token(), timeout
        self.docs = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"

    # ---------- HTTP ----------
    def _call(self, method, url, body=None, raw=None, ctype="application/json", want_json=True, retries=3):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        for attempt in range(retries):
            headers = {"Authorization": "Bearer " + self.token.get(), "Content-Type": ctype}
            if not os.environ.get("K_SERVICE"):  # user credentials need a quota project; the service account doesn't
                headers["x-goog-user-project"] = self.project
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = r.read()
                return (json.loads(payload) if payload else {}) if want_json else payload
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors="replace")
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise GcpError(e.code, txt) from None
            except urllib.error.URLError:
                if attempt < retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise

    # ---------- Firestore ----------
    def commit(self, writes: list[dict]):
        """writes: [{"set": (collection, doc_id, dict)}] or [{"delete": (collection, doc_id)}]; batches of 400."""
        out = []
        for i in range(0, len(writes), 400):
            ws = []
            for w in writes[i:i + 400]:
                if "set" in w:
                    col, did, data = w["set"]
                    ws.append({"update": {"name": self._name(col, did), "fields": encode_fields(data)}})
                elif "patch" in w:  # update only the given fields
                    col, did, data = w["patch"]
                    ws.append({"update": {"name": self._name(col, did), "fields": encode_fields(data)},
                               "updateMask": {"fieldPaths": list(data)}})
                else:
                    col, did = w["delete"]
                    ws.append({"delete": self._name(col, did)})
            out.append(self._call("POST", f"{self.docs}:commit", {"writes": ws}))
        return out

    def add(self, collection: str, rows: list[dict]):
        """Insert documents with generated ids."""
        import secrets
        return self.commit([{"set": (collection, secrets.token_hex(10), r)} for r in rows])

    def get(self, collection: str, doc_id: str) -> dict | None:
        try:
            d = self._call("GET", f"{self.docs}/{collection}/{urllib.parse.quote(doc_id, safe='')}")
        except GcpError as e:
            if e.status == 404:
                return None
            raise
        return decode_fields(d.get("fields", {}))

    def query(self, collection: str, where: dict | None = None, select: list[str] | None = None) -> list[dict]:
        """Equality filters only. Returns decoded documents with '_id'."""
        sq: dict = {"from": [{"collectionId": collection}]}
        if where:
            fs = [{"fieldFilter": {"field": {"fieldPath": k}, "op": "EQUAL", "value": encode_value(v)}}
                  for k, v in where.items()]
            sq["where"] = fs[0] if len(fs) == 1 else {"compositeFilter": {"op": "AND", "filters": fs}}
        if select:
            sq["select"] = {"fields": [{"fieldPath": f} for f in select]}
        res = self._call("POST", f"{self.docs}:runQuery", {"structuredQuery": sq})
        out = []
        for row in res:
            d = row.get("document")
            if d:
                out.append({**decode_fields(d.get("fields", {})), "_id": d["name"].rsplit("/", 1)[1]})
        return out

    def count(self, collection: str, where: dict | None = None) -> int:
        sq: dict = {"from": [{"collectionId": collection}]}
        if where:
            fs = [{"fieldFilter": {"field": {"fieldPath": k}, "op": "EQUAL", "value": encode_value(v)}}
                  for k, v in where.items()]
            sq["where"] = fs[0] if len(fs) == 1 else {"compositeFilter": {"op": "AND", "filters": fs}}
        res = self._call("POST", f"{self.docs}:runAggregationQuery", {"structuredAggregationQuery": {
            "structuredQuery": sq, "aggregations": [{"alias": "n", "count": {}}]}})
        for row in res:
            r = row.get("result")
            if r:
                return int(r["aggregateFields"]["n"]["integerValue"])
        return 0

    def _name(self, col, did):
        return f"projects/{self.project}/databases/(default)/documents/{col}/{did}"

    # ---------- Cloud Storage ----------
    def put_object(self, name: str, data: bytes, ctype="image/png"):
        url = (f"https://storage.googleapis.com/upload/storage/v1/b/{self.bucket}/o?uploadType=media&name="
               + urllib.parse.quote(name, safe=""))
        return self._call("POST", url, raw=data, ctype=ctype)

    def get_object(self, name: str) -> bytes:
        url = f"https://storage.googleapis.com/storage/v1/b/{self.bucket}/o/{urllib.parse.quote(name, safe='')}?alt=media"
        return self._call("GET", url, want_json=False)


# ---------- Firestore value codec ----------
def encode_value(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [encode_value(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": encode_fields(v)}}
    raise TypeError(f"can't store {type(v).__name__} in Firestore")


def encode_fields(d: dict) -> dict:
    return {k: encode_value(v) for k, v in d.items()}


def decode_value(v):
    if "nullValue" in v:
        return None
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "stringValue" in v:
        return v["stringValue"]
    if "timestampValue" in v:
        return v["timestampValue"]
    if "arrayValue" in v:
        return [decode_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return decode_fields(v["mapValue"].get("fields", {}))
    return None


def decode_fields(f: dict) -> dict:
    return {k: decode_value(v) for k, v in f.items()}
