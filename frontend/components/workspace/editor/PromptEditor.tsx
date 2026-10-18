import { useRef, useEffect, useCallback } from "react";
import { Paperclip } from "lucide-react";
import { useWorkspace, type AttachmentType } from "../WorkspaceState";

export function PromptEditor({ onStart }: { onStart: () => void }) {
  const { state, dispatch, isPending } = useWorkspace();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    // Auto-resize textarea
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 400)}px`;
    }
  }, [state.text]);

  useEffect(() => {
    const handleGlobalKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        textareaRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleGlobalKeyDown);
    return () => window.removeEventListener("keydown", handleGlobalKeyDown);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      onStart();
    }
  };

  const addFiles = useCallback((files: File[]) => {
    if (isPending) return;
    dispatch({type: "ADD_ATTACHMENTS", payload: files.map(file => {
      const suffix = file.name.split(".").pop()?.toLowerCase();
      const type: AttachmentType = file.type.startsWith("image/") ? "image" : suffix === "pdf" ? "pdf" : suffix === "md" ? "markdown" : "code";
      return {id: `local_${crypto.randomUUID()}`, file, type, status: "queued" as const};
    })});
  }, [dispatch, isPending]);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dispatch({ type: "SET_DRAGGING", payload: false });
    addFiles(Array.from(e.dataTransfer.files));
  }, [dispatch, addFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dispatch({ type: "SET_DRAGGING", payload: true });
  }, [dispatch]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Only set to false if we are actually leaving the container, not entering a child
    if (e.currentTarget.contains(e.relatedTarget as Node)) return;
    dispatch({ type: "SET_DRAGGING", payload: false });
  }, [dispatch]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    addFiles(Array.from(e.clipboardData.files));
  }, [addFiles]);

  return (
    <div 
      className="relative w-full"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
    >
      <textarea
        ref={textareaRef}
        aria-label="Task request"
        disabled={isPending}
        value={state.text}
        onChange={(e) => dispatch({ type: "SET_TEXT", payload: e.target.value })}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        placeholder="Research, organize, remember, or ask ATLAS to do something..."
        className="w-full bg-transparent text-slate-100 placeholder:text-slate-500 border-none outline-none resize-none px-4 py-3 leading-relaxed focus:ring-0 text-[15px]"
        style={{ minHeight: '60px' }}
      />

      <div className="flex flex-wrap items-center gap-3 px-4 pb-2 text-xs text-slate-500">
        <input ref={fileInputRef} type="file" multiple className="hidden" aria-label="Attachment files"
          accept=".txt,.md,.markdown,.json,.py,.js,.ts,.tsx,.jsx,.go,.rs,.cpp,.c,.h,.css,.html,.yaml,.yml,.sh,.sql,.pdf"
          onChange={e => {addFiles(Array.from(e.target.files || [])); e.target.value = "";}} />
        <button type="button" disabled={isPending} onClick={() => fileInputRef.current?.click()} className="flex items-center gap-1 hover:text-slate-200">
          <Paperclip size={13} /> Attach files
        </button>
        <span>Text, code, markdown, JSON, text-based PDF · 64 KiB each · up to 8 files</span>
      </div>
      {state.isDragging && (
        <div className="absolute inset-0 z-10 bg-gold-900/10 border-2 border-dashed border-gold-500/50 rounded flex items-center justify-center backdrop-blur-[2px]">
          <span className="text-gold-400 font-medium tracking-wide">Drop files to attach</span>
        </div>
      )}
    </div>
  );
}
