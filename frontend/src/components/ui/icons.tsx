// One set of lightweight outline icons and the logo, shared by every screen. Decorative: the words beside them carry the meaning.

import { ReactNode } from "react";

const PATHS: Record<string, ReactNode> = {
  home: <><path d="M3 11.5 12 4l9 7.5" /><path d="M5.5 10v9.5h13V10" /><path d="M10 19.5v-5h4v5" /></>,
  orders: <><rect x="5" y="4" width="14" height="17" rx="2.5" /><path d="M9 4V3h6v1" /><path d="m9 13 2 2 4-4.5" /></>,
  box: <><path d="M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5z" /><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9" /></>,
  boxes: <><path d="M3 10.5 7.5 8l4.5 2.5v5L7.5 18 3 15.5z" /><path d="M12 10.5 16.5 8l4.5 2.5v5L16.5 18 12 15.5z" /><path d="M7.5 8V4.5L12 2l4.5 2.5V8" /></>,
  user: <><circle cx="12" cy="8.5" r="4" /><path d="M4.5 20c.8-3.6 3.7-5.5 7.5-5.5s6.7 1.9 7.5 5.5" /></>,
  users: <><circle cx="9" cy="8.5" r="3.5" /><path d="M2.5 19.5c.7-3.3 3.2-5 6.5-5s5.8 1.7 6.5 5" /><path d="M16 5.2a3.5 3.5 0 0 1 0 6.6M18.5 14.8c1.7.6 2.7 2 3 4.7" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  back: <path d="m14.5 5-7 7 7 7" />,
  chevron: <path d="m9.5 5 7 7-7 7" />,
  chevronDown: <path d="m5 9.5 7 7 7-7" />,
  drop: <path d="M12 3c3.5 4.2 6 7.2 6 10.5a6 6 0 0 1-12 0C6 10.2 8.5 7.2 12 3z" />,
  wheat: <><path d="M12 21V8" /><path d="M12 8c-2.5 0-4-1.5-4-4 2.5 0 4 1.5 4 4zm0 0c2.5 0 4-1.5 4-4-2.5 0-4 1.5-4 4z" /><path d="M12 14c-2.5 0-4-1.5-4-4 2.5 0 4 1.5 4 4zm0 0c2.5 0 4-1.5 4-4-2.5 0-4 1.5-4 4z" /></>,
  cross: <><rect x="4" y="4" width="16" height="16" rx="4" /><path d="M12 8v8M8 12h8" /></>,
  calendar: <><rect x="3.5" y="5" width="17" height="15.5" rx="3" /><path d="M8 3v4M16 3v4M3.5 10h17" /></>,
  shield: <><path d="M12 3 4.5 6v5.5c0 4.7 3 8 7.5 9.5 4.5-1.5 7.5-4.8 7.5-9.5V6z" /><path d="m8.8 12 2.4 2.4 4-4.6" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  building: <><rect x="5" y="3.5" width="14" height="17" rx="2" /><path d="M9 8h2M13 8h2M9 12h2M13 12h2M10 20.5v-4h4v4" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="3" /><path d="m4 7.5 8 5.5 8-5.5" /></>,
  headset: <><path d="M4.5 14v-2a7.5 7.5 0 0 1 15 0v2" /><rect x="3.5" y="13" width="4" height="6" rx="1.5" /><rect x="16.5" y="13" width="4" height="6" rx="1.5" /><path d="M18.5 19c0 1.4-1.5 2-4 2" /></>,
  logout: <><path d="M9.5 4.5h-4A1.5 1.5 0 0 0 4 6v12a1.5 1.5 0 0 0 1.5 1.5h4" /><path d="m15 8 4 4-4 4M19 12H9" /></>,
  save: <><path d="M5 4h11l3 3v13H5z" /><path d="M8 4v5h7V4M8 20v-6h8v6" /></>,
  pin: <><path d="M12 21s-6.5-5.8-6.5-11a6.5 6.5 0 0 1 13 0c0 5.2-6.5 11-6.5 11z" /><circle cx="12" cy="10" r="2.3" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  alert: <><path d="M12 4 2.8 19.5h18.4z" /><path d="M12 10v4.5M12 17.2v.1" /></>,
  truck: <><path d="M2.5 6.5h11v10h-11z" /><path d="M13.5 10h4l3 3v3.5h-7" /><circle cx="7" cy="17.5" r="1.8" /><circle cx="17" cy="17.5" r="1.8" /></>,
  map: <><path d="M3.5 6.5 9 4l6 2.5 5.5-2.5v13.5L15 20l-6-2.5L3.5 20z" /><path d="M9 4v13.5M15 6.5V20" /></>,
  hazard: <><path d="M12 3.5 21 19H3z" /><path d="M12 9.5v4.5M12 16.5v.1" /></>,
  plans: <><rect x="4.5" y="3.5" width="15" height="17" rx="2.5" /><path d="M8.5 8.5h7M8.5 12h7M8.5 15.5h4" /></>,
  evac: <><circle cx="12" cy="5.5" r="2.3" /><path d="M12 9v5l-3 5.5M12 14l3 5.5M7.5 11.5 12 10l4.5 1.5" /></>,
  database: <><ellipse cx="12" cy="6" rx="7.5" ry="3" /><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3" /></>,
  activity: <path d="M3 12h4l2.5-6.5 5 13L17 12h4" />,
  book: <><path d="M5 4.5h11a3 3 0 0 1 3 3v12H8a3 3 0 0 1-3-3z" /><path d="M5 16.5a3 3 0 0 1 3-3h11" /></>,
  flask: <><path d="M9.5 3.5h5M10.5 3.5v6L5 18.5c-.7 1.2.1 2.5 1.5 2.5h11c1.4 0 2.2-1.300 1.500-2.500L13.500 9.500v-6" /><path d="M7.500 15h9" /></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.500 4.500" /></>,
  panel: <><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="M9.500 4.500v15" /></>,
  signal: <><path d="M4 18.500v-3M9 18.500v-6M14 18.500V9M19 18.500V5" /></>,
  wifi: <><path d="M3.500 9.500a12 12 0 0 1 17 0M6.500 12.800a8 8 0 0 1 11 0M9.500 16a4 4 0 0 1 5 0" /><circle cx="12" cy="19" r="0.8" /></>,
  route: <><circle cx="6" cy="18" r="2.2" /><circle cx="18" cy="6" r="2.2" /><path d="M8.200 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.800" /></>,
  play: <path d="M8 5.500v13l11-6.500z" />,
  refresh: <><path d="M20 12a8 8 0 1 1-2.500-5.800" /><path d="M20 4v5h-5" /></>,
  filter: <path d="M4 5.500h16l-6 7.500v5.500l-4-2V13z" />,
  sound: <><path d="M4 9.500v5h3.500L12 18.500v-13L7.500 9.500z" /><path d="M15.500 9a4 4 0 0 1 0 6M18 6.500a8 8 0 0 1 0 11" /></>,
  navigation: <path d="m12 3 7 17.500-7-4-7 4z" />,
};

const ITEM_ICONS: Record<string, string> = { WATER: "drop", RICE: "wheat", PULSES: "wheat", RTE: "wheat", MEDICAL: "cross", ORS: "cross", HYGIENE: "cross" };

export function itemIcon(code: string): string {
  return ITEM_ICONS[code.split("_")[0]] ?? "box";
}

export default function Icon({ name, size = 22 }: { name: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name] ?? PATHS.box}
    </svg>
  );
}

/** The SafePassage shield, used wherever the brand appears. */
export function BrandMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <path d="M16 2.5 5 6.5v8c0 6.8 4.300 11.600 11 14.500 6.700-2.900 11-7.700 11-14.500v-8z" fill="#087F8C" />
      <path d="M10 20c4 0 3-6 6-6s2-4 6-4" fill="none" stroke="#fff" strokeWidth="2.200" strokeLinecap="round" />
      <circle cx="10" cy="20" r="1.900" fill="#fff" />
      <circle cx="22" cy="10" r="1.900" fill="#fff" />
    </svg>
  );
}
