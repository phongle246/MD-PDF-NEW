import { marked } from "marked";
import DOMPurify from "dompurify";
import { api } from "./api";

// Render source-faithful Markdown for preview. Page markers become clickable chips; images resolve through the engine.
export function renderMarkdown(md: string, root: string, chapterFile: string): string {
  const body = md.replace(/^---\n[\s\S]*?\n---\n/, "");
  const withMarkers = body.replace(/<!--\s*source_page:\s*(\d+)\s*-->/g, (_m, p) => `<span class="pagemark" data-page="${p}">p.${p}</span>`);
  const html = marked.parse(withMarkers, { async: false }) as string;
  const base = chapterFile.split("/").slice(0, -1).join("/");
  const fixed = html.replace(/src="\.\.\/(assets\/[^"]+)"/g, (_m, p) => `src="${api.assetUrl(root, p)}"`).replace(/src="(?!http|data)([^"]+)"/g, (_m, p) => `src="${api.assetUrl(root, (base ? base + "/" : "") + p)}"`);
  return DOMPurify.sanitize(fixed, { ADD_ATTR: ["data-page", "id"], ADD_TAGS: ["details", "summary"] });
}
