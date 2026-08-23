"use client";

import { useReducer, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { atlasApi } from "@/lib/api/client";
import { 
  WorkspaceContext, 
  workspaceReducer, 
  initialState, 
} from "./WorkspaceState";
import { ContextIndicatorBar } from "./context/ContextIndicatorBar";
import { PromptEditor } from "./editor/PromptEditor";
import { AttachmentGallery } from "./attachments/AttachmentGallery";
import { CommandFooter } from "./CommandFooter";
import { PlannerPreview } from "./preflight/PlannerPreview";
import { Mic, Camera, Monitor } from "lucide-react";

export function CommandWorkspace() {
  const [state, dispatch] = useReducer(workspaceReducer, initialState);
  const [isListening, setIsListening] = useState(false);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [isScreenShared, setIsScreenShared] = useState(false);

  const queryClient = useQueryClient();
  const router = useRouter();

  const {
    mutate: submitCommand,
    isPending,
    error: submitError,
    reset: resetSubmit,
  } = useMutation({
    mutationFn: async () => {
      // Phase 4: Eventually this will upload attachments first,
      // but for Phase 2 we just map the mock IDs directly.
      const attachments = state.attachments.map(att => ({
        id: att.id,
        type: att.type
      }));

      return atlasApi.createTask({
        request: state.text,
        idempotency_key: crypto.randomUUID(),
        attachments: attachments
      });
    },
    onSuccess: (task) => {
      dispatch({ type: "RESET" });
      setIsListening(false);
      setIsCameraActive(false);
      setIsScreenShared(false);

      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      router.push(`/tasks/${encodeURIComponent(task.id)}`);
    },
    // No onError: the error is rendered by PlannerPreview, which is the panel the
    // user is looking at when they press Start. Mutations do not auto-retry, so
    // that render is the only place the failure can ever appear.
  });

  const handleStart = () => {
    if (!state.text.trim() && !isCameraActive && !isScreenShared && state.attachments.length === 0) {
      return;
    }
    // Straight to the confirmation gate. What was here: a 1500ms setTimeout that
    // moved through an "analyzing" state rendering "Analyzing request & classifying
    // safety…" with a spinner — while nothing was analysed and nothing was
    // classified. There is no pre-flight endpoint to await, so the honest version
    // does not pretend to be waiting on one.
    resetSubmit();
    dispatch({ type: "SET_PREFLIGHT_STATUS", payload: "ready" });
  };

  const confirmAndExecute = () => {
    submitCommand();
  };

  const cancelPreflight = () => {
    resetSubmit();
    dispatch({ type: "SET_PREFLIGHT_STATUS", payload: "idle" });
  };

  const toggleMic = () => {
    setIsListening(!isListening);
    if (!isListening) {
      dispatch({ type: "SET_TEXT", payload: state.text ? state.text + " [Voice input recording...]" : "[Voice input recording...]" });
    } else {
      dispatch({ type: "SET_TEXT", payload: state.text.replace(" [Voice input recording...]", "").replace("[Voice input recording...]", "") });
    }
  };

  return (
    <WorkspaceContext.Provider value={{ state, dispatch }}>
      <section className="command mb-8" aria-label="Command composer" style={{ position: 'relative' }}>
        
        {/* Top Controls (Mic, Camera, Screen) */}
        <div style={{ display: 'flex', gap: '0.35rem', marginBottom: '1rem' }}>
          <button 
            title="Voice Input"
            onClick={toggleMic}
            className="icon-btn"
            style={{ 
              height: '32px', width: '32px',
              borderColor: isListening ? 'var(--ember-500)' : undefined, 
              color: isListening ? 'var(--ember-400)' : undefined 
            }}
          >
            <Mic size={14} className={isListening ? "animate-pulse" : ""} />
          </button>
          <button 
            title="Video Feed"
            onClick={() => setIsCameraActive(!isCameraActive)}
            className="icon-btn"
            style={{ 
              height: '32px', width: '32px',
              borderColor: isCameraActive ? 'var(--jade-500)' : undefined, 
              color: isCameraActive ? 'var(--jade-400)' : undefined 
            }}
          >
            <Camera size={14} />
          </button>
          <button 
            title="Vision / Screen Share"
            onClick={() => setIsScreenShared(!isScreenShared)}
            className="icon-btn"
            style={{ 
              height: '32px', width: '32px',
              borderColor: isScreenShared ? 'var(--gold-500)' : undefined, 
              color: isScreenShared ? 'var(--gold-400)' : undefined 
            }}
          >
            <Monitor size={14} />
          </button>
        </div>

        <div className="flex flex-col border border-ink-700 bg-ink-950 rounded-lg overflow-hidden focus-within:border-gold-500/50 focus-within:ring-1 focus-within:ring-gold-500/20 transition-all shadow-xl relative">
          
          <PlannerPreview
            onConfirm={confirmAndExecute}
            onCancel={cancelPreflight}
            isPending={isPending}
            error={submitError}
          />

          <ContextIndicatorBar />
          <PromptEditor onStart={handleStart} />
          <AttachmentGallery />
          <CommandFooter onStart={handleStart} isPending={isPending} />
        </div>

      </section>
    </WorkspaceContext.Provider>
  );
}
