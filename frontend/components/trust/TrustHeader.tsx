import React from 'react';
import { ShieldCheck, Brain, ListChecks, ScrollText } from 'lucide-react';
import Link from 'next/link';

export function TrustHeader({ active }: { active: 'tasks' | 'approvals' | 'memory' | 'audit' }) {
  const tabs = [
    { id: 'tasks', label: 'Tasks', icon: ListChecks, href: '/tasks' },
    { id: 'approvals', label: 'Approvals', icon: ShieldCheck, href: '/approvals' },
    { id: 'memory', label: 'Memory', icon: Brain, href: '/memory' },
    { id: 'audit', label: 'Audit Log', icon: ScrollText, href: '/audit' },
  ];

  return (
    <div className="flex gap-1 border-b border-[var(--line)] mb-6 overflow-x-auto hide-scrollbar">
      {tabs.map((t) => {
        const Icon = t.icon;
        const isActive = active === t.id;
        return (
          <Link 
            key={t.id}
            href={t.href}
            className={`flex items-center gap-2 px-4 py-3 text-sm transition-colors relative ${isActive ? 'text-[var(--paper-100)]' : 'text-[var(--paper-500)] hover:text-[var(--paper-300)]'}`}
          >
            <Icon size={16} />
            <span>{t.label}</span>
            {isActive && (
              <div className="absolute bottom-[-1px] left-0 right-0 h-[2px] bg-[var(--gold-400)]" />
            )}
          </Link>
        );
      })}
    </div>
  );
}
