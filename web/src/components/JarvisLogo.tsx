import { useId } from "react";

interface JarvisLogoProps {
  size?: number;
  className?: string;
}

export function JarvisLogo({ size = 28, className }: JarvisLogoProps) {
  const uid = useId().replace(/:/g, "");
  const bgId = `j-bg-${uid}`;
  const arcId = `j-arc-${uid}`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 64 64"
      width={size}
      height={size}
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <linearGradient id={bgId} x1="6" y1="4" x2="58" y2="60" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#132238" />
          <stop offset="100%" stopColor="#09101A" />
        </linearGradient>
        <linearGradient id={arcId} x1="14" y1="14" x2="50" y2="50" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#7DD3FC" />
          <stop offset="100%" stopColor="#2563EB" />
        </linearGradient>
      </defs>
      <rect x="4" y="4" width="56" height="56" rx="15" fill={`url(#${bgId})`} />
      <rect
        x="4.75"
        y="4.75"
        width="54.5"
        height="54.5"
        rx="14.25"
        fill="none"
        stroke="#7DD3FC"
        strokeOpacity="0.18"
        strokeWidth="1.2"
      />
      <path
        d="M 46.5 21.5 A 17.5 17.5 0 1 0 46.5 42.5"
        fill="none"
        stroke={`url(#${arcId})`}
        strokeWidth="4.2"
        strokeLinecap="round"
      />
      <circle cx="32" cy="32" r="5.5" fill="#E0F2FE" />
    </svg>
  );
}
