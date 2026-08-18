# Autonomy Fabric

The **Autonomy Fabric** is the event-driven connective tissue of ATLAS. It enables true autonomy by allowing the system to react to events dynamically, rather than relying solely on explicit user commands.

## Core Concepts

1. **MessageBus**: A pub/sub event bus that routes events (like `task.completed`, `webhook.github`, `scheduler.tick`) across the system.
2. **Trigger Engine**: Evaluates incoming events against registered Automation rules.
3. **Automations**: User-defined (or built-in) rules mapping an event to a subsequent action (usually a `task` dispatch).

## How it Works

When an event occurs (e.g., a cron job ticks, a webhook fires, or an agent completes a task), an `Event` is published to the `MessageBus`. The `TriggerEngine` subscribes to a wildcard topic (`*`) and intercepts all events. 

It compares each event against active `Automation` rules. If an event matches a rule's `trigger_config`, the engine dispatches the corresponding `action_config` (typically via the REST API facade or direct internal invocation), creating a continuous, reactive execution loop.

## The Trigger Engine

The Trigger Engine operates completely offline and at zero cost. It uses the `Database` as the source of truth for active automations.

- **Resilience**: The trigger engine is designed to be bulletproof. Malformed payloads or missing fields in events are logged but do not crash the engine.
- **Security**: Actions dispatched by the Trigger Engine specify `"source": "system"` (or `"api"`) and still pass through the ATLAS Safety Engine. Dangerous actions triggered by automations will halt and await user approval (via the `/approvals` UI), preventing run-away loops from executing destructive commands.

## Automation Schema

Automations are stored in the SQLite database and defined via the following structure:

```json
{
  "id": "auto_abcdef123456",
  "name": "Auto-Deploy",
  "description": "Trigger deployment tasks when GitHub webhook fires",
  "enabled": true,
  "trigger_config": {
    "event_type": "webhook.github",
    "filters": {
      "payload.action": "push"
    }
  },
  "action_config": {
    "type": "task",
    "request_template": "Deploy the latest code from the repository."
  }
}
```

## Creating Automations

You can manage automations through the CLI or the Dashboard.

**CLI**:
```bash
atlas automations create --name "System Check" --event "scheduler.tick" --action "Run diagnostics"
atlas automations ls
atlas automations toggle auto_abcdef123456
```

**Dashboard**:
Navigate to the `/automations` page in the Next.js UI to view, create, or disable automations interactively.

## Canonical Webhooks

The Autonomy Fabric provides a unified endpoint for external triggers:
`POST /api/v1/webhooks/{source}`

For example, a GitHub webhook configured to hit `/api/v1/webhooks/github` will emit an event with the topic `webhook.github` on the MessageBus. The Trigger Engine can then capture this and dispatch tasks automatically.
