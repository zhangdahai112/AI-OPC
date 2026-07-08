import type { SVGProps } from "react";

/**
 * Flat / tech line-icon set (1.6 stroke, rounded joins) replacing the
 * colored emoji used across the nav. All icons inherit currentColor.
 */
export type IconName =
  | "chat" | "folder" | "chart" | "settings"
  | "users" | "layers" | "check" | "ruler"
  | "cpu" | "key" | "plug" | "scale" | "node" | "store";

const P: Record<IconName, string> = {
  chat: "M4 5.5h16v10H9l-4 3.5v-3.5H4z",
  folder: "M3.5 6.5a1 1 0 011-1H10l2 2h6.5a1 1 0 011 1v8a1 1 0 01-1 1h-14a1 1 0 01-1-1z",
  chart: "M4 4v16h16 M8 16v-4 M12 16V8 M16 16v-6",
  settings: "M12 15.5a3.5 3.5 0 100-7 3.5 3.5 0 000 7z M19.4 13a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-2.7 1.1v.2a2 2 0 11-4 0v-.1A1.6 1.6 0 006 17.9l-.1.1a2 2 0 11-2.8-2.8l.1-.1A1.6 1.6 0 002.3 12H2a2 2 0 110-4h.1A1.6 1.6 0 004 5.6l-.1-.1a2 2 0 112.8-2.8l.1.1A1.6 1.6 0 009.5 3h.1a2 2 0 114 0v.1A1.6 1.6 0 0018 5.5l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 001.1 2.7h.2",
  users: "M16 19v-1.5a3 3 0 00-3-3H7a3 3 0 00-3 3V19 M10 11a3 3 0 100-6 3 3 0 000 6 M20 19v-1.5a3 3 0 00-2.2-2.9 M15 5.2a3 3 0 010 5.6",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
  check: "M20 6.5L9.5 17.5 4 12",
  ruler: "M4 8.5l4.5-4.5 12 12L16 20.5zM8 8l1.5 1.5M11 5l1.5 1.5M14 11l1.5 1.5M11 14l1.5 1.5",
  cpu: "M8 8h8v8H8zM9 3v2M15 3v2M9 19v2M15 19v2M3 9h2M3 15h2M19 9h2M19 15h2",
  key: "M15 9a3 3 0 10-3.6 2.94L7 16.3V19h2.7l.9-.9v-1.8h1.8l1.7-1.7A3 3 0 0015 9z",
  plug: "M9 3v5M15 3v5M7 8h10v3a5 5 0 01-10 0zM12 16v5",
  scale: "M12 4v16M6 20h12M12 6l-5 6a3 3 0 006 0zM12 6l5 6a3 3 0 01-6 0",
  node: "M12 3l7.5 4.3v8.6L12 20.2 4.5 15.9V7.3zM12 9.5v5M8.5 7.7l3.5 1.8 3.5-1.8",
  store: "M4 9.5h16V20H4zM4 9.5L5.5 5h13L20 9.5M4 9.5a2 2 0 004 0 2 2 0 004 0 2 2 0 004 0 2 2 0 004 0M10 20v-5h4v5",
};

const CLOSED = new Set<IconName>(["chat", "folder", "layers", "cpu", "node"]);

export default function Icon({
  name,
  size = 16,
  ...rest
}: { name: IconName; size?: number } & Omit<SVGProps<SVGSVGElement>, "name">) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      <path d={P[name]} />
    </svg>
  );
}

export { CLOSED };
