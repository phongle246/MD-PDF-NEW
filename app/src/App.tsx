import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, pickPdf, ENGINE, Overview, ProjectCard, Job, Chapter, SearchHit, Issue } from "./api";
import { renderMarkdown } from "./md";

type View = "projects" | "overview" | "convert" | "search" | "review" | "export" | "settings";
const NAV: { id: View; label: string; needsProject: boolean }[] = [
  { id: "projects", label: "Projects", needsProject: false }, { id: "overview", label: "Book overview", needsProject: true },
  { id: "convert", label: "Convert", needsProject: true }, { id: "search", label: "Search", needsProject: true },
  { id: "review", label: "Review", needsProject: true }, { id: "export", label: "Export", needsProject: true },
  { id: "settings", label: "Settings", needsProject: false },
];
const STATUS_LABEL: Record<string, string> = { NOT_STARTED: "Not started", PROCESSING: "Processing", PARTIAL: "Partial (interrupted)", COMPLETED: "Completed", COMPLETED_WITH_WARNINGS: "Completed · warnings", REVIEW_REQUIRED: "Review required", FAILED: "Failed" };

export default function App() {
  const [view, setView] = useState<View>("projects");
  const [ov, setOv] = useState<Overview | null>(null);
  const [error, setError] = useState<string>("");
  const [review, setReview] = useState<{ chapter: number; page?: number } | null>(null);
  const [engineOk, setEngineOk] = useState<boolean | null>(null);

  useEffect(() => { api.health().then(() => setEngineOk(true)).catch(() => setEngineOk(false)); }, []);
  useEffect(() => {
    const t = (localStorage.getItem("theme") || "system");
    document.documentElement.dataset.theme = t;
  }, []);

  const refresh = useCallback(async () => { if (ov) setOv(await api.overview(ov.root)); }, [ov]);
  const guard = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    try { setError(""); return await fn(); } catch (e: any) { setError(e.message || String(e)); }
  }, []);
  const openReview = (chapter: number, page?: number) => { setReview({ chapter, page }); setView("review"); };

  return (
    <div className="app">
      <nav className="sidebar">
        <div className="brand">MD-PDF<small>Textbook → Markdown KB</small></div>
        {NAV.map((n) => (
          <button key={n.id} className={view === n.id ? "active" : ""} disabled={n.needsProject && !ov} onClick={() => setView(n.id)}>{n.label}</button>
        ))}
        {ov && (
          <div className="sidebar-chapters">
            <div className="muted small">{ov.title}</div>
            {ov.chapters.map((c) => (
              <button key={c.number} className="chrow" title={STATUS_LABEL[c.status]} onClick={() => c.markdown_file ? openReview(c.number) : setView("overview")}>
                <span className={`dot s-${c.status}`} /> <span className="chnum">{c.number}</span> <span className="chtitle">{c.title}</span>
              </button>
            ))}
          </div>
        )}
      </nav>
      <main>
        {engineOk === false && <div className="banner err">Local engine not running. Start it with <code>python -m mdkb serve</code> in <code>engine/</code> (the desktop app does this automatically). <button onClick={() => api.health().then(() => setEngineOk(true)).catch(() => {})}>Retry</button></div>}
        {error && <div className="banner err" role="alert">{error} <button onClick={() => setError("")}>Dismiss</button></div>}
        {view === "projects" && <Projects guard={guard} onOpen={async (root) => { const o = await guard(() => api.open(root)); if (o) { setOv(o); setView("overview"); } }} onCreated={(o) => { setOv(o); setView("overview"); }} />}
        {view === "overview" && ov && <OverviewView ov={ov} refresh={refresh} guard={guard} go={setView} openReview={openReview} />}
        {view === "convert" && ov && <ConvertView ov={ov} refresh={refresh} guard={guard} />}
        {view === "search" && ov && <SearchView ov={ov} guard={guard} openReview={openReview} />}
        {view === "review" && ov && <ReviewView ov={ov} guard={guard} target={review} />}
        {view === "export" && ov && <ExportView ov={ov} guard={guard} refresh={refresh} />}
        {view === "settings" && <SettingsView guard={guard} />}
      </main>
    </div>
  );
}

type Guard = <T>(fn: () => Promise<T>) => Promise<T | undefined>;

function Projects({ guard, onOpen, onCreated }: { guard: Guard; onOpen: (r: string) => void; onCreated: (o: Overview) => void }) {
  const [list, setList] = useState<ProjectCard[]>([]);
  const [wizard, setWizard] = useState<{ pdf: string; info: Awaited<ReturnType<typeof api.inspect>> } | null>(null);
  const [title, setTitle] = useState(""); const [edition, setEdition] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { guard(api.projects).then((p) => p && setList(p)); }, [guard]);
  const start = async () => {
    const pdf = await pickPdf(); if (!pdf) return;
    setBusy(true);
    const info = await guard(() => api.inspect(pdf));
    setBusy(false);
    if (info) { setWizard({ pdf, info }); setTitle(info.title); setEdition(info.edition); }
  };
  const create = async () => {
    if (!wizard) return; setBusy(true);
    const o = await guard(() => api.create(wizard.pdf, title, edition)); setBusy(false);
    if (o) onCreated(o);
  };
  return (
    <section>
      <header className="row"><h1>Projects</h1><button className="primary" onClick={start} disabled={busy}>{busy ? "Inspecting…" : "New book project"}</button></header>
      {wizard && (
        <div className="card">
          <h2>Book overview</h2>
          <p className="muted">{wizard.pdf} — {wizard.info.pages} pages · outline {wizard.info.has_outline ? "found" : "not found"} · native text {Math.round(wizard.info.native_text_ratio * 100)}%</p>
          <div className="form">
            <label>Title <input value={title} onChange={(e) => setTitle(e.target.value)} /></label>
            <label>Edition <input value={edition} onChange={(e) => setEdition(e.target.value)} /></label>
          </div>
          <ChapterTable chapters={wizard.info.chapters} />
          <div className="row"><button className="primary" onClick={create} disabled={busy}>Create project</button> <button onClick={() => setWizard(null)}>Cancel</button></div>
          <p className="small muted">The original PDF is never modified. Low-confidence chapters are flagged and can be corrected after creation.</p>
        </div>
      )}
      <div className="grid">
        {list.length === 0 && !wizard && <p className="muted">No projects yet. Choose “New book project” and select a textbook PDF.</p>}
        {list.map((p) => (
          <button key={p.root} className="card project" onClick={() => onOpen(p.root)} disabled={p.missing}>
            <h3>{p.title}</h3>
            <p className="muted small">{p.missing ? "Folder missing" : `${p.edition ? "Edition " + p.edition + " · " : ""}${p.chapters} chapters · ${p.pages} pages`}</p>
            {!p.missing && <><div className="bar"><div style={{ width: `${p.percent}%` }} /></div><p className="small">{p.percent}% converted{p.last_opened ? ` · opened ${p.last_opened.slice(0, 10)}` : ""}</p></>}
          </button>
        ))}
      </div>
    </section>
  );
}

function ChapterTable({ chapters }: { chapters: Pick<Chapter, "number" | "title" | "start_page" | "end_page" | "confidence" | "source">[] }) {
  return (
    <table className="tbl"><thead><tr><th>#</th><th>Title</th><th>Pages</th><th>Detected by</th><th>Confidence</th></tr></thead>
      <tbody>{chapters.map((c) => (
        <tr key={c.number + "-" + c.start_page} className={c.confidence < 0.6 ? "warn" : ""}>
          <td>{c.number}</td><td>{c.title}</td><td>{c.start_page}–{c.end_page}</td><td>{c.source}</td>
          <td>{Math.round(c.confidence * 100)}%{c.confidence < 0.6 && " ⚠ review"}</td>
        </tr>))}</tbody></table>
  );
}

function OverviewView({ ov, refresh, guard, go, openReview }: { ov: Overview; refresh: () => void; guard: Guard; go: (v: View) => void; openReview: (c: number, p?: number) => void }) {
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState<Chapter[]>(ov.chapters);
  useEffect(() => setDraft(ov.chapters), [ov]);
  const start = async (nums?: number[]) => { const j = await guard(() => api.convert(ov.root, nums)); if (j) go("convert"); };
  const toggle = (n: number) => setSel((s) => { const x = new Set(s); x.has(n) ? x.delete(n) : x.add(n); return x; });
  const save = async () => { await guard(() => api.setChapters(ov.root, draft)); setEdit(false); refresh(); };
  const upd = (i: number, k: keyof Chapter, v: string) => setDraft((d) => d.map((c, j) => j === i ? { ...c, [k]: k === "title" ? v : Number(v) } : c));
  return (
    <section>
      <header className="row"><div><h1>{ov.title}</h1><p className="muted">{ov.edition && `Edition ${ov.edition} · `}{ov.pages} pages · {ov.source_name} · SHA-256 <code>{ov.source_sha256.slice(0, 12)}…</code> {!ov.source_ok && <b className="err-text">source PDF missing or changed!</b>}</p></div>
        <div className="bar big"><div style={{ width: `${ov.percent}%` }} /></div></header>
      <div className="row wrap">
        <button className="primary" disabled={sel.size === 0} onClick={() => start([...sel].sort((a, b) => a - b))}>Convert selected ({sel.size})</button>
        <button onClick={() => start()}>Convert all</button>
        <button onClick={() => guard(() => api.convert(ov.root, undefined, false, true)).then(() => go("convert"))}>Convert pending only</button>
        <button onClick={() => (edit ? save() : setEdit(true))}>{edit ? "Save chapter boundaries" : "Edit chapters"}</button>
        {edit && <button onClick={() => { setDraft(ov.chapters); setEdit(false); }}>Cancel</button>}
        <button onClick={() => guard(() => api.resolveXrefs(ov.root)).then((r) => r && alert(`Linked ${r.linked} cross-references, ${r.remaining} unresolved`))}>Resolve cross-references</button>
      </div>
      <table className="tbl">
        <thead><tr><th></th><th>#</th><th>Title</th><th>Pages</th><th>Status</th><th>Progress</th><th>Issues</th><th></th></tr></thead>
        <tbody>{(edit ? draft : ov.chapters).map((c, i) => (
          <tr key={c.number} className={c.needs_review ? "warn" : ""}>
            <td><input type="checkbox" checked={sel.has(c.number)} onChange={() => toggle(c.number)} aria-label={`select chapter ${c.number}`} /></td>
            <td>{c.number}</td>
            <td>{edit ? <input value={c.title} onChange={(e) => upd(i, "title", e.target.value)} /> : <>{c.title}{c.needs_review && <span className="chip warn" title={c.notes?.join("; ")}>check boundaries</span>}</>}</td>
            <td>{edit ? <><input className="num" value={c.start_page} onChange={(e) => upd(i, "start_page", e.target.value)} />–<input className="num" value={c.end_page} onChange={(e) => upd(i, "end_page", e.target.value)} /></> : `${c.start_page}–${c.end_page}`}</td>
            <td><span className={`chip s-${c.status}`}>{STATUS_LABEL[c.status] || c.status}</span></td>
            <td className="small">{c.pages_done}/{c.pages_total}</td>
            <td className="small">{Object.entries(c.issues || {}).map(([k, v]) => `${k[0]}${v}`).join(" ") || "—"}</td>
            <td><button onClick={() => start([c.number])}>{c.status === "NOT_STARTED" ? "Convert" : c.status === "PARTIAL" ? "Resume" : "Re-run"}</button> {c.markdown_file && <button onClick={() => openReview(c.number)}>Review</button>}</td>
          </tr>))}</tbody>
      </table>
    </section>
  );
}

function ConvertView({ ov, refresh, guard }: { ov: Overview; refresh: () => void; guard: Guard }) {
  const [jobs, setJobs] = useState<Job[]>([]);
  useEffect(() => {
    let alive = true;
    const tick = async () => { try { const j = await api.jobs(); if (alive) setJobs(j.filter((x) => x.root === ov.root).reverse()); } catch {} };
    tick(); const t = setInterval(() => { tick(); refresh(); }, 1000);
    return () => { alive = false; clearInterval(t); };
  }, [ov.root]); // eslint-disable-line
  const act = (id: string, a: "pause" | "cancel" | "resume") => guard(() => api.jobAction(id, a));
  const stages = ["Parsing pages", "Reading layout", "Extracting text", "Building Markdown", "Running QA", "Indexing"];
  return (
    <section>
      <h1>Convert</h1>
      {jobs.length === 0 && <p className="muted">No conversion running. Start one from Book overview. Interrupted chapters resume from the last saved page.</p>}
      {jobs.map((j) => {
        const ev = j.event || {}; const pct = ev.total ? Math.round(100 * (ev.done || 0) / ev.total) : 0;
        const ch = ov.chapters.find((c) => c.number === (j.current ?? ev.chapter));
        return (
          <div className="card" key={j.id}>
            <div className="row"><h3>Job {j.id} — {j.state}</h3>
              <div>{j.state === "running" && <button onClick={() => act(j.id, "pause")}>Pause</button>} {j.state === "paused" && <button className="primary" onClick={() => act(j.id, "resume")}>Resume</button>} {(j.state === "running" || j.state === "paused") && <button onClick={() => act(j.id, "cancel")}>Cancel</button>}</div></div>
            <p>Chapter <b>{ev.chapter ?? "—"}</b>{ch ? ` — ${ch.title}` : ""} · page <b>{ev.page ?? "—"}</b> · stage <b>{ev.stage ?? "—"}</b></p>
            <div className="bar big"><div style={{ width: `${pct}%` }} /></div>
            <ol className="stages">{stages.map((s) => <li key={s} className={ev.stage === s ? "on" : ""}>{s}</li>)}</ol>
            <p className="small muted">Chapters: {j.chapters.join(", ")} · done: {Object.entries(j.results).map(([k, v]) => `${k}:${STATUS_LABEL[v.status] || v.status}`).join(", ") || "none"}</p>
            {j.errors.map((e, i) => <p key={i} className="err-text small">Chapter {e.chapter ?? "?"}: {e.error}</p>)}
          </div>
        );
      })}
    </section>
  );
}

function SearchView({ ov, guard, openReview }: { ov: Overview; guard: Guard; openReview: (c: number, p?: number) => void }) {
  const [q, setQ] = useState(""); const [chapter, setChapter] = useState<string>(""); const [type, setType] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [src, setSrc] = useState<SearchHit | null>(null);
  const run = async () => { const r = await guard(() => api.search(ov.root, q, chapter ? Number(chapter) : undefined, type || undefined)); if (r) setHits(r); };
  const conv = ov.chapters.filter((c) => c.markdown_file);
  return (
    <section>
      <h1>Search</h1>
      <form className="row wrap" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <input className="grow" autoFocus placeholder="Search the whole book (converted chapters)…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={chapter} onChange={(e) => setChapter(e.target.value)}><option value="">All chapters</option>{conv.map((c) => <option key={c.number} value={c.number}>{c.number}. {c.title}</option>)}</select>
        <select value={type} onChange={(e) => setType(e.target.value)}><option value="">All content</option>{["paragraph", "list", "table", "heading", "caption", "callout", "reference"].map((t) => <option key={t}>{t}</option>)}</select>
        <button className="primary">Search</button>
      </form>
      {conv.length === 0 && <p className="muted">Nothing is searchable yet — convert a chapter first.</p>}
      {hits && hits.length === 0 && <p className="muted">No results.</p>}
      {hits?.map((h) => (
        <div className="card hit" key={h.block_id + h.page}>
          <p className="muted small">Ch {h.chapter} · {h.chapter_title} {h.heading_path && <>› {h.heading_path}</>} · <b>p.{h.page}</b> · {h.type}</p>
          <p dangerouslySetInnerHTML={{ __html: h.snippet.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/\[\[/g, "<mark>").replace(/\]\]/g, "</mark>") }} />
          <div className="row"><button onClick={() => openReview(h.chapter, h.page)}>Open section</button> <button onClick={() => setSrc(h)}>Open source page</button> <button onClick={() => openReview(h.chapter, h.page)}>Side-by-side</button></div>
        </div>
      ))}
      {src && <div className="modal" onClick={() => setSrc(null)}><div className="modal-body" onClick={(e) => e.stopPropagation()}><div className="row"><b>Source PDF — page {src.page}</b><button onClick={() => setSrc(null)}>Close</button></div><img src={api.pageUrl(ov.root, src.page, 1.8)} alt={`PDF page ${src.page}`} /></div></div>}
    </section>
  );
}

function ReviewView({ ov, guard, target }: { ov: Overview; guard: Guard; target: { chapter: number; page?: number } | null }) {
  const conv = ov.chapters.filter((c) => c.markdown_file);
  const [chapter, setChapter] = useState<number>(target?.chapter ?? conv[0]?.number ?? 0);
  const [page, setPage] = useState<number>(target?.page ?? conv.find((c) => c.number === chapter)?.start_page ?? 1);
  const [html, setHtml] = useState(""); const [raw, setRaw] = useState(""); const [issues, setIssues] = useState<Issue[]>([]);
  const [mode, setMode] = useState<"rendered" | "source" | "markdown">("rendered");
  const [sync, setSync] = useState(true);
  const mdRef = useRef<HTMLDivElement>(null);
  const ch = ov.chapters.find((c) => c.number === chapter);

  useEffect(() => { if (target) { setChapter(target.chapter); if (target.page) setPage(target.page); } }, [target]);
  useEffect(() => {
    if (!chapter) return;
    guard(async () => {
      const [m, r] = await Promise.all([api.markdown(ov.root, chapter, mode === "source" ? "source" : "normal"), api.report(ov.root, chapter)]);
      setRaw(m.markdown); setHtml(renderMarkdown(m.markdown, ov.root, m.file)); setIssues(r.issues);
    });
  }, [chapter, mode, ov.root]); // eslint-disable-line
  // sync Markdown pane to the PDF page: scroll to the matching page marker
  useEffect(() => {
    if (!sync || !mdRef.current) return;
    const el = mdRef.current.querySelector(`.pagemark[data-page="${page}"]`) as HTMLElement | null;
    if (el) el.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [page, html, sync]);
  const onMdClick = (e: React.MouseEvent) => { const t = (e.target as HTMLElement).closest(".pagemark") as HTMLElement | null; if (t) setPage(Number(t.dataset.page)); };
  if (!conv.length) return <section><h1>Review</h1><p className="muted">Convert a chapter to review it against the source PDF.</p></section>;
  const pmin = ch?.start_page ?? 1, pmax = ch?.end_page ?? ov.pages;
  const rendered = useMemo(() => html, [html]);
  return (
    <section className="review">
      <header className="row wrap">
        <select value={chapter} onChange={(e) => { const n = Number(e.target.value); setChapter(n); setPage(ov.chapters.find((c) => c.number === n)!.start_page); }}>{conv.map((c) => <option key={c.number} value={c.number}>{c.number}. {c.title}</option>)}</select>
        <button onClick={() => setPage((p) => Math.max(pmin, p - 1))}>◀</button><span>Page <input className="num" value={page} onChange={(e) => setPage(Math.min(pmax, Math.max(pmin, Number(e.target.value) || pmin)))} /> / {pmax}</span><button onClick={() => setPage((p) => Math.min(pmax, p + 1))}>▶</button>
        <select value={mode} onChange={(e) => setMode(e.target.value as any)}><option value="rendered">Rendered</option><option value="source">Source mode (page labels)</option><option value="markdown">Raw Markdown</option></select>
        <label className="small"><input type="checkbox" checked={sync} onChange={(e) => setSync(e.target.checked)} /> sync by page</label>
      </header>
      <div className="panes">
        <div className="pane pdf"><div className="pane-h">SOURCE PDF</div><img src={api.pageUrl(ov.root, page)} alt={`PDF page ${page}`} /></div>
        <div className="pane md" ref={mdRef} onClick={onMdClick}><div className="pane-h">MARKDOWN</div>
          {mode === "markdown" ? <pre>{raw}</pre> : <article className="prose" dangerouslySetInnerHTML={{ __html: rendered }} />}</div>
        <aside className="pane warn-pane"><div className="pane-h">WARNINGS ({issues.length})</div>
          {issues.length === 0 && <p className="muted small">No issues.</p>}
          {issues.map((i, k) => (
            <button key={k} className={`issue sev-${i.severity}`} onClick={() => i.page && setPage(i.page)}>
              <b>{i.severity}</b> {i.message}{i.page ? <span className="small"> — jump to p.{i.page}</span> : null}
            </button>))}
        </aside>
      </div>
    </section>
  );
}

function ExportView({ ov, guard, refresh }: { ov: Overview; guard: Guard; refresh: () => void }) {
  const [dest, setDest] = useState(""); const [pdf, setPdf] = useState(false); const [msg, setMsg] = useState("");
  const [chapter, setChapter] = useState("");
  const run = async (opts: { rag_only?: boolean; chapter?: number }) => {
    const r = await guard(() => api.exportZip(ov.root, dest || undefined, { include_pdf: pdf, ...opts }));
    if (r) setMsg(`Exported: ${r.zip}`);
  };
  return (
    <section>
      <h1>Export</h1>
      <p className="muted">The project folder is plain Markdown/JSON and works without this app: <code>{ov.root}</code></p>
      <div className="form"><label>Destination ZIP path (optional) <input value={dest} placeholder={`${ov.root}.zip`} onChange={(e) => setDest(e.target.value)} /></label>
        <label className="small"><input type="checkbox" checked={pdf} onChange={(e) => setPdf(e.target.checked)} /> Include original PDF in the ZIP</label></div>
      <div className="row wrap">
        <button className="primary" onClick={() => run({})}>Export project ZIP</button>
        <button onClick={() => run({ rag_only: true })}>Export RAG package</button>
        <select value={chapter} onChange={(e) => setChapter(e.target.value)}><option value="">Chapter…</option>{ov.chapters.filter((c) => c.markdown_file).map((c) => <option key={c.number} value={c.number}>{c.number}. {c.title}</option>)}</select>
        <button disabled={!chapter} onClick={() => run({ chapter: Number(chapter) })}>Export chapter</button>
        <button onClick={() => guard(() => api.buildIndexes(ov.root)).then((r) => r && setMsg(`Generated indexes: ${Object.entries(r).map(([k, v]) => `${k} ${v}`).join(", ")}`))}>Generate navigation indexes</button>
      </div>
      {msg && <p className="ok-text">{msg}</p>}
      <p className="small muted">Generated indexes are labelled “not part of the original textbook” and never mixed into chapters.</p>
    </section>
  );
}

function SettingsView({ guard }: { guard: Guard }) {
  const [s, setS] = useState<Record<string, any>>({ provider: "openai", model: "", ai_enabled: false, ocr: true, theme: "system", markdown_mode: "portable", output_dir: "" });
  const [key, setKey] = useState(""); const [msg, setMsg] = useState(""); const [health, setHealth] = useState<{ ocr_available: boolean; version: string } | null>(null);
  const [preview, setPreview] = useState<string>("");
  useEffect(() => { guard(api.settings).then((x) => x && setS(x)); guard(api.health).then((h) => h && setHealth(h)); }, [guard]);
  const set = (k: string, v: any) => setS((o) => ({ ...o, [k]: v }));
  const save = async () => {
    const { api_key, ...rest } = s;
    await guard(() => api.saveSettings(rest)); if (key) await guard(() => api.saveSecret(key));
    localStorage.setItem("theme", s.theme); document.documentElement.dataset.theme = s.theme; setMsg("Saved."); setKey("");
  };
  return (
    <section>
      <h1>Settings</h1>
      <div className="card form">
        <h3>AI provider (optional)</h3>
        <label className="small"><input type="checkbox" checked={!!s.ai_enabled} onChange={(e) => set("ai_enabled", e.target.checked)} /> Enable AI enhancements (headings/index classification). Deterministic extraction works without AI.</label>
        <label>Provider <select value={s.provider} onChange={(e) => set("provider", e.target.value)}><option value="openai">OpenAI</option><option value="gemini">Google Gemini</option></select></label>
        <label>Model (blank = default) <input value={s.model || ""} onChange={(e) => set("model", e.target.value)} /></label>
        <label>API key <input type="password" autoComplete="off" value={key} placeholder={s.api_key ? "saved (enter to replace)" : "not set"} onChange={(e) => setKey(e.target.value)} /></label>
        <p className="small muted">The key is stored locally and sent only to the selected provider. Only short term lists are ever sent — never whole chapters.</p>
        <div className="row"><button onClick={async () => { await save(); const r = await guard(() => api.aiTest()); if (r) setMsg(`Connection OK: ${r.reply}`); }}>Test connection</button>
          <button onClick={() => guard(() => api.aiPreview()).then((p) => p && setPreview(`Would send ${p.characters} characters to ${p.provider} (${p.host}), model ${p.model}`))}>Preview what is sent</button></div>
        {preview && <p className="small">{preview}</p>}
      </div>
      <div className="card form">
        <h3>Conversion</h3>
        <label className="small"><input type="checkbox" checked={!!s.ocr} onChange={(e) => set("ocr", e.target.checked)} /> OCR fallback for pages without text (engine OCR: {health ? (health.ocr_available ? "Tesseract found" : "Tesseract not installed — such pages are marked UNCERTAIN") : "…"})</label>
        <label>Output folder <input value={s.output_dir || ""} onChange={(e) => set("output_dir", e.target.value)} /></label>
        <label>Markdown mode <select value={s.markdown_mode} onChange={(e) => set("markdown_mode", e.target.value)}><option value="portable">Portable (GFM/CommonMark)</option><option value="obsidian">Obsidian callouts (export view)</option></select></label>
        <label>Theme <select value={s.theme} onChange={(e) => set("theme", e.target.value)}><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
      </div>
      <button className="primary" onClick={save}>Save settings</button> {msg && <span className="ok-text">{msg}</span>}
      <p className="small muted">Engine: {ENGINE} {health && `· v${health.version}`}</p>
    </section>
  );
}
