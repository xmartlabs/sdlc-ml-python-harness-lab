import { defineMessages, useMessages } from "../i18n";

const MESSAGES = defineMessages({
  en: { dismiss: "Dismiss error" },
  es: { dismiss: "Descartar el error" }
});

type ErrorBannerProps = {
  message: string;
  onDismiss?: () => void;
};

export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  const m = useMessages(MESSAGES);
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss ? (
        <button aria-label={m.dismiss} className="error-banner-dismiss" onClick={onDismiss} type="button">
          ×
        </button>
      ) : null}
    </div>
  );
}
