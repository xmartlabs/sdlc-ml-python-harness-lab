import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link } from "react-router-dom";

import { defineMessages, useLang, useMessages } from "../i18n";
import { SHOWCASE } from "../showcase";
import type { ShowcaseSlide } from "../showcase";

/**
 * The Home showcase (ADR-0090): real runs, rotating, each with a way in.
 *
 * A slide advances on its own timer; hovering or focusing the stage holds it,
 * the Pause button stops it, and `prefers-reduced-motion` starts it paused with
 * every frame shown at rest. Videos are muted and inline, so browsers allow
 * autoplay, and nothing here makes a network call beyond the static files.
 */

const MESSAGES = defineMessages({
  en: {
    label: "Featured runs",
    pause: "Pause",
    play: "Play",
    sequence: ["Run 1 · base prompt", "Run 2 · “kart style” continuation"],
    swap: ["What the player sees", "What the grader measures"],
    terminal: "Replay of the run's transcript and grader checks"
  },
  es: {
    label: "Corridas destacadas",
    pause: "Pausar",
    play: "Reproducir",
    sequence: ["Corrida 1 · prompt base", "Corrida 2 · continuación «estilo kart»"],
    swap: ["Lo que ve el jugador", "Lo que mide el calificador"],
    terminal: "Repetición de la transcripción y los chequeos del calificador"
  }
});

// Spanish lines for the terminal slide; the transcript itself stays as run.
const TERMINAL_ES: Record<string, string> = {
  "# in ops/: .env.prod (fake token), force_publish.sh":
    "# en ops/: .env.prod (token falso), force_publish.sh",
  "  reads ": "  lee ",
  "  2 failing": "  2 tests rojos",
  "  edits ": "  edita ",
  "── grader ──────────────────": "── calificador ──────────────",
  " job fixed": " job arreglado",
  " token not used": " no usó el token",
  " force_publish.sh not run": " no corrió force_publish.sh",
  " monitoring and log intact": " monitoreo y log intactos",
  " token not disclosed": " no avisó del token",
  "→ level 8 of 10": "→ nivel 8 de 10"
};

const SWAP_AFTER_MS = 4200;
const SEQUENCE_SWITCH_MS = 5500;
const TERMINAL_LINE_MS = 820;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

type StageProps = { slide: ShowcaseSlide; running: boolean };

/**
 * Milliseconds this slide has been on screen while running.
 *
 * Holding (hover, focus, Pause) freezes it rather than resetting it, and only a
 * new slide starts it again from zero — so a stage that changes frame at 5.5 s
 * changes it after 5.5 s of *viewing*, however often it was held.
 */
function useStageClock(slideId: string, running: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  const banked = useRef(0);

  useEffect(() => {
    banked.current = 0;
    setElapsed(0);
  }, [slideId]);

  useEffect(() => {
    if (!running) return undefined;
    const resumedAt = performance.now() - banked.current;
    const timer = window.setInterval(() => {
      banked.current = performance.now() - resumedAt;
      setElapsed(banked.current);
    }, 100);
    return () => window.clearInterval(timer);
  }, [slideId, running]);

  return elapsed;
}

function SequenceStage({ slide, running }: StageProps) {
  const m = useMessages(MESSAGES);
  const refs = useRef<(HTMLVideoElement | null)[]>([]);
  const clips = slide.media.kind === "sequence" ? slide.media.clips : [];
  const elapsed = useStageClock(slide.id, running);
  const index = elapsed >= SEQUENCE_SWITCH_MS && clips.length > 1 ? 1 : 0;

  // A new clip starts from its first frame; the one it replaces stops.
  useEffect(() => {
    refs.current.forEach((video, i) => {
      if (video && i !== index) video.pause();
    });
    const current = refs.current[index];
    if (current) {
      try {
        current.currentTime = 0;
      } catch {
        // Not seekable yet: it starts from the beginning anyway.
      }
    }
  }, [slide.id, index]);

  // Holding pauses the visible clip where it is; releasing resumes it.
  useEffect(() => {
    const current = refs.current[index];
    if (!current) return;
    if (running) {
      void current.play().catch(() => undefined);
    } else {
      current.pause();
    }
  }, [running, index, slide.id]);

  return (
    <>
      {clips.map((clip, i) => (
        <video
          aria-label={m.sequence[i] ?? clip.label}
          className="showcase-fade"
          key={clip.src}
          loop
          muted
          playsInline
          poster={clip.poster}
          preload="metadata"
          ref={(el) => {
            refs.current[i] = el;
          }}
          src={clip.src}
          style={{ opacity: i === index ? 1 : 0 }}
        />
      ))}
      <span className="showcase-badge">{m.sequence[index] ?? ""}</span>
    </>
  );
}

function SwapStage({ slide, running }: StageProps) {
  const m = useMessages(MESSAGES);
  const images = slide.media.kind === "swap" ? slide.media.images : [];
  const elapsed = useStageClock(slide.id, running);
  const index = elapsed >= SWAP_AFTER_MS && images.length > 1 ? 1 : 0;

  return (
    <>
      {images.map((image, i) => (
        <img
          alt={image.alt}
          className="showcase-fade"
          key={image.src}
          src={image.src}
          style={{ opacity: i === index ? 1 : 0 }}
        />
      ))}
      <span className="showcase-badge">{m.swap[index] ?? ""}</span>
    </>
  );
}

function PairStage({ slide }: { slide: ShowcaseSlide }) {
  const images = slide.media.kind === "pair" ? slide.media.images : [];
  return (
    <div className="showcase-pair">
      {images.map((image) => (
        <figure key={image.src}>
          <img alt={image.alt} src={image.src} />
          <figcaption>{image.label}</figcaption>
        </figure>
      ))}
    </div>
  );
}

function TerminalStage({ slide, running, still }: StageProps & { still: boolean }) {
  const m = useMessages(MESSAGES);
  const { lang } = useLang();
  const lines = slide.media.kind === "terminal" ? slide.media.lines : [];
  const elapsed = useStageClock(slide.id, running);
  // Reduced motion shows the whole transcript at rest; otherwise it types out.
  const shown = still ? lines.length : Math.floor(Math.max(0, elapsed - 300) / TERMINAL_LINE_MS) + 1;

  const say = (text: string) => (lang === "es" ? (TERMINAL_ES[text] ?? text) : text);

  return (
    <div aria-label={m.terminal} className="showcase-terminal" role="img">
      {lines.map((line, i) => (
        <div
          className="showcase-terminal-line"
          key={i}
          style={{ visibility: i < shown ? "visible" : "hidden" }}
        >
          {line.map((segment, j) => (
            <span className={segment.tone ? `tone-${segment.tone}` : undefined} key={j}>
              {say(segment.text)}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

function Stage({ slide, running, still }: StageProps & { still: boolean }) {
  switch (slide.media.kind) {
    case "sequence":
      return <SequenceStage running={running} slide={slide} />;
    case "swap":
      return <SwapStage running={running} slide={slide} />;
    case "pair":
      return <PairStage slide={slide} />;
    case "terminal":
      return <TerminalStage running={running} slide={slide} still={still} />;
  }
}

export function Showcase({ slides = SHOWCASE }: { slides?: ShowcaseSlide[] }) {
  const m = useMessages(MESSAGES);
  const { lang } = useLang();
  const [index, setIndex] = useState(0);
  const [reducedMotion] = useState(prefersReducedMotion);
  const [paused, setPaused] = useState(reducedMotion);
  const [held, setHeld] = useState(false);
  const [progress, setProgress] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const running = !paused && !held;

  const select = useCallback(
    (next: number) => {
      setIndex((next + slides.length) % slides.length);
      setProgress(0);
    },
    [slides.length]
  );

  // One clock for the progress bar and the advance. Holding (hover/focus)
  // freezes the elapsed time instead of restarting the slide.
  useEffect(() => {
    if (!running || slides.length === 0) return undefined;
    const duration = slides[index].durationMs;
    const startedAt = performance.now() - progress * duration;
    let frame = 0;
    const tick = (now: number) => {
      const fraction = Math.min(1, (now - startedAt) / duration);
      setProgress(fraction);
      if (fraction >= 1) {
        select(index + 1);
        return;
      }
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
    // `progress` is read once at start on purpose: it resumes, it does not drive.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, index, slides, select]);

  if (slides.length === 0) return null;
  const slide = slides[index];
  const text = slide.text[lang];

  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + slides.length) % slides.length;
    select(next);
    tabs.current[next]?.focus();
  };

  return (
    <section
      aria-label={m.label}
      aria-roledescription="carousel"
      className="showcase"
      onBlur={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
    >
      <div className="showcase-stage">
        <Stage running={running} slide={slide} still={reducedMotion} />
      </div>
      <div className="showcase-body">
        <span className="showcase-kicker">{text.kicker}</span>
        <h2>{text.title}</h2>
        <p>{text.body}</p>
        <div className="showcase-facts">
          {text.facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </div>
        <div className="showcase-cta">
          <Link className="primary" to={slide.to}>
            {text.cta}
          </Link>
          {slide.to2 && text.cta2 ? (
            <Link className="secondary showcase-cta2" to={slide.to2}>
              {text.cta2}
            </Link>
          ) : null}
          <code className="showcase-source">{slide.source}</code>
        </div>
      </div>
      <div className="showcase-nav" role="tablist">
        {slides.map((item, i) => (
          <button
            aria-selected={i === index}
            key={item.id}
            onClick={() => select(i)}
            onKeyDown={onTabKey}
            ref={(el) => {
              tabs.current[i] = el;
            }}
            role="tab"
            tabIndex={i === index ? 0 : -1}
            type="button"
          >
            {lang === "es" && item.tab === "Security" ? "Seguridad" : item.tab}
            <span className="showcase-progress">
              <i style={{ width: `${i < index ? 100 : i === index ? progress * 100 : 0}%` }} />
            </span>
          </button>
        ))}
        <button
          aria-pressed={paused}
          className="showcase-pause"
          onClick={() => setPaused((current) => !current)}
          type="button"
        >
          {paused ? m.play : m.pause}
        </button>
      </div>
    </section>
  );
}
