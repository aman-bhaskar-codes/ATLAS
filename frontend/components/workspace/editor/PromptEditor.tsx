import { useRef, useEffect, useCallback } from "react";
import { useWorkspace } from "../WorkspaceState";

export function PromptEditor({ onStart }: { onStart: () => void }) {
  const { state, dispatch } = useWorkspace();
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
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onStart();
    }
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dispatch({ type: "SET_DRAGGING", payload: false });
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      // Process files...
      // For now, we'll just log them or dispatch placeholder additions
      const files = Array.from(e.dataTransfer.files);
      const newAttachments = files.map(file => {
        const id = `local_${Math.random().toString(36).substring(7)}`;
        const type = file.type.startsWith("image/") ? "image" : file.type === "application/pdf" ? "pdf" : "file";
        const previewUrl = type === "image" ? URL.createObjectURL(file) : undefined;
        return {
          id,
          file,
          type: type as any,
          status: "ready" as const,
          previewUrl
        };
      });
      dispatch({ type: "ADD_ATTACHMENTS", payload: newAttachments });
    }
  }, [dispatch]);

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
    if (e.clipboardData.files.length > 0) {
      // Don't prevent default text pasting, just capture files
      const files = Array.from(e.clipboardData.files);
      const newAttachments = files.map(file => {
        const id = `local_${Math.random().toString(36).substring(7)}`;
        const type = file.type.startsWith("image/") ? "image" : file.type === "application/pdf" ? "pdf" : "file";
        const previewUrl = type === "image" ? URL.createObjectURL(file) : undefined;
        return {
          id,
          file,
          type: type as any,
          status: "ready" as const,
          previewUrl
        };
      });
      dispatch({ type: "ADD_ATTACHMENTS", payload: newAttachments });
    }
  }, [dispatch]);

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
        value={state.text}
        onChange={(e) => dispatch({ type: "SET_TEXT", payload: e.target.value })}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        placeholder="Research, organize, remember, or ask ATLAS to do something..."
        className="w-full bg-transparent text-slate-100 placeholder:text-slate-500 border-none outline-none resize-none px-4 py-3 leading-relaxed focus:ring-0 text-[15px]"
        style={{ minHeight: '60px' }}
      />

      {state.isDragging && (
        <div className="absolute inset-0 z-10 bg-gold-900/10 border-2 border-dashed border-gold-500/50 rounded flex items-center justify-center backdrop-blur-[2px]">
          <span className="text-gold-400 font-medium tracking-wide">Drop files to attach</span>
        </div>
      )}
    </div>
  );
}
