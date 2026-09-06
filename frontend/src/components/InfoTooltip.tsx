import { useState } from "react";

import { IconInfo } from "./icons";

type InfoTooltipProps = {
  text: string;
};

/**
 * An info icon that reveals a short explanation on hover, focus, or click.
 *
 * The native `title` attribute is not enough here: it waits about a second
 * before appearing, cannot be reached by keyboard, and never shows on touch.
 */
export function InfoTooltip({ text }: InfoTooltipProps) {
  const [open, setOpen] = useState(false);

  return (
    <span className="tooltip-anchor">
      <button
        aria-label="What does this option do?"
        className="tooltip-trigger"
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          // These triggers sit inside a <label>, where an unhandled click
          // would toggle the very checkbox the tooltip is explaining.
          event.preventDefault();
          setOpen((value) => !value);
        }}
        onFocus={() => setOpen(true)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        type="button"
      >
        <IconInfo size={14} />
      </button>
      {open ? (
        <span className="tooltip-bubble" role="tooltip">
          {text}
        </span>
      ) : null}
    </span>
  );
}
