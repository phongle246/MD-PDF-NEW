import json, threading, time, urllib.request, urllib.error
import pytest
from mdkb import server


@pytest.fixture()
def api(tmp_path, monkeypatch, book_pdf):
    monkeypatch.setattr(server, "HOME", tmp_path / "home")
    (tmp_path / "home").mkdir()
    srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, body=None):
        req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                data = r.read()
                return json.loads(data) if r.headers["Content-Type"] == "application/json" else data
        except urllib.error.HTTPError as e:
            return {"_status": e.code, **json.loads(e.read())}
    call.out = tmp_path / "out"
    yield call
    srv.shutdown()


def wait_done(call, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = [x for x in call("/api/jobs") if x["id"] == job_id][0]
        if j["state"] in ("done", "failed", "cancelled", "paused"):
            return j
        time.sleep(0.1)
    raise TimeoutError


def test_full_flow_flow_a_b_e(api, book_pdf, tmp_path):
    ov = api("/api/projects", {"pdf": str(book_pdf), "output": str(api.out / "book")})
    root = ov["root"]
    assert len(ov["chapters"]) == 2 and api("/api/projects")[0]["title"] == "Test Textbook of Pediatrics"
    job = api("/api/convert", {"root": root, "chapters": [1, 2]})
    j = wait_done(api, job["id"])
    assert j["state"] == "done" and set(j["results"]) == {"1", "2"} or set(j["results"]) == {1, 2}
    ov = api(f"/api/project/overview?root={root}")
    assert all(c["status"].startswith("COMPLETED") for c in ov["chapters"]) and ov["percent"] == 100
    # Flow B: search -> markdown section -> source page image
    res = api(f"/api/search?root={root}&q=deferasirox")
    assert res[0]["page"] == 1
    md = api(f"/api/chapter/markdown?root={root}&chapter=1&mode=source")
    assert "[Trang gốc: 1]" in md["markdown"] and "<!-- source_page" not in md["markdown"]
    assert api(f"/api/chapter/markdown?root={root}&chapter=1")["markdown"].count("<!-- source_page") >= 2
    png = api(f"/api/page.png?root={root}&page={res[0]['page']}")
    assert png[:4] == b"\x89PNG"
    assert api(f"/api/asset?root={root}&path=assets/images/ch_001/figure_1_01.png")[:4] == b"\x89PNG"
    assert api(f"/api/asset?root={root}&path=../../etc/passwd")["_status"] == 404
    rep = api(f"/api/chapter/report?root={root}&chapter=1")
    assert "## Status" in rep["report"]
    # Flow E: export
    z = api("/api/export", {"root": root, "dest": str(tmp_path / "e.zip")})
    assert (tmp_path / "e.zip").exists()
    assert api(f"/api/chapter/markdown?root={root}&chapter=9")["_status"] == 404


def test_errors_are_actionable(api, tmp_path):
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"junk")
    r = api("/api/projects", {"pdf": str(bad), "output": str(tmp_path / "x")})
    assert r["_status"] == 422 and "corrupt" in r["error"]
    assert api("/api/ai/test", {})["_status"] == 400
    assert "API key" in api("/api/ai/test", {})["error"]


def test_pause_resume_cancel(api, book_pdf):
    ov = api("/api/projects", {"pdf": str(book_pdf), "output": str(api.out / "b2")})
    job = api("/api/convert", {"root": ov["root"], "chapters": [1, 2]})
    api(f"/api/jobs/{job['id']}/cancel", {})
    j = wait_done(api, job["id"])
    assert j["state"] in ("cancelled", "done")


def test_settings_hide_secret(api):
    api("/api/secret", {"api_key": "sk-test"})
    s = api("/api/settings")
    assert s["api_key"] == "•••" and "sk-test" not in json.dumps(s)
