"""Local HTTP API (127.0.0.1 only). The desktop UI talks to this sidecar."""
from __future__ import annotations
import json, mimetypes, os, re, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import pymupdf
from . import APP_VERSION, search as searchmod, export as exportmod, indexes, pipeline, ai, ocr
from .jobs import JobManager
from .project import Project, inspect_pdf, PDFError
from .util import read_json, write_json, atomic_write

HOME = Path(os.environ.get("MDKB_HOME", Path.home() / ".mdkb"))
JOBS = JobManager()


def registry() -> list[dict]:
    return read_json(HOME / "projects.json", [])


def register(root: str, title: str):
    reg = [r for r in registry() if r["root"] != root]
    reg.insert(0, {"root": root, "title": title})
    write_json(HOME / "projects.json", reg)


def settings() -> dict:
    return read_json(HOME / "settings.json", {"provider": "openai", "model": "", "ai_enabled": False, "ocr": True, "theme": "system",
                                                 "markdown_mode": "portable", "output_dir": str(Path.home() / "MDKB")})


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg); self.code = code


def _project(root: str) -> Project:
    if not root or not (Path(root) / ".mdkb" / "project.json").exists():
        raise ApiError(f"Not a project folder: {root}", 404)
    return Project(root)


def source_mode(md: str, mode: str) -> str:
    if mode == "source":
        return re.sub(r"<!--\s*source_page:\s*(\d+)\s*-->", r"> **[Trang gốc: \1]**", md)
    if mode == "obsidian":
        md = re.sub(r"^> \*\*(KEY POINTS?|CLINICAL PEARLS?|IMPORTANT|SUMMARY|CONTROVERSIES|WARNING|NOTE)\*\*", lambda m: f"> [!{m.group(1).lower().split()[0]}] {m.group(1)}", md, flags=re.M)
    return md


class Handler(BaseHTTPRequestHandler):
    server_version = "mdkb/" + APP_VERSION

    def log_message(self, *a): pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self): self._send(204, b"")

    def _handle(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                body = json.loads(self.rfile.read(n) or b"{}")
        try:
            out = route(method, u.path, q, body)
            if isinstance(out, tuple):
                self._send(200, out[0], out[1])
            else:
                self._send(200, out)
        except ApiError as e:
            self._send(e.code, {"error": str(e)})
        except PDFError as e:
            self._send(422, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")


def route(method, path, q, b):
    root = q.get("root") or b.get("root")
    if path == "/api/health":
        return {"ok": True, "version": APP_VERSION, "ocr_available": ocr.available()}
    if path == "/api/settings":
        if method == "POST":
            s = {**settings(), **b}
            write_json(HOME / "settings.json", s)
        s = settings()
        return {**s, "api_key": "•••" if (HOME / "secret.json").exists() else ""}
    if path == "/api/secret" and method == "POST":
        write_json(HOME / "secret.json", {"api_key": b.get("api_key", "")})
        try: os.chmod(HOME / "secret.json", 0o600)
        except OSError: pass
        return {"ok": True}
    if path == "/api/ai/preview":
        p = _provider()
        return p.describe_payload(b.get("prompt", "Reply with the single word: ok"))
    if path == "/api/ai/test":
        return {"reply": _provider().test_connection()}
    if path == "/api/projects":
        if method == "GET":
            out = []
            for r in registry():
                try:
                    p = Project(r["root"]); ov = p.overview(JOBS.live_chapters(r["root"]))
                    out.append({"root": r["root"], "title": ov["title"], "edition": ov["edition"], "percent": ov["percent"],
                                "chapters": len(ov["chapters"]), "last_opened": ov["last_opened"], "pages": ov["pages"]})
                except Exception:
                    out.append({"root": r["root"], "title": r["title"], "missing": True})
            return out
        pdf = b.get("pdf")
        if not pdf or not Path(pdf).exists():
            raise ApiError("PDF not found")
        out_root = b.get("output") or str(Path(settings()["output_dir"]) / re.sub(r"[^\w\- ]", "", b.get("title") or Path(pdf).stem).strip().replace(" ", "_"))
        p = Project.create(out_root, pdf, b.get("title"), b.get("edition"))
        register(str(p.root), p.title)
        return p.overview()
    if path == "/api/inspect":
        pdf = b.get("pdf") or q.get("pdf")
        if not pdf or not Path(pdf).exists():
            raise ApiError("PDF not found")
        return inspect_pdf(pdf)
    if path == "/api/project/open":
        p = _project(root)
        p.data["last_opened"] = __import__("mdkb.project", fromlist=["_now"])._now(); p.save()
        register(str(p.root), p.title)
        return p.overview(JOBS.live_chapters(root))
    if path == "/api/project/overview":
        return _project(root).overview(JOBS.live_chapters(root))
    if path == "/api/project/chapters" and method == "POST":
        p = _project(root); p.set_chapters(b["chapters"]); p.write_book_files()
        return p.overview()
    if path == "/api/convert" and method == "POST":
        p = _project(root)
        nums = b.get("chapters") or [c.number for c in p.chapters()]
        if b.get("only_pending"):
            nums = [n for n in nums if p.chapter_status(n) not in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "REVIEW_REQUIRED")]
        return JOBS.submit(str(p.root), nums, b.get("force", False)).to_dict()
    if path == "/api/jobs":
        return [j.to_dict() for j in JOBS.jobs.values()]
    m = re.fullmatch(r"/api/jobs/(\w+)/(pause|cancel|resume)", path)
    if m:
        j = JOBS.jobs.get(m.group(1))
        if not j: raise ApiError("No such job", 404)
        getattr(j, m.group(2))()
        return j.to_dict()
    if path == "/api/chapter/markdown":
        p = _project(root); pr = p.progress(int(q["chapter"]))
        if not pr.get("markdown_file"): raise ApiError("Chapter not converted", 404)
        md = (p.root / pr["markdown_file"]).read_text("utf-8")
        return {"markdown": source_mode(md, q.get("mode", "normal")), "file": pr["markdown_file"]}
    if path == "/api/chapter/report":
        p = _project(root); n = int(q["chapter"]); pr = p.progress(n)
        rp = p.root / "reports" / f"{p.ch_key(n)}_conversion_report.md"
        issues = read_json(p.root / "reports" / f"{p.ch_key(n)}_issues.json", [])
        return {"report": rp.read_text("utf-8") if rp.exists() else "", "issues": issues, "status": pr["status"]}
    if path == "/api/chapter/sourcemap":
        p = _project(root)
        return read_json(p.root / "source_maps" / f"{p.ch_key(int(q['chapter']))}.json", {})
    if path == "/api/page.png":
        p = _project(root)
        doc = pymupdf.open(str(p.pdf_path))
        try:
            pg = doc[int(q["page"]) - 1]
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(float(q.get("zoom", 1.6)), float(q.get("zoom", 1.6))), alpha=False)
            return pix.tobytes("png"), "image/png"
        finally:
            doc.close()
    if path == "/api/pdf":
        p = _project(root)
        return p.pdf_path.read_bytes(), "application/pdf"
    if path == "/api/asset":
        p = _project(root); f = (p.root / q["path"]).resolve()
        if p.root.resolve() not in f.parents or not f.exists(): raise ApiError("Not found", 404)
        return f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    if path == "/api/search":
        p = _project(root)
        return searchmod.search(p, q.get("q", ""), int(q["chapter"]) if q.get("chapter") else None, q.get("type"), q.get("specialty"))
    if path == "/api/search/reindex" and method == "POST":
        return {"indexed": searchmod.reindex_from_source_maps(_project(root))}
    if path == "/api/indexes/build" and method == "POST":
        return indexes.build(_project(root))
    if path == "/api/xrefs/resolve" and method == "POST":
        return pipeline.resolve_all_xrefs(_project(root))
    if path == "/api/export" and method == "POST":
        p = _project(root)
        dest = b.get("dest") or str(p.root.parent / (p.root.name + ".zip"))
        exportmod.export_zip(p, dest, b.get("include_pdf", False), b.get("rag_only", False), b.get("chapter"))
        return {"zip": dest}
    raise ApiError(f"Unknown endpoint {path}", 404)


def _provider():
    s = settings()
    key = (read_json(HOME / "secret.json", {}) or {}).get("api_key", "")
    try:
        return ai.Provider(s.get("provider", "openai"), key, s.get("model") or None)
    except ai.AIError as e:
        raise ApiError(str(e))


def serve(port: int = 8765):
    HOME.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mdkb engine listening on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()
