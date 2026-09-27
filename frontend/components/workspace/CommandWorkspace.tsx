"use client";

import { useReducer } from "react";
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

export function CommandWorkspace() {
  const [state, dispatch] = useReducer(workspaceReducer, initialState);


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


      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      router.push(`/tasks/${encodeURIComponent(task.id)}`);
    },
    // No onError: the error is rendered by PlannerPreview, which is the panel the
    // user is looking at when they press Start. Mutations do not auto-retry, so
    // that render is the only place the failure can ever appear.
  });

  const handleStart = () => {
    if (!state.text.trim() && state.attachments.length === 0) {
      return;
    }
    resetSubmit();
    submitCommand();
  };

  const confirmAndExecute = () => {
    submitCommand();
  };

  const cancelPreflight = () => {
    resetSubmit();
    dispatch({ type: "SET_PREFLIGHT_STATUS", payload: "idle" });
  };



  return (
    <WorkspaceContext.Provider value={{ state, dispatch }}>
      <section className="command mb-8" aria-label="Command composer" style={{ position: 'relative' }}>
        


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
