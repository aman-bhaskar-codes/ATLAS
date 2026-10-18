import { describe, expect, it } from 'vitest';
import { SubmitCoordinator } from '../../../components/workspace/SubmitCoordinator';

const draft = () => ({text: '', attachments: [{id: 'local_one', file: new File(['notes'], 'notes.md'), type: 'markdown' as const}]});

describe('composer submission', () => {
  it('uploads attachment-only input and synchronously blocks duplicate starts', async () => {
    const coordinator = new SubmitCoordinator();
    const current = draft();
    const requests: unknown[] = [];
    let complete!: (value: {id: string; type: 'markdown'}) => void;
    const upload = () => new Promise<{id: string; type: 'markdown'}>(resolve => { complete = resolve; });
    const create = async (request: unknown) => {requests.push(request); return {id: 'task_one'};};
    const first = coordinator.start(current, {upload, create});
    expect(coordinator.start(current, {upload, create})).toBeNull();
    expect(requests).toEqual([]);
    complete({id: 'att_server', type: 'markdown'});
    await expect(first).resolves.toEqual({id: 'task_one'});
    expect(requests).toEqual([expect.objectContaining({request: '', attachments: [{id: 'att_server', type: 'markdown'}]})]);
  });

  it('preserves uploaded refs and idempotency on unchanged failed task retries', async () => {
    const coordinator = new SubmitCoordinator();
    const current = draft();
    let uploads = 0;
    const requests: {idempotency_key: string}[] = [];
    const upload = async () => {uploads++; return {id: 'att_server', type: 'markdown' as const};};
    const create = async (request: {idempotency_key: string}) => { requests.push(request); throw new Error('Task rejected'); };
    await expect(coordinator.start(current, {upload, create})).rejects.toThrow('Task rejected');
    await expect(coordinator.start(current, {upload, create})).rejects.toThrow('Task rejected');
    expect(requests[0].idempotency_key).toBe(requests[1].idempotency_key);
    expect(uploads).toBe(1);
    await expect(coordinator.start({...current, text: 'New request'}, {upload, create})).rejects.toThrow();
    expect(requests[2].idempotency_key).not.toBe(requests[1].idempotency_key);
  });

  it('does not execute a task if an upload fails and retries the failed upload', async () => {
    const coordinator = new SubmitCoordinator();
    const current = draft();
    let creates = 0;
    const create = async () => {creates++; return {id: 'task_one'};};
    await expect(coordinator.start(current, {upload: async () => {throw new Error('Image unsupported');}, create})).rejects.toThrow('Image unsupported');
    expect(creates).toBe(0);
    await expect(coordinator.start(current, {upload: async () => ({id: 'att_uploaded', type: 'markdown' as const}), create})).resolves.toEqual({id: 'task_one'});
    expect(creates).toBe(1);
  });

  it('rejects empty input and more than eight files before uploading', () => {
    const coordinator = new SubmitCoordinator();
    const deps = {upload: async () => ({id: 'x', type: 'code' as const}), create: async () => ({id: 'x'})};
    expect(coordinator.start({text: ' ', attachments: []}, deps)).toBeNull();
    expect(() => coordinator.start({...draft(), attachments: Array(9).fill(draft().attachments[0])}, deps)).toThrow('8');
  });
});

it('reuploads retained files after a missing or expired attachment rejection', async () => {
  const coordinator = new SubmitCoordinator();
  const current = draft();
  let uploads = 0;
  let submissions = 0;
  const upload = async () => ({id: `att_${++uploads}`, type: 'markdown' as const});
  const create = async () => {
    if (++submissions === 1) throw Object.assign(new Error('Attachment not found'), {status: 404});
    return {id: 'task_new'};
  };
  await expect(coordinator.start(current, {upload, create})).rejects.toThrow();
  await expect(coordinator.start(current, {upload, create})).resolves.toEqual({id: 'task_new'});
  expect(uploads).toBe(2);
});
