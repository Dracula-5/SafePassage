// A small, flat scene: a relief truck with supplies on a pale landscape. Decorative, carries no data.

export default function ReliefIllustration({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 360 140" aria-hidden="true" focusable="false">
      <circle cx="302" cy="34" r="16" fill="#fde7b0" />
      <path d="M0 108 C60 74 120 88 180 70 S300 60 360 84 V140 H0 Z" fill="#d9f0f6" />
      <path d="M0 122 C80 102 150 116 230 104 S330 100 360 110 V140 H0 Z" fill="#b9e3ec" />
      <rect x="56" y="58" width="30" height="26" rx="3" fill="#d89f4b" />
      <path d="M56 71h30" stroke="#b97f2e" strokeWidth="2" />
      <rect x="90" y="66" width="22" height="18" rx="3" fill="#2aa3b4" />
      <rect x="196" y="48" width="112" height="50" rx="7" fill="#ffffff" stroke="#c9dde6" strokeWidth="1.5" />
      <rect x="208" y="58" width="60" height="26" rx="4" fill="#f5c46b" />
      <path d="M208 71h60" stroke="#dca24c" strokeWidth="2" />
      <path d="M270 62h22l14 16v10h-36z" fill="#087f8c" />
      <path d="M277 67h14l8 9h-22z" fill="#cfeef3" />
      <circle cx="222" cy="102" r="10" fill="#102a43" /><circle cx="222" cy="102" r="4" fill="#cfe3ea" />
      <circle cx="288" cy="102" r="10" fill="#102a43" /><circle cx="288" cy="102" r="4" fill="#cfe3ea" />
    </svg>
  );
}
