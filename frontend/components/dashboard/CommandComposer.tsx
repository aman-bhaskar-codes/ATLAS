"use client";

import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import { ArrowUpRight, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";

export function CommandComposer() {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const queryClient = useQueryClient();
  const router = useRouter();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const { mutate: submitCommand, isPending } = useMutation({
    mutationFn: (request: string) =>
      atlasApi.createTask({
        request,
        idempotency_key: crypto.randomUUID(),
      }),
    onSuccess: (task) => {
      setInput("");
      // Force refresh tasks list so it shows in timeline
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      // Navigate to live run
      router.push(`/tasks/${encodeURIComponent(task.id)}`);
    },
  });

  const handleStart = () => {
    if (!input.trim()) {
      inputRef.current?.focus();
      return;
    }
    submitCommand(input.trim());
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleStart();
    }
  };

  return (
    <section className="command" aria-label="Command composer">
      <div className="command-label">
        <span>New command</span>
        <span className="mono">⌘ K</span>
      </div>
      <textarea
        ref={inputRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Research, organize, remember, or ask ATLAS to do something..."
        disabled={isPending}
      />
      <div className="command-foot">
        <div className="chips">
          <button className="chip" onClick={() => setInput("Research the latest papers on autonomous agents")}>research</button>
          <button className="chip" onClick={() => setInput("Recall what I decided about ATLAS last week")}>recall memory</button>
          <button className="chip" onClick={() => setInput("Check my system health")}>system health</button>
        </div>
        <button className="primary" onClick={handleStart} disabled={isPending || !input.trim()}>
          {isPending ? <Loader2 className="w-5 h-5 animate-spin" /> : <ArrowUpRight className="w-5 h-5" />}
          {isPending ? "Starting" : "Start task"}
        </button>
      </div>
    </section>
  );
}
