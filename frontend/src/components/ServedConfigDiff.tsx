import { defineMessages, useMessages } from "../i18n";
import type { LocalConfigDiff } from "../types";

const MESSAGES = defineMessages({
  en: {
    heading: "Requested / served",
    field: "Field",
    requested: "Requested",
    served: "Served",
    unsupportedHeading: "Not applied",
    unsupportedBody:
      "These settings are server environment, not a request option — change them by hand and restart the server:",
    matches: "The server loaded exactly what was requested."
  },
  es: {
    heading: "Pedido / servido",
    field: "Campo",
    requested: "Pedido",
    served: "Servido",
    unsupportedHeading: "No aplicado",
    unsupportedBody:
      "Estos ajustes son del entorno del servidor, no una opción del pedido — cambialos a mano y reiniciá el servidor:",
    matches: "El servidor cargó exactamente lo pedido."
  }
});

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "true" : "false";
  }
  return String(value);
}

type ServedConfigDiffProps = {
  diff: LocalConfigDiff[];
  unsupported: string[];
};

/** "You asked for 64k, LM Studio loaded 50k" — only the fields that differ. */
export function ServedConfigDiff({ diff, unsupported }: ServedConfigDiffProps) {
  const m = useMessages(MESSAGES);

  return (
    <div className="served-config-diff">
      <h3>{m.heading}</h3>
      {diff.length === 0 ? (
        <p className="muted small">{m.matches}</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{m.field}</th>
              <th>{m.requested}</th>
              <th>{m.served}</th>
            </tr>
          </thead>
          <tbody>
            {diff.map((row) => (
              <tr key={row.field}>
                <td>{row.field}</td>
                <td>{formatValue(row.requested)}</td>
                <td>{formatValue(row.served)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {unsupported.length > 0 ? (
        <div className="callout callout--warning">
          <div>
            <strong>{m.unsupportedHeading}</strong>
            <p className="muted small">{m.unsupportedBody}</p>
            <ul>
              {unsupported.map((hint) => (
                <li key={hint}>{hint}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </div>
  );
}
