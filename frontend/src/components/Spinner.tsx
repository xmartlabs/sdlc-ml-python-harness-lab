type SpinnerProps = {
  label?: string;
};

export function Spinner({ label }: SpinnerProps) {
  return (
    <span className="spinner-row" role="status">
      <span className="spinner" />
      {label ? <span className="muted">{label}</span> : null}
    </span>
  );
}
