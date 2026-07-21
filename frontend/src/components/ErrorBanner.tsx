type ErrorBannerProps = {
  message: string;
  onDismiss?: () => void;
};

export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onDismiss ? (
        <button aria-label="Dismiss error" className="error-banner-dismiss" onClick={onDismiss} type="button">
          ×
        </button>
      ) : null}
    </div>
  );
}
