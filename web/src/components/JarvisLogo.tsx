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
        <linearGradient id={bgId} x1="2" y1="2" x2="62" y2="62" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#132238" />
          <stop offset="100%" stopColor="#09101A" />
        </linearGradient>
        <linearGradient id={arcId} x1="13" y1="13" x2="51" y2="51" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#7DD3FC" />
          <stop offset="100%" stopColor="#2563EB" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="60" height="60" rx="15.5" fill={`url(#${bgId})`} />
      <rect
        x="2.75"
        y="2.75"
        width="58.5"
        height="58.5"
        rx="14.75"
        fill="none"
        stroke="#7DD3FC"
        strokeOpacity="0.18"
        strokeWidth="1.2"
      />
      <path
        d="M 47.5 20.75 A 18.75 18.75 0 1 0 47.5 43.25"
        fill="none"
        stroke={`url(#${arcId})`}
        strokeWidth="4.5"
        strokeLinecap="round"
      />
      <circle cx="32" cy="32" r="5.9" fill="#E0F2FE" />
    </svg>
  );
}
