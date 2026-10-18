"use client";

import { PlatformLauncher } from "@/components/dashboard/PlatformLauncher";
import { HeroSection } from "@/components/dashboard/HeroSection";
import { CommandWorkspace } from "@/components/workspace/CommandWorkspace";
import { ActivityTimeline } from "@/components/dashboard/ActivityTimeline";
import { ApprovalInbox } from "@/components/dashboard/ApprovalInbox";

export default function Dashboard() {
  return (
    <>
      {/* Top Zone: System Posture */}
      <HeroSection />

      {/* Full Width Zone: Command Composer */}
      <CommandWorkspace />
      <PlatformLauncher />

      {/* Primary & Secondary Zones */}
      <div className="grid-cols-panel">
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
          {/* Primary Zone: Activity */}
          <ActivityTimeline />
        </div>
        
        <div>
          {/* Secondary Zone: Inbox Preview */}
          <ApprovalInbox />
        </div>
      </div>
    </>
  );
}
