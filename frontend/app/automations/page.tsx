"use client";

import React, { useState } from 'react';
import { useAutomations, useCreateAutomation, useDeleteAutomation, useToggleAutomation } from '../../features/autonomy/queries';
import { AUTOMATION_TEMPLATES } from '../../features/autonomy/templates';
import { Automation } from '../../lib/api/contracts';
import { ErrorState } from '@/components/primitives/ErrorState';
import { EmptyState } from '@/components/primitives/EmptyState';
import { Button } from '@/components/primitives/Button';

export default function AutomationsPage() {
  const { data: automations, isLoading, isError, error } = useAutomations(false);
  const toggleMutation = useToggleAutomation();
  const deleteMutation = useDeleteAutomation();
  const createMutation = useCreateAutomation();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<string>('');

  const handleToggle = (id: string, enabled: boolean, auto: Partial<Automation>) => {
    toggleMutation.mutate({ id, enabled: !enabled, auto });
  };

  const handleDelete = (id: string) => {
    if (confirm("Are you sure you want to delete this automation?")) {
      deleteMutation.mutate(id);
    }
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTemplate) return;

    const template = AUTOMATION_TEMPLATES.find(t => t.id === selectedTemplate);
    if (!template) return;

    createMutation.mutate(
      {
        name: template.name,
        description: template.description,
        enabled: true,
        trigger_config: template.trigger_config,
        action_config: template.action_config,
      },
      {
        onSuccess: () => {
          setIsModalOpen(false);
          setSelectedTemplate('');
        }
      }
    );
  };

  return (
    <>
      <div className="crumb mb-6 flex justify-between items-center">
        <div>
          ATLAS / <strong>Automations</strong>
        </div>
        <Button onClick={() => setIsModalOpen(true)}>New Automation</Button>
      </div>

      <section className="panel relative">
        <div className="section-head">
          <h2>Autonomy Fabric Rules</h2>
        </div>

        {isLoading ? (
          <div className="text-[var(--paper-500)] text-sm py-4 px-4">Loading automations...</div>
        ) : isError ? (
          <ErrorState title="Failed to load automations" error={error as Error} />
        ) : automations && automations.length > 0 ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem', textAlign: 'left' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--paper-500)' }}>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Name</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Event Trigger</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Status</th>
                  <th style={{ padding: '0.75rem 1rem', fontWeight: 'normal' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {automations.map((auto) => (
                  <tr key={auto.id} style={{ borderBottom: '1px solid var(--ink-850)', transition: 'background 0.15s ease' }}>
                    <td style={{ padding: '0.75rem 1rem' }}>
                      <div style={{ color: 'var(--paper-100)', fontWeight: 500 }}>{auto.name}</div>
                      <div style={{ color: 'var(--paper-500)', fontSize: '0.8rem', marginTop: '0.2rem' }}>{auto.description}</div>
                    </td>
                    <td style={{ padding: '0.75rem 1rem' }}>
                      <span className="mono bg-[var(--ink-800)] px-2 py-1 rounded text-xs text-[var(--gold-400)]">
                        {auto.trigger_config.event_type}
                      </span>
                    </td>
                    <td style={{ padding: '0.75rem 1rem' }}>
                      <button 
                        onClick={() => handleToggle(auto.id, auto.enabled, auto)}
                        className={`text-xs px-2 py-1 rounded border transition-colors ${
                          auto.enabled 
                            ? 'border-green-500/30 text-green-400 bg-green-500/10 hover:bg-green-500/20' 
                            : 'border-red-500/30 text-red-400 bg-red-500/10 hover:bg-red-500/20'
                        }`}
                      >
                        {auto.enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </td>
                    <td style={{ padding: '0.75rem 1rem' }}>
                      <button 
                        onClick={() => handleDelete(auto.id)}
                        className="text-[var(--paper-500)] hover:text-red-400 transition-colors text-sm"
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState 
            title="No automations configured" 
            description="Create rules to automatically trigger tasks based on system events." 
            action={<Button onClick={() => setIsModalOpen(true)}>Create Automation</Button>}
          />
        )}

        {isModalOpen && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm rounded-lg">
            <div className="bg-[var(--ink-900)] border border-[var(--line)] p-6 rounded-lg shadow-2xl max-w-md w-full">
              <h3 className="text-lg font-medium text-[var(--paper-100)] mb-4">Create New Automation</h3>
              <form onSubmit={handleCreate}>
                <div className="mb-4">
                  <label className="block text-sm text-[var(--paper-300)] mb-2">Select Template</label>
                  <select 
                    className="w-full bg-[var(--ink-800)] border border-[var(--line)] text-[var(--paper-100)] p-2 rounded"
                    value={selectedTemplate}
                    onChange={(e) => setSelectedTemplate(e.target.value)}
                    required
                  >
                    <option value="" disabled>Choose a template...</option>
                    {AUTOMATION_TEMPLATES.map(t => (
                      <option key={t.id} value={t.id}>{t.name} ({t.trigger_config.event_type})</option>
                    ))}
                  </select>
                </div>
                
                {selectedTemplate && (
                  <div className="mb-6 bg-[var(--ink-850)] p-3 rounded border border-[var(--line)]">
                    <p className="text-xs text-[var(--paper-400)] mb-2">
                      {AUTOMATION_TEMPLATES.find(t => t.id === selectedTemplate)?.description}
                    </p>
                    <div className="text-xs text-[var(--paper-500)]">
                      <div><strong className="text-[var(--gold-400)]">When:</strong> {AUTOMATION_TEMPLATES.find(t => t.id === selectedTemplate)?.trigger_config.event_type}</div>
                      <div className="mt-1"><strong className="text-[var(--gold-400)]">Do:</strong> {AUTOMATION_TEMPLATES.find(t => t.id === selectedTemplate)?.action_config.request_template}</div>
                    </div>
                  </div>
                )}

                <div className="flex justify-end gap-3">
                  <button 
                    type="button" 
                    onClick={() => setIsModalOpen(false)}
                    className="px-4 py-2 text-sm text-[var(--paper-300)] hover:text-[var(--paper-100)]"
                  >
                    Cancel
                  </button>
                  <Button type="submit" disabled={!selectedTemplate || createMutation.isPending}>
                    {createMutation.isPending ? 'Creating...' : 'Create Automation'}
                  </Button>
                </div>
              </form>
            </div>
          </div>
        )}
      </section>
    </>
  );
}
