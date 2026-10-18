import { TrainingWorkspace } from '@/components/workspace/TrainingWorkspace';

export const metadata = { title: 'IDE training | ATLAS' };
export default async function TrainingPage({ searchParams }: { searchParams: Promise<{ workspace_id?: string }> }) {
  const query = await searchParams;
  return <TrainingWorkspace initialWorkspace={query.workspace_id ?? ''} />;
}
