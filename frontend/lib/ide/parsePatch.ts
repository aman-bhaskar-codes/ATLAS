/**
 * Split a unified `git diff` patch into per-file sections so the workbench can
 * show one file's hunks in isolation. Purely lexical — it keys off the
 * `diff --git a/<old> b/<new>` section headers git always emits — so it never
 * needs a second backend round-trip per file.
 */
export interface PatchSection {
  /** New-side workspace-relative path (the `b/` path), or the old path for deletes. */
  path: string;
  /** The raw text of this file's section, including its `diff --git` header. */
  body: string;
}

const HEADER = /^diff --git a\/(.+?) b\/(.+)$/;

export function splitPatch(patch: string): PatchSection[] {
  if (!patch.trim()) return [];
  const lines = patch.split("\n");
  const sections: PatchSection[] = [];
  let current: { path: string; lines: string[] } | null = null;

  for (const line of lines) {
    const m = line.match(HEADER);
    if (m) {
      if (current) sections.push({ path: current.path, body: current.lines.join("\n") });
      // Prefer the new-side path; fall back to the old side for deletions.
      current = { path: m[2] || m[1], lines: [line] };
    } else if (current) {
      current.lines.push(line);
    }
  }
  if (current) sections.push({ path: current.path, body: current.lines.join("\n") });
  return sections;
}

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "context";

/** Classify one patch line for colorized rendering. */
export function classifyDiffLine(line: string): DiffLineKind {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("diff --git") || line.startsWith("index ") || line.startsWith("new file") || line.startsWith("deleted file") || line.startsWith("rename ")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "context";
}
