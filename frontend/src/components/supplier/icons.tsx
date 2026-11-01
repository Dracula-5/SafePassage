// The icon set and logo live in components/ui, so every screen shares one. This keeps the supplier imports short.

export { BrandMark, default, itemIcon } from "../ui/icons";

/** A simple road-relief truck for the hero card. Drawn, not photographed: it carries no data. */
export function TruckArt() {
  return (
    <svg viewBox="0 0 200 110" aria-hidden="true" focusable="false">
      <ellipse cx="100" cy="98" rx="86" ry="7" fill="#04424c" opacity=".35" />
      <rect x="14" y="26" width="104" height="58" rx="8" fill="#f2c26b" />
      <path d="M14 46h104M14 62h104" stroke="#d9a24a" strokeWidth="2" />
      <path d="M118 42h34l22 22v20h-56z" fill="#ffffff" />
      <path d="M126 48h22l14 15h-36z" fill="#9fd8e6" />
      <rect x="12" y="80" width="170" height="8" rx="3" fill="#e4edf4" />
      <circle cx="46" cy="90" r="11" fill="#12384a" /><circle cx="46" cy="90" r="4.5" fill="#cfe3ea" />
      <circle cx="146" cy="90" r="11" fill="#12384a" /><circle cx="146" cy="90" r="4.5" fill="#cfe3ea" />
      <path d="M62 52h30M62 66h20" stroke="#fff" strokeOpacity=".7" strokeWidth="5" strokeLinecap="round" />
    </svg>
  );
}
