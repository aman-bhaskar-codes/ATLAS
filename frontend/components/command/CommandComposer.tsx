import React, { useState } from "react";
import { Button } from "../primitives/Button";
import { Panel } from "../primitives/Panel";
import { Send, Terminal } from "lucide-react";

interface CommandComposerProps {
  onSubmit: (request: string) => void;
  isLoading: boolean;
}

export function CommandComposer({ onSubmit, isLoading }: CommandComposerProps) {
  const [request, setRequest] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!request.trim() || isLoading) return;
    onSubmit(request.trim());
    setRequest("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <Panel>
      <form onSubmit={handleSubmit} className="relative flex items-end gap-3">
        <div className="absolute top-3 left-3 text-[var(--color-paper-500)]">
          <Terminal className="w-5 h-5" />
        </div>
        <textarea
          value={request}
          onChange={(e) => setRequest(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Command ATLAS... (Press Enter to submit)"
          className="w-full bg-[var(--color-ink-950)] border border-[var(--color-line)] rounded-[var(--radius-sm)] py-3 pl-10 pr-3 text-[var(--color-paper-100)] placeholder:text-[var(--color-paper-500)] focus:outline-none focus:border-[var(--color-royal-500)] focus:ring-1 focus:ring-[var(--color-royal-500)] resize-none min-h-[48px] max-h-[200px]"
          rows={1}
          disabled={isLoading}
        />
        <Button 
          type="submit" 
          disabled={!request.trim() || isLoading}
          isLoading={isLoading}
          className="shrink-0 h-[48px] px-6"
        >
          {!isLoading && <Send className="w-4 h-4 mr-2" />}
          Execute
        </Button>
      </form>
    </Panel>
  );
}
