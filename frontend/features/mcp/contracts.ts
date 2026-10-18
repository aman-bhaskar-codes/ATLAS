import { z } from 'zod';
// Inspection intentionally omits configuration, endpoint URIs and raw failures.
export const McpServerSchema = z.object({
  server_id: z.string(), name: z.string(), transport: z.string(), enabled: z.boolean(),
  startup: z.string(), trust_level: z.string(), state: z.string(), connected: z.boolean(),
  tools: z.number().int().nonnegative(), protocol_version: z.string().optional(),
  server_version: z.string().optional(), last_success: z.string().nullable().optional(),
});
export const McpToolSchema = z.object({ id: z.string(), name: z.string(), operations: z.array(z.string()), trust_level: z.string(), locality: z.string() });
export const McpResourceSchema = z.object({ name: z.string(), mime_type: z.string().nullable().optional() });
export const McpPromptSchema = z.object({ name: z.string() });
