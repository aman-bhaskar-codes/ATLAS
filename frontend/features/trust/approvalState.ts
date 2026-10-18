export function canApproveOnce(
  approval: { tier: number; status: string; expires_at: string; code_required?: boolean; confirmation_code?: string | null },
  reviewed: boolean,
  code: string,
  now: number,
): boolean {
  if (!reviewed || approval.status !== 'pending' || approval.tier >= 4 || Date.parse(approval.expires_at) <= now) return false;
  if (approval.code_required || approval.tier >= 3) return Boolean(approval.confirmation_code) && code.trim() === approval.confirmation_code;
  return true;
}
