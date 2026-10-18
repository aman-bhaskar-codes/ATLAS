import { expect, it } from 'vitest';
import { McpServerSchema, McpResourceSchema } from '../contracts';
it('keeps inspection fields and drops sensitive server diagnostics', () => {
  const result = McpServerSchema.parse({ server_id: 'local', name: 'Local', transport: 'stdio', enabled: true, startup: 'lazy', trust_level: 'local', state: 'configured', connected: false, tools: 0, env: { API_KEY: 'private' }, last_failure: 'https://user:password@host/' });
  expect(result).not.toHaveProperty('env'); expect(result).not.toHaveProperty('last_failure');
});
it('never exposes a resource URI in the inspection view', () => {
  expect(McpResourceSchema.parse({ name: 'Document', uri: 'https://user:private@host/file', mime_type: 'text/plain' })).toEqual({ name: 'Document', mime_type: 'text/plain' });
});
