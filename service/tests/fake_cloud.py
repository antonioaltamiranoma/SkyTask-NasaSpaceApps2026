"""In-memory stand-in for app.gcp.Client (Firestore + bucket), for tests."""
import copy


class FakeCloud:
    def __init__(self):
        self.cols: dict[str, dict[str, dict]] = {}
        self.objects: dict[str, bytes] = {}
        self.fail = 0          # fail the next N calls
        self.calls = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise RuntimeError("simulated Firestore outage")

    def commit(self, writes):
        self._maybe_fail()
        for w in writes:
            if "set" in w:
                col, did, data = w["set"]
                self.cols.setdefault(col, {})[did] = copy.deepcopy(data)
            elif "patch" in w:
                col, did, data = w["patch"]
                self.cols.setdefault(col, {}).setdefault(did, {}).update(copy.deepcopy(data))
            else:
                col, did = w["delete"]
                self.cols.get(col, {}).pop(did, None)

    def add(self, col, rows):
        import secrets
        self.commit([{"set": (col, secrets.token_hex(6), r)} for r in rows])

    def get(self, col, did):
        self._maybe_fail()
        d = self.cols.get(col, {}).get(did)
        return copy.deepcopy(d) if d is not None else None

    def query(self, col, where=None, select=None):
        self._maybe_fail()
        out = []
        for did, d in self.cols.get(col, {}).items():
            if all(d.get(k) == v for k, v in (where or {}).items()):
                row = {k: d.get(k) for k in select} if select else copy.deepcopy(d)
                out.append({**row, "_id": did})
        return out

    def count(self, col, where=None):
        return len(self.query(col, where))

    def put_object(self, name, data, ctype="image/png"):
        self._maybe_fail()
        self.objects[name] = data

    def get_object(self, name):
        self._maybe_fail()
        return self.objects[name]
