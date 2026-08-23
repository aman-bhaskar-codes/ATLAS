// The legacy dashboard prototype (raw fetch to :8000, light theme) has been
// retired. Its live-metrics role is served by the Home Command Center (/) and
// the Activity view. Redirect so any bookmarked link lands on the real UI.
import { redirect } from "next/navigation";

export default function DashboardPage() {
  redirect("/");
}
