import { describe, expect, it } from 'vitest';
import { platformForPath, openPlatformWindow } from '../navigation';

describe('platform boundaries', () => {
  it.each(['/research', '/research/history', '/research/corpus', '/research/status', '/research/session-1'])('owns %s as research', path => expect(platformForPath(path)).toBe('research'));
  it.each(['/workspaces', '/ide', '/ide/training', '/dev', '/dev/workspace-1'])('owns %s as IDE', path => expect(platformForPath(path)).toBe('ide'));
  it.each(['/', '/models', '/tools', '/memory', '/approvals', '/researcher', '/development'])('keeps %s in command centre', path => expect(platformForPath(path)).toBe('command'));
  it('reports a blocked popup for same-window fallback', () => {
    expect(openPlatformWindow('research', () => null)).toEqual({ opened: false });
  });
  it('treats browser launch failures as blocked', () => {
    expect(openPlatformWindow('ide', () => { throw new Error('blocked'); })).toEqual({ opened: false });
  });
  it('opens a named same-origin tab with its opener detached', () => {
    const handle = { closed: false, opener: {}, focus: () => {} };
    let openedPath = '';
    const result = openPlatformWindow('ide', path => { openedPath = path; return handle as unknown as Window; });
    expect(openedPath).toBe('/workspaces');
    expect(handle.opener).toBe(null);
    expect(result.opened).toBe(true);
  });
});
