"use client";

import { useReducer, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { z } from "zod";
import { describeError } from "@/lib/api/errorMessage";
import { SubmitCoordinator } from "./SubmitCoordinator";
import { ErrorRow } from "@/components/primitives/ErrorState";
import { atlasApi, requestContract } from "@/lib/api/client";
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
  const coordinator = useRef(new SubmitCoordinator());
  const [validationError, setValidationError] = useState<unknown>(null);


  const queryClient = useQueryClient();
  const router = useRouter();

  const {
    mutate: submitCommand,
    isPending,
    error: submitError,
    reset: resetSubmit,
  } = useMutation({
    mutationFn: (job: ReturnType<typeof atlasApi.createTask>) => job,
    onSuccess: (task) => {
      dispatch({ type: "RESET" });


      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      router.push(`/tasks/${encodeURIComponent(task.id)}`);
    },
    // Keep the draft on failure; inline feedback remains visible without preflight.
  });

  const handleStart = () => {
    if (isPending) return;
    setValidationError(null);
    try {
      const job = coordinator.current.start(state, {
        upload: (file) => {
          const body = new FormData();
          body.append("file", file);
          return requestContract("/attachments", z.object({
            id: z.string(),
            type: z.enum(["image", "pdf", "code", "markdown", "file"]),
          }), { method: "POST", body });
        },
        create: atlasApi.createTask,
        status: (id, status, error) => dispatch({type: "UPDATE_ATTACHMENT", payload: {id, updates: {status, error: error ? describeError(error) : undefined}}}),
      });
      if (job) {
        resetSubmit();
        submitCommand(job);
      }
    } catch (error) {
      setValidationError(error);
    }
  };

  const cancelPreflight = () => {
    resetSubmit();
    dispatch({ type: "SET_PREFLIGHT_STATUS", payload: "idle" });
  };



  return (
    <WorkspaceContext.Provider value={{ state, dispatch, isPending }}>
      <section className="command mb-8" aria-label="Command composer" style={{ position: 'relative' }}>
        


        <div className="flex flex-col border border-ink-700 bg-ink-950 rounded-lg overflow-hidden focus-within:border-gold-500/50 focus-within:ring-1 focus-within:ring-gold-500/20 transition-all shadow-xl relative">
          
          <PlannerPreview
            onConfirm={handleStart}
            onCancel={cancelPreflight}
            isPending={isPending}
            error={validationError || submitError}
          />

          {Boolean(submitError || validationError) && state.preflightStatus === "idle" && (
            <div role="alert" className="px-4 pt-3"><ErrorRow error={validationError || submitError} /></div>
          )}
          <ContextIndicatorBar />
          <PromptEditor onStart={handleStart} />
          <AttachmentGallery />
          <CommandFooter onStart={handleStart} isPending={isPending} />
        </div>

      </section>
    </WorkspaceContext.Provider>
  );
}
