from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from . import pipeline, search as searchmod, export as exportmod, indexes
from .project import Project, inspect_pdf


def main(argv=None):
    ap = argparse.ArgumentParser("mdkb")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765)
    s = sub.add_parser("inspect"); s.add_argument("pdf")
    s = sub.add_parser("new"); s.add_argument("pdf"); s.add_argument("output"); s.add_argument("--title"); s.add_argument("--edition")
    s = sub.add_parser("convert"); s.add_argument("project"); s.add_argument("--chapters", help="e.g. 1,2,5 (default: all)"); s.add_argument("--force", action="store_true")
    s = sub.add_parser("search"); s.add_argument("project"); s.add_argument("query")
    s = sub.add_parser("export"); s.add_argument("project"); s.add_argument("dest"); s.add_argument("--include-pdf", action="store_true"); s.add_argument("--rag-only", action="store_true")
    s = sub.add_parser("indexes"); s.add_argument("project")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        from .server import serve; serve(a.port); return 0
    if a.cmd == "inspect":
        print(json.dumps(inspect_pdf(a.pdf), indent=2)); return 0
    if a.cmd == "new":
        p = Project.create(a.output, a.pdf, a.title, a.edition)
        print(f"Created {p.root} with {len(p.data['chapters'])} chapters"); return 0
    p = Project(a.project)
    if a.cmd == "convert":
        nums = [int(x) for x in a.chapters.split(",")] if a.chapters else [c.number for c in p.chapters()]
        for n in nums:
            r = pipeline.convert_chapter(p, n, progress_cb=lambda e: print(f"  ch{e['chapter']} {e['stage']} p.{e['page']}", file=sys.stderr), force=a.force)
            print(f"Chapter {n}: {r['status']} {r['counts']}")
    elif a.cmd == "search":
        for r in searchmod.search(p, a.query):
            print(f"[Ch {r['chapter']} p.{r['page']}] {r['heading_path']}: {r['snippet']}")
    elif a.cmd == "export":
        print(exportmod.export_zip(p, a.dest, a.include_pdf, a.rag_only))
    elif a.cmd == "indexes":
        print(indexes.build(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
