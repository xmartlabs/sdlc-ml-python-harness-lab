import { defineMessages, LANGS, useLang, useMessages } from "../i18n";

const MESSAGES = defineMessages({
  en: { label: "Language", switchTo: "Cambiar a español" },
  es: { label: "Idioma", switchTo: "Switch to English" }
});

/**
 * EN | ES. `compact` is the collapsed sidebar's single button, which shows the
 * language it switches to.
 */
export function LanguageToggle({ compact = false }: { compact?: boolean }) {
  const { lang, setLang } = useLang();
  const m = useMessages(MESSAGES);

  if (compact) {
    const next = lang === "en" ? "es" : "en";
    return (
      <button
        aria-label={m.switchTo}
        className="lang-toggle-compact"
        onClick={() => setLang(next)}
        title={m.switchTo}
        type="button"
      >
        {next.toUpperCase()}
      </button>
    );
  }

  return (
    <div aria-label={m.label} className="segmented lang-toggle" role="group">
      {LANGS.map((code) => (
        <button
          aria-pressed={lang === code}
          className={lang === code ? "active" : ""}
          key={code}
          lang={code}
          onClick={() => setLang(code)}
          type="button"
        >
          {code.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
