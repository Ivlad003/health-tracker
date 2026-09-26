import type { Page } from "../types";

const PATHS: Record<Page, string> = {
  dashboard: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  food: "M7 2v9a3 3 0 0 0 2 2.8V22h2v-8.2A3 3 0 0 0 13 11V2h-2v7h-1V2H9v7H8V2zm10 0c-2 0-3 2.5-3 6v5h2v9h2V2z",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  history: "M12 3a9 9 0 1 0 9 9M12 7v5l3 3M21 3v6h-6",
  profile: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-8 9a8 8 0 0 1 16 0",
  admin: "M12 2l8 3v6c0 5-3.5 9.5-8 11-4.5-1.5-8-6-8-11V5z",
};

const FILLED: ReadonlySet<Page> = new Set<Page>(["dashboard", "food", "admin"]);

/** Filled when active, outlined otherwise (docs/design/README.md, bottom nav). */
export function NavIcon({ page, active }: { page: Page; active: boolean }) {
  const fill = active && FILLED.has(page) ? "currentColor" : "none";
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill={fill} stroke="currentColor"
      strokeWidth={active ? 2.4 : 1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[page]} />
    </svg>
  );
}
