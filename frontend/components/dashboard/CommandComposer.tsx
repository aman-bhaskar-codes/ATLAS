"use client";

import { useState, useRef, useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { atlasApi } from "@/lib/api/client";
import { ArrowUpRight, Loader2, Mic, Camera, Monitor, X } from "lucide-react";
import { useRouter } from "next/navigation";

export function CommandComposer() {
  const [input, setInput] = useState("");
  const [isListening, setIsListening] = useState(false);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const [isScreenShared, setIsScreenShared] = useState(false);
  
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
      setIsListening(false);
      setIsCameraActive(false);
      setIsScreenShared(false);
      // Force refresh tasks list so it shows in timeline
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      // Navigate to live run
      router.push(`/tasks/${encodeURIComponent(task.id)}`);
    },
  });

  const handleStart = () => {
    if (!input.trim() && !isCameraActive && !isScreenShared) {
      inputRef.current?.focus();
      return;
    }
    const finalInput = input.trim() || (isCameraActive ? "Analyze current camera feed" : "Analyze current screen");
    submitCommand(finalInput);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleStart();
    }
  };

  const toggleMic = () => {
    setIsListening(!isListening);
    if (!isListening) {
      setInput(prev => prev ? prev + " [Voice input recording...]" : "[Voice input recording...]");
    } else {
      setInput(prev => prev.replace(" [Voice input recording...]", "").replace("[Voice input recording...]", ""));
    }
  };

  return (
    <section className="command" aria-label="Command composer" style={{ position: 'relative' }}>
      <div className="command-label" style={{ marginBottom: '1rem' }}>
        <span>New command</span>
        <span className="mono">⌘ K</span>
      </div>

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

      <textarea
        ref={inputRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={isListening ? "Listening..." : "Research, organize, remember, or ask ATLAS to do something..."}
        disabled={isPending}
        style={{ borderColor: isListening ? 'var(--ember-500)' : undefined, minHeight: '60px' }}
      />
      
      {(isCameraActive || isScreenShared) && (
        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem', marginTop: '-0.5rem' }}>
          {isCameraActive && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--ink-850)', padding: '0.35rem 0.6rem', borderRadius: '6px', border: '1px solid var(--jade-500)', fontSize: '0.75rem', color: 'var(--jade-400)' }}>
              <Camera size={14} /> Live Camera Feed
              <X size={14} style={{ cursor: 'pointer', marginLeft: '0.25rem' }} onClick={() => setIsCameraActive(false)} />
            </div>
          )}
          {isScreenShared && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', background: 'var(--ink-850)', padding: '0.35rem 0.6rem', borderRadius: '6px', border: '1px solid var(--gold-500)', fontSize: '0.75rem', color: 'var(--gold-400)' }}>
              <Monitor size={14} /> Screen Capture
              <X size={14} style={{ cursor: 'pointer', marginLeft: '0.25rem' }} onClick={() => setIsScreenShared(false)} />
            </div>
          )}
        </div>
      )}

      <div className="command-foot">
        <div className="chips">
          <button className="chip" onClick={() => setInput("Research the latest papers on autonomous agents")}>research</button>
          <button className="chip" onClick={() => setInput("Recall what I decided about ATLAS last week")}>recall memory</button>
          <button className="chip" onClick={() => { setIsScreenShared(true); setInput("Review my current code for layout bugs"); }}>vision debug</button>
        </div>
        
        <button className="primary" onClick={handleStart} disabled={isPending || (!input.trim() && !isCameraActive && !isScreenShared)}>
          {isPending ? <Loader2 className="w-5 h-5 animate-spin" /> : <ArrowUpRight className="w-5 h-5" />}
          {isPending ? "Starting" : "Start task"}
        </button>
      </div>
    </section>
  );
}
