import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

/**
 * The interface language (ADR-0070): English or Spanish, switched from the
 * sidebar and remembered per browser.
 *
 * Every component keeps its own strings next to its markup in a
 * `defineMessages({ en, es })` block. The Spanish block must have exactly the
 * English block's keys and shapes, so a string added in one language and not
 * the other fails `make check-frontend` instead of shipping half-translated.
 *
 * Only the interface is translated. Text the backend composes — verdicts,
 * readings, check results — and the markdown guides stay in English for now.
 */

export type Lang = "en" | "es";

export const LANGS: readonly Lang[] = ["en", "es"];

const STORAGE_KEY = "ahl.lang";

function readStoredLang(): Lang {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "es") {
      return stored;
    }
  } catch {
    // Storage blocked (private window, disabled site data): fall through.
  }
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("es")) {
    return "es";
  }
  return "en";
}

// The language for helpers that run during render but outside a component
// (date formatting, label builders). Kept in step with the provider's state.
let current: Lang = typeof window === "undefined" ? "en" : readStoredLang();

type LanguageContextValue = { lang: Lang; setLang: (lang: Lang) => void };

const LanguageContext = createContext<LanguageContextValue>({
  lang: current,
  setLang: () => undefined
});

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(current);
  current = lang;

  useEffect(() => {
    document.documentElement.lang = lang;
    try {
      window.localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      // Not remembered across visits; the switch still works for this one.
    }
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang }), [lang]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

/** The current language and its setter. */
export function useLang(): LanguageContextValue {
  return useContext(LanguageContext);
}

/**
 * Both languages of one component's strings. `es` is checked against `en`:
 * same keys, same nesting, same function signatures for strings that take
 * values (`(count: number) => string`).
 */
export function defineMessages<T>(messages: { en: T; es: NoInfer<T> }): Record<Lang, T> {
  return messages;
}

/** The current language's strings, inside a component. */
export function useMessages<T>(messages: Record<Lang, T>): T {
  return messages[useLang().lang];
}

/** The current language's strings, in a helper called during render. */
export function pickMessages<T>(messages: Record<Lang, T>): T {
  return messages[current];
}

/** The current language, in a helper called during render. */
export function currentLang(): Lang {
  return current;
}
