import type { AttachmentType } from './WorkspaceState';

export type UploadedAttachment = { id: string; type: AttachmentType };
type Draft = { text: string; attachments: { id: string; file: File; type: AttachmentType }[] };
type TaskRequest = { request: string; idempotency_key: string; attachments: UploadedAttachment[] };
type Dependencies<T> = {
  upload: (file: File) => Promise<UploadedAttachment>;
  create: (request: TaskRequest) => Promise<T>;
  status?: (id: string, status: 'uploading' | 'ready' | 'error', error?: unknown) => void;
};

/** Owns the synchronous submit lock and retry identity outside React render timing. */
export class SubmitCoordinator {
  private pending = false;
  private fingerprint = '';
  private key = '';
  private uploaded = new Map<string, { file: File; ref: UploadedAttachment }>();

  start<T>(draft: Draft, deps: Dependencies<T>): Promise<T> | null {
    if (this.pending || (!draft.text.trim() && draft.attachments.length === 0)) return null;
    if (draft.attachments.length > 8) throw new Error('Attach at most 8 files. Remove extra files and retry.');
    const fingerprint = JSON.stringify([draft.text, draft.attachments.map(att => [att.id, att.file.name, att.file.size, att.file.lastModified])]);
    if (fingerprint !== this.fingerprint) {
      this.fingerprint = fingerprint;
      this.key = crypto.randomUUID();
    }
    const key = this.key;
    this.pending = true;
    for (const id of this.uploaded.keys()) {
      if (!draft.attachments.some(att => att.id === id)) this.uploaded.delete(id);
    }
    return this.execute(draft, key, deps).finally(() => { this.pending = false; });
  }

  private async execute<T>(draft: Draft, key: string, deps: Dependencies<T>): Promise<T> {
    const refs: UploadedAttachment[] = [];
    for (const attachment of draft.attachments) {
      const cached = this.uploaded.get(attachment.id);
      if (cached?.file === attachment.file) {
        refs.push(cached.ref);
        continue;
      }
      deps.status?.(attachment.id, 'uploading');
      try {
        if (attachment.file.size > 65_536) throw new Error('Files must be at most 64 KiB. Attach a smaller excerpt.');
        const ref = await deps.upload(attachment.file);
        this.uploaded.set(attachment.id, {file: attachment.file, ref});
        refs.push(ref);
        deps.status?.(attachment.id, 'ready');
      } catch (error) {
        deps.status?.(attachment.id, 'error', error);
        throw error;
      }
    }
    let task: T;
    try {
      task = await deps.create({request: draft.text, idempotency_key: key, attachments: refs});
    } catch (error) {
      if (error && typeof error === 'object' && 'status' in error && error.status === 404 && refs.length) {
        this.uploaded.clear();
        throw new Error('An attachment expired or is missing. Start task again to upload the retained files.');
      }
      throw error;
    }
    this.key = '';
    this.fingerprint = '';
    this.uploaded.clear();
    return task;
  }
}
