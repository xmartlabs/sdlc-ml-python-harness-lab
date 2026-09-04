/**
 * The icon set, inline.
 *
 * Lucide's geometry (ISC licensed, https://lucide.dev) drawn as local SVG
 * rather than pulled in as a dependency: the app ships a handful of icons and
 * the package is a few hundred, so this keeps the bundle honest and keeps the
 * stroke weight under our own control.
 *
 * Icons replace the emoji that used to carry the navigation. Emoji render
 * differently on every platform, ignore the theme, and cannot be given a
 * semantic colour — none of which a tool that reports evidence can afford.
 */

import type { SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Svg({ size = 16, children, ...rest }: IconProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

export const IconHome = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 10.5 12 3l9 7.5" />
    <path d="M5 9.5V21h14V9.5" />
  </Svg>
);

export const IconFlask = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 3h6" />
    <path d="M10 3v6.5L4.5 18a2 2 0 0 0 1.7 3h11.6a2 2 0 0 0 1.7-3L14 9.5V3" />
    <path d="M6.7 14h10.6" />
  </Svg>
);

export const IconPlay = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7 4.5 19 12 7 19.5z" />
  </Svg>
);

export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
);

export const IconLightbulb = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 18h6" />
    <path d="M10 21h4" />
    <path d="M12 3a6 6 0 0 0-3.6 10.8c.6.5.9 1.1 1 1.7l.1.5h5l.1-.5c.1-.6.4-1.2 1-1.7A6 6 0 0 0 12 3z" />
  </Svg>
);

export const IconFiles = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z" />
    <path d="M13 3v6h6" />
  </Svg>
);

export const IconRepo = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H19v14H6.5A2.5 2.5 0 0 0 4 19.5z" />
    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H19v4H6.5A2.5 2.5 0 0 1 4 19.5z" />
  </Svg>
);

export const IconPuzzle = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9 4a2 2 0 1 1 4 0v1h3a1 1 0 0 1 1 1v3h1a2 2 0 1 1 0 4h-1v3a1 1 0 0 1-1 1h-3v-1a2 2 0 1 0-4 0v1H6a1 1 0 0 1-1-1v-3H4a2 2 0 1 1 0-4h1V6a1 1 0 0 1 1-1h3z" />
  </Svg>
);

export const IconList = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 6h12M8 12h12M8 18h12" />
    <path d="M4 6h.01M4 12h.01M4 18h.01" />
  </Svg>
);

export const IconCompare = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 8h13M14 5l3 3-3 3" />
    <path d="M20 16H7M10 13l-3 3 3 3" />
  </Svg>
);

export const IconTrophy = (p: IconProps) => (
  <Svg {...p}>
    <path d="M7 4h10v5a5 5 0 0 1-10 0z" />
    <path d="M7 6H4v1a4 4 0 0 0 3.5 4M17 6h3v1a4 4 0 0 1-3.5 4" />
    <path d="M10 14h4l.5 4h-5z" />
    <path d="M8 21h8" />
  </Svg>
);

export const IconPulse = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 12h4l2.5-7 4 14L16 12h5" />
  </Svg>
);

export const IconBook = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H19v16H5.5A1.5 1.5 0 0 0 4 20.5z" />
    <path d="M4 20.5A1.5 1.5 0 0 1 5.5 19H19v2H5.5" />
    <path d="M8 7.5h7M8 11h7" />
  </Svg>
);

export const IconSettings = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.5v2.2M12 19.3v2.2M4.2 7.2l1.9 1.1M17.9 15.7l1.9 1.1M4.2 16.8l1.9-1.1M17.9 8.3l1.9-1.1" />
  </Svg>
);

export const IconSearch = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.6-3.6" />
  </Svg>
);

export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10.3 3.9 2.6 17.2A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.8L13.7 3.9a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4M12 16.5h.01" />
  </Svg>
);

export const IconInfo = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5M12 7.8h.01" />
  </Svg>
);

export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="m4.5 12.5 5 5 10-11" />
  </Svg>
);

export const IconX = (p: IconProps) => (
  <Svg {...p}>
    <path d="m6 6 12 12M18 6 6 18" />
  </Svg>
);

export const IconChevronLeft = (p: IconProps) => (
  <Svg {...p}>
    <path d="m15 6-6 6 6 6" />
  </Svg>
);

export const IconChevronRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="m9 6 6 6-6 6" />
  </Svg>
);

export const IconSparkles = (p: IconProps) => (
  <Svg {...p}>
    <path d="M12 3.5 13.9 9l5.6 1.9-5.6 1.9L12 18.5l-1.9-5.7-5.6-1.9L10.1 9z" />
    <path d="M19 2.5v3M17.5 4h3" />
    <path d="M5 17.5v3M3.5 19h3" />
  </Svg>
);

export const IconArrowRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 12h15M13 6l6 6-6 6" />
  </Svg>
);

export const IconTrendUp = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 17 9.5 10.5l4 4L21 7" />
    <path d="M15 7h6v6" />
  </Svg>
);

export const IconTrendDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 7 9.5 13.5l4-4L21 17" />
    <path d="M15 17h6v-6" />
  </Svg>
);

export const IconMinus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M5 12h14" />
  </Svg>
);

export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5V12l3 2" />
  </Svg>
);

export const IconLayers = (p: IconProps) => (
  <Svg {...p}>
    <path d="m12 3 8.5 4.5L12 12 3.5 7.5z" />
    <path d="m4 12.5 8 4.3 8-4.3M4 17l8 4.3 8-4.3" />
  </Svg>
);
