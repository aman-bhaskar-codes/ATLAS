import { ActionConfigSchema, TriggerConfigSchema } from "../../lib/api/contracts";
import { z } from "zod";

export interface AutomationTemplate {
  id: string;
  name: string;
  description: string;
  trigger_config: z.infer<typeof TriggerConfigSchema>;
  action_config: z.infer<typeof ActionConfigSchema>;
}

export const AUTOMATION_TEMPLATES: AutomationTemplate[] = [
  {
    id: "system-check",
    name: "Scheduled System Check",
    description: "Runs a diagnostic task on every scheduler tick.",
    trigger_config: {
      event_type: "scheduler.tick",
      filters: {},
    },
    action_config: {
      type: "task",
      request_template: "Perform a system diagnostic and report any degraded services.",
    },
  },
  {
    id: "error-remediation",
    name: "Error Remediation",
    description: "Spawns an agent to analyze task failures.",
    trigger_config: {
      event_type: "task.failed",
      filters: {},
    },
    action_config: {
      type: "task",
      request_template: "Task {{ payload.task_id }} failed. Analyze the logs and suggest a fix.",
    },
  },
  {
    id: "knowledge-consolidation",
    name: "Knowledge Consolidation",
    description: "Summarizes recent events into semantic memory.",
    trigger_config: {
      event_type: "memory.consolidate_requested",
      filters: {},
    },
    action_config: {
      type: "task",
      request_template: "Consolidate the latest trajectory data into semantic memory facts.",
    },
  },
  {
    id: "custom-webhook",
    name: "Custom Webhook",
    description: "Trigger an action when a webhook event is received.",
    trigger_config: {
      event_type: "webhook.received",
      filters: {
        "payload.source": "github"
      },
    },
    action_config: {
      type: "task",
      request_template: "Handle GitHub webhook: {{ payload.action }}",
    },
  }
];
