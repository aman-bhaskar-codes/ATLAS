"use client";
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const researchLinks = [
  ['/research', 'Overview'], ['/research/history', 'History'],
  ['/research/corpus', 'Corpus'], ['/research/status', 'Status'],
];
const ideLinks = [['/workspaces', 'Workspaces'], ['/ide/training', 'Training'], ['/tools', 'Tools'], ['/models', 'Models'], ['/approvals', 'Approvals']];

/** Platform-owned navigation; command-centre controls never nest inside it. */
export function PlatformShell({ platform, children }: { platform: 'research' | 'ide'; children: React.ReactNode }) {
  const pathname = usePathname();
  const links = platform === 'research' ? researchLinks : ideLinks;
  return (
    <div className="platform-shell" data-platform={platform}>
      <header className="platform-header">
        <Link href={platform === 'research' ? '/research' : '/workspaces'} className="platform-brand">ATLAS <strong>{platform === 'research' ? 'Research' : 'IDE'}</strong></Link>
        <Link href="/" className="ghost-btn">Return to command centre</Link>
      </header>
      <nav className="platform-nav" aria-label={`${platform === 'research' ? 'Research' : 'IDE'} navigation`}>
        {links.map(([href, label]) => <Link key={href} href={href} aria-current={pathname === href ? 'page' : undefined}>{label}</Link>)}
      </nav>
      <main className="platform-content">{children}</main>
    </div>
  );
}
