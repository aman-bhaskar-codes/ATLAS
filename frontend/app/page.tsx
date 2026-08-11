"use client";

import { HeroSection } from "@/components/dashboard/HeroSection";
import { CommandComposer } from "@/components/dashboard/CommandComposer";
import { ActivityTimeline } from "@/components/dashboard/ActivityTimeline";
import { ApprovalInbox } from "@/components/dashboard/ApprovalInbox";
import { CapabilityPosture } from "@/components/dashboard/CapabilityPosture";

export default function Dashboard() {
  return (
    <>
      <HeroSection />
      <CommandComposer />
      <div className="grid-cols-panel">
        <ActivityTimeline />
        <ApprovalInbox />
      </div>
      <CapabilityPosture />
    </>
  );
}
