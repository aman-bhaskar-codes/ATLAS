// frontend/features/ide-checks/contracts.ts
//
// Zod contracts for the Tests + Problems surface (Slice 8):
//   * POST /api/v1/ide/workspaces/{id}/tests       → TestReport
//   * POST /api/v1/ide/workspaces/{id}/diagnostics → DiagnosticReport
//
// Both mirror the backend source of truth exactly (routes_ide.py →
// TestReportResponse / DiagnosticReportResponse). A test/lint command runs through
// the SAME SafetyEngine funnel as any shell dispatch (IDEService → CommandRunner →
// guard); these layers only parse the governed output — no second execution path.
//
// HONESTY (mirrors the backend): test counts are `null`, never a fabricated 0, when
// the framework summary could not be parsed; a linter exiting non-zero WITH findings
// is not an `error`. The UI renders exactly what the backend reports.

import { z } from "zod";

/* ── Tests ──────────────────────────────────────────────────────────────────── */

export const TestOutcomeSchema = z.enum(["passed", "failed", "error", "skipped", "unknown"]);
export type TestOutcome = z.infer<typeof TestOutcomeSchema>;

/** One enumerated failing/erroring case (passing cases are counted, not listed). */
export const TestCaseSchema = z.object({
  name: z.string(),
  outcome: TestOutcomeSchema,
  file: z.string().nullable().default(null),
  line: z.number().int().nullable().default(null),
  message: z.string().nullable().default(null),
});
export type TestCase = z.infer<typeof TestCaseSchema>;

/**
 * The structured outcome of one governed test run. `ok`/`exit_code` always reflect
 * the real process; the counts are `null` when the framework was unrecognized
 * (honest "ran, unparsed"). `denied` means SafetyEngine refused — nothing ran.
 */
export const TestReportSchema = z.object({
  command: z.string(),
  framework: z.string().nullable().default(null),
  ok: z.boolean(),
  exit_code: z.number().int().nullable().default(null),
  duration_ms: z.number().int().default(0),
  passed: z.number().int().nullable().default(null),
  failed: z.number().int().nullable().default(null),
  skipped: z.number().int().nullable().default(null),
  total: z.number().int().nullable().default(null),
  failures: z.array(TestCaseSchema).default([]),
  denied: z.boolean().default(false),
  error: z.string().nullable().default(null),
  output_tail: z.string().default(""),
});
export type TestReport = z.infer<typeof TestReportSchema>;

/* ── Diagnostics (Problems) ─────────────────────────────────────────────────── */

export const DiagnosticSeveritySchema = z.enum(["error", "warning", "info", "hint"]);
export type DiagnosticSeverity = z.infer<typeof DiagnosticSeveritySchema>;

/** One normalized problem parsed from a linter/type-checker's output. */
export const DiagnosticSchema = z.object({
  file: z.string(),
  line: z.number().int().nullable().default(null),
  col: z.number().int().nullable().default(null),
  severity: DiagnosticSeveritySchema,
  message: z.string().default(""),
  code: z.string().nullable().default(null),
  source: z.string().nullable().default(null),
});
export type Diagnostic = z.infer<typeof DiagnosticSchema>;

/**
 * Normalized problems from one governed lint/type-check run. `errors`/`warnings`
 * count the parsed diagnostics — NOT the process exit (a linter exits non-zero
 * because it found problems, and that is not an `error`).
 */
export const DiagnosticReportSchema = z.object({
  command: z.string(),
  ok: z.boolean(),
  exit_code: z.number().int().nullable().default(null),
  duration_ms: z.number().int().default(0),
  diagnostics: z.array(DiagnosticSchema).default([]),
  errors: z.number().int().default(0),
  warnings: z.number().int().default(0),
  denied: z.boolean().default(false),
  error: z.string().nullable().default(null),
  output_tail: z.string().default(""),
});
export type DiagnosticReport = z.infer<typeof DiagnosticReportSchema>;

/** True when the test run produced parseable counts (vs "ran, unparsed"). */
export function hasParsedCounts(report: TestReport): boolean {
  return report.total !== null;
}
