// Thin client for the local mdkb engine (sidecar on 127.0.0.1). Nothing leaves the machine except AI calls made by the engine.
export const ENGINE = (import.meta as any).env?.VITE_ENGINE_URL ?? "http://127.0.0.1:8765";

export type Chapter = {
  number: number; title: string; start_page: number; end_page: number; confidence: number; source: string;
  status: string; pages_done: number; pages_total: number; stage: string; issues: Record<string, number>;
  markdown_file: string; needs_review: boolean; specialty?: string; notes?: string[];
};
export type Overview = {
  title: string; edition: string; pages: number; source_name: string; source_sha256: string; source_ok: boolean;
  has_outline: boolean; chapters: Chapter[]; percent: number; root: string; last_opened?: string; settings: Record<string, any>;
};
export type ProjectCard = { root: string; title: string; edition?: string; percent?: number; chapters?: number; last_opened?: string; pages?: number; missing?: boolean };
export type Job = { id: string; root: string; state: string; chapters: number[]; current: number | null; event: { chapter?: number; stage?: string; page?: number; done?: number; total?: number }; errors: { chapter?: number; error: string }[]; results: Record<string, { status: string }> };
export type SearchHit = { snippet: string; chapter: number; chapter_title: string; heading_path: string; section_id: string; page: number; block_id: string; type: string; markdown_file: string };
export type Issue = { severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW"; code: string; message: string; page?: number | null; block_id?: string | null };
export type SourceMap = { entries: { block_id: string; type: string; source_page: number; bbox: number[]; heading_path: string[]; section_id: string; fingerprint: string }[]; sections: { section_id: string; level: number; title: string }[] };

async function req<T>(path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(ENGINE + path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error("Cannot reach the local engine. Start it with `python -m mdkb serve` (or restart the app).");
  }
  const ct = r.headers.get("Content-Type") || "";
  const data = ct.includes("json") ? await r.json() : null;
  if (!r.ok) throw new Error((data && data.error) || `Engine error ${r.status}`);
  return data as T;
}
const qs = (o: Record<string, string | number | undefined>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export const api = {
  health: () => req<{ ok: boolean; version: string; ocr_available: boolean }>("/api/health"),
  projects: () => req<ProjectCard[]>("/api/projects"),
  inspect: (pdf: string) => req<{ title: string; edition: string; pages: number; has_outline: boolean; chapters: Chapter[]; native_text_ratio: number }>("/api/inspect", { pdf }),
  create: (pdf: string, title?: string, edition?: string, output?: string) => req<Overview>("/api/projects", { pdf, title, edition, output }),
  open: (root: string) => req<Overview>("/api/project/open", { root }),
  overview: (root: string) => req<Overview>(`/api/project/overview?${qs({ root })}`),
  setChapters: (root: string, chapters: Partial<Chapter>[]) => req<Overview>("/api/project/chapters", { root, chapters }),
  convert: (root: string, chapters?: number[], force = false, only_pending = false) => req<Job>("/api/convert", { root, chapters, force, only_pending }),
  jobs: () => req<Job[]>("/api/jobs"),
  jobAction: (id: string, action: "pause" | "cancel" | "resume") => req<Job>(`/api/jobs/${id}/${action}`, {}),
  markdown: (root: string, chapter: number, mode = "normal") => req<{ markdown: string; file: string }>(`/api/chapter/markdown?${qs({ root, chapter, mode })}`),
  report: (root: string, chapter: number) => req<{ report: string; issues: Issue[]; status: string }>(`/api/chapter/report?${qs({ root, chapter })}`),
  sourceMap: (root: string, chapter: number) => req<SourceMap>(`/api/chapter/sourcemap?${qs({ root, chapter })}`),
  pageUrl: (root: string, page: number, zoom = 1.6) => `${ENGINE}/api/page.png?${qs({ root, page, zoom })}`,
  assetUrl: (root: string, path: string) => `${ENGINE}/api/asset?${qs({ root, path })}`,
  search: (root: string, q: string, chapter?: number, type?: string, specialty?: string) => req<SearchHit[]>(`/api/search?${qs({ root, q, chapter, type, specialty })}`),
  buildIndexes: (root: string) => req<Record<string, number>>("/api/indexes/build", { root }),
  resolveXrefs: (root: string) => req<{ linked: number; remaining: number }>("/api/xrefs/resolve", { root }),
  exportZip: (root: string, dest?: string, opts: { include_pdf?: boolean; rag_only?: boolean; chapter?: number } = {}) => req<{ zip: string }>("/api/export", { root, dest, ...opts }),
  settings: () => req<Record<string, any>>("/api/settings"),
  saveSettings: (s: Record<string, any>) => req<Record<string, any>>("/api/settings", s),
  saveSecret: (api_key: string) => req<{ ok: boolean }>("/api/secret", { api_key }),
  aiPreview: (prompt?: string) => req<{ provider: string; host: string; model: string; characters: number; preview: string }>("/api/ai/preview", { prompt }),
  aiTest: () => req<{ reply: string }>("/api/ai/test", {}),
};

// Native file/folder pickers when running inside Tauri; plain path prompt in the browser (dev).
export async function pickPdf(): Promise<string | null> {
  const w = window as any;
  if (w.__TAURI_INTERNALS__) {
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const f = await open({ multiple: false, filters: [{ name: "PDF", extensions: ["pdf"] }] });
      return (f as string) || null;
    } catch { /* fall through */ }
  }
  return window.prompt("Absolute path to the textbook PDF:");
}
