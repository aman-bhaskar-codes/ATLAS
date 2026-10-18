"use client";
import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ExternalLink, FlaskConical, Code2, Loader2 } from 'lucide-react';
import { launchPlatform, PLATFORM_PATHS } from '@/lib/platforms/navigation';

export function PlatformLauncher() {
  const router = useRouter();
  const [launching, setLaunching] = useState<'research' | 'ide' | null>(null);

  const launch = async (platform: 'research' | 'ide') => {
    try {
      setLaunching(platform);
      const res = await launchPlatform(platform);
      if (res?.redirect_path) {
        router.push(res.redirect_path);
      } else {
        router.push(PLATFORM_PATHS[platform]);
      }
    } catch {
      router.push(PLATFORM_PATHS[platform]);
    } finally {
      setLaunching(null);
    }
  };

  return (
    <section className="platform-launcher panel" aria-label="Platforms">
      <div className="section-head"><h2>Platforms</h2></div>
      <div className="platform-launcher-grid">
        <div>
          <h3><FlaskConical size={18} /> Research</h3>
          <p>Investigate questions, inspect cited answers, and manage your source corpus.</p>
          <button className="primary" onClick={() => launch('research')} disabled={launching !== null}>
            {launching === 'research' ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />} Launch Research
          </button>
        </div>
        <div>
          <h3><Code2 size={18} /> IDE</h3>
          <p>Open a workspace to edit files, run tools, and review agent changes.</p>
          <button className="primary" onClick={() => launch('ide')} disabled={launching !== null}>
            {launching === 'ide' ? <Loader2 size={14} className="animate-spin" /> : <ExternalLink size={14} />} Launch IDE
          </button>
        </div>
      </div>
      <nav className="component-shortcuts" aria-label="Component pages">
        <Link href="/models">Models</Link><Link href="/tools">Tools</Link><Link href="/mcp">MCP</Link><Link href="/memory">Memory</Link><Link href="/approvals">Approvals</Link>
      </nav>
    </section>
  );
}

