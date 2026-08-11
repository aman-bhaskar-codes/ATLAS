import { useWorkspace } from "../WorkspaceState";
import { AttachmentCard } from "./AttachmentCard";

export function AttachmentGallery() {
  const { state } = useWorkspace();

  if (state.attachments.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 p-2 bg-ink-900 border-t border-ink-800">
      {state.attachments.map((att) => (
        <AttachmentCard key={att.id} attachment={att} />
      ))}
    </div>
  );
}
