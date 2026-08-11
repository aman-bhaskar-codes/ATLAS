import Link from 'next/link';
import { ShieldAlert } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="panel" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: '50vh', textAlign: 'center' }}>
      <ShieldAlert style={{ width: 48, height: 48, color: 'var(--danger-400)', marginBottom: '1rem' }} />
      <h2 style={{ fontSize: '1.5rem', marginBottom: '0.5rem', color: 'var(--paper-100)' }}>404 — Subsystem Offline</h2>
      <p style={{ color: 'var(--paper-500)', marginBottom: '2rem', maxWidth: '400px' }}>
        The requested route does not exist within the active ATLAS interface.
      </p>
      <Link href="/" className="primary" style={{ display: 'inline-flex', width: 'auto' }}>
        Return to Command Center
      </Link>
    </div>
  );
}
