"""Background conversion jobs with pause / cancel / resume."""
from __future__ import annotations
import threading, time, uuid, traceback
from . import pipeline
from .project import Project


class Job:
    def __init__(self, root: str, chapters: list[int], force: bool = False):
        self.id = uuid.uuid4().hex[:8]
        self.root, self.chapters, self.force = root, chapters, force
        self.state = "queued"          # queued running paused cancelled done failed
        self.event: dict = {}
        self.errors: list[dict] = []
        self.results: dict = {}
        self._pause = threading.Event()
        self._cancel = threading.Event()
        self.thread: threading.Thread | None = None
        self.current: int | None = None

    def should_stop(self):
        return self._pause.is_set() or self._cancel.is_set()

    def run(self):
        self.state = "running"
        try:
            project = Project(self.root)
            for n in self.chapters:
                if self._cancel.is_set():
                    break
                self.current = n
                try:
                    res = pipeline.convert_chapter(project, n, progress_cb=lambda ev: setattr(self, "event", ev), should_stop=self.should_stop,
                                                   force=self.force, ocr_enabled=project.data.get("settings", {}).get("ocr", True))
                    self.results[n] = {"status": res["status"], "counts": res["counts"]}
                except pipeline.Interrupted:
                    self.state = "cancelled" if self._cancel.is_set() else "paused"
                    return
                except Exception as e:
                    self.errors.append({"chapter": n, "error": str(e), "trace": traceback.format_exc(limit=3)})
                    self.results[n] = {"status": "FAILED"}
            self.state = "cancelled" if self._cancel.is_set() else "done"
        except Exception as e:
            self.errors.append({"error": str(e)})
            self.state = "failed"
        finally:
            self.current = None

    def start(self):
        self._pause.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def pause(self): self._pause.set()
    def cancel(self): self._cancel.set(); self._pause.set()

    def resume(self):
        if self.state == "paused":
            remaining = [n for n in self.chapters if self.results.get(n, {}).get("status") in (None, "PROCESSING")]
            self.chapters = remaining or self.chapters
            self.start()

    def to_dict(self):
        return {"id": self.id, "root": self.root, "state": self.state, "chapters": self.chapters, "current": self.current,
                "event": self.event, "errors": self.errors, "results": self.results}


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}

    def submit(self, root, chapters, force=False) -> Job:
        job = Job(root, chapters, force)
        self.jobs[job.id] = job
        job.start()
        return job

    def live_chapters(self, root: str) -> set[int]:
        s = set()
        for j in self.jobs.values():
            if j.root == root and j.state == "running" and j.current is not None:
                s.add(j.current)
        return s
