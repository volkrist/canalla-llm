import { detailRows, stateText, type SubsystemStatus } from "../lib/status";
import {
  parseTorDetails,
  torBinaryText,
  torEnsureText,
  torMissingBinary,
} from "../lib/tor";

/** The Tor service as Settings shows it: health first, then the policy and the proof rows, then the
 *  one manual action. It renders the same rows as the Tor chip's popover from one `/status` answer,
 *  plus the binary a user can act on.
 *
 *  Pure on purpose: it takes the snapshot and a callback, so the whole block is renderable without
 *  a session, a network call or a mount effect.
 */
export default function TorServiceBlock({
  status,
  busy,
  onEnsure,
}: {
  status: SubsystemStatus | null;
  busy: boolean;
  onEnsure: () => void;
}) {
  const details = parseTorDetails(status?.details);
  const rows = status ? detailRows("tor", status) : [];
  // The dependency note is only honest when the service is down *and* the backend said there is no
  // binary to run. A payload that simply did not answer never claims a missing dependency.
  const dependency =
    status?.state === "unavailable" && torMissingBinary(details);
  return (
    <div className="tor-service" data-testid="tor-service">
      <p className="tor-service-health" data-testid="tor-service-health">
        <strong>Служба Tor</strong>{" "}
        {status
          ? `${stateText("tor", status.state)} · ${status.message}`
          : "Проверка…"}
      </p>
      {status ? (
        <dl className="tor-service-rows">
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
          <div>
            <dt>Файл Tor</dt>
            <dd data-testid="tor-service-binary">{torBinaryText(details)}</dd>
          </div>
        </dl>
      ) : null}
      <button
        type="button"
        className="tor-service-action"
        data-testid="tor-service-ensure"
        disabled={busy}
        onClick={onEnsure}
      >
        {torEnsureText(details)}
      </button>
      {dependency ? (
        <p className="field-help" data-testid="tor-dependency">
          Tor Browser — обязательная зависимость: установите Tor Browser или
          укажите путь к tor.exe в TOR_BINARY_PATH (backend .env). Canalla LLM
          не скачивает Tor и не подменяет его другим источником.
        </p>
      ) : null}
    </div>
  );
}
