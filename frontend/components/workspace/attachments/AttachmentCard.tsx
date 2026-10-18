import { X, FileText, Image as ImageIcon, Code2, FileCode2, Paperclip, Loader2 } from "lucide-react";
import { Attachment, useWorkspace } from "../WorkspaceState";

export function AttachmentCard({ attachment }: { attachment: Attachment }) {
  const { dispatch, isPending } = useWorkspace();

  const handleRemove = () => {
    dispatch({ type: "REMOVE_ATTACHMENT", payload: attachment.id });
  };

  const getIcon = () => {
    switch (attachment.type) {
      case "image": return <ImageIcon size={14} className="text-jade-400" />;
      case "pdf": return <FileText size={14} className="text-ember-400" />;
      case "code": return <Code2 size={14} className="text-blue-400" />;
      case "markdown": return <FileCode2 size={14} className="text-gold-400" />;
      default: return <Paperclip size={14} className="text-slate-400" />;
    }
  };

  return (
    <div className="relative group flex items-center gap-3 bg-ink-850 border border-ink-700 p-2 pr-8 rounded-md transition-all hover:border-gold-500/50">
      {/* Remove Button */}
      <button 
        onClick={handleRemove}
        disabled={isPending}
        aria-label={`Remove ${attachment.file.name}`}
        className="absolute top-2 right-2 opacity-100 transition-opacity text-slate-400 hover:text-ember-400"
        title="Remove attachment"
      >
        <X size={14} />
      </button>

      {/* Preview / Icon */}
      <div className="w-10 h-10 flex items-center justify-center bg-ink-900 rounded overflow-hidden flex-shrink-0 border border-ink-800">
        {getIcon()}
      </div>

      {/* Details */}
      <div className="flex flex-col min-w-0">
        <span className="text-xs text-slate-200 truncate" title={attachment.file.name}>
          {attachment.file.name}
        </span>
        <div className="flex items-center gap-2 mt-1">
          <span className="text-[10px] text-slate-500 font-mono uppercase">
            {attachment.type}
          </span>
          {attachment.status === "uploading" && (
            <span className="text-[10px] text-gold-500 flex items-center gap-1">
              <Loader2 size={10} className="animate-spin" /> Uploading...
            </span>
          )}
          {attachment.status === "queued" && <span className="text-[10px] text-slate-400">Queued</span>}
          {attachment.status === "ready" && <span className="text-[10px] text-jade-400">Uploaded</span>}
          {Boolean(attachment.metadata?.ocr_detected) && (
            <span className="text-[10px] bg-jade-900/50 text-jade-400 px-1 rounded-sm border border-jade-500/20">
              OCR
            </span>
          )}
          {attachment.metadata?.lines != null && (
            <span className="text-[10px] text-slate-400">
              {String(attachment.metadata.lines)} lines
            </span>
          )}
        </div>
        {attachment.error && <span role="alert" className="text-xs text-ember-400 mt-1">{attachment.error}</span>}
      </div>
    </div>
  );
}
