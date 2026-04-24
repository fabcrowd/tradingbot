import type { ExchangeErrorEvent, ScalpSnapshot, Snapshot } from "../lib/types";

type Props = {
  snapshot: Snapshot | null;
  scalp: ScalpSnapshot | null;
  connected: boolean;
  onOpenLogs: (opts?: { focusExchangeId?: string }) => void;
};

function tsShort(epoch: number): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function FlightDeck({ snapshot, scalp, connected, onOpenLogs }: Props) {
  const mode = snapshot?.mode ?? "—";
  const modeLc = String(mode).toLowerCase();
  const se = scalp?.enabled;
  const sim = scalp?.sim_mode;
  const phaseRaw = scalp?.startup_phase ?? scalp?.operator?.startup_phase ?? "—";
  const phaseLc = String(phaseRaw).toLowerCase();
  const legRows = Object.values(scalp?.trader?.open_positions ?? {});
  const legCount = legRows.length;
  const pendingLegs = legRows.filter((p) => String(p.status ?? "").toLowerCase() === "pending").length;
  const filledLegs = legRows.filter((p) => String(p.status ?? "").toLowerCase() === "open").length;
  const legsSubtitle =
    legCount === 0
      ? ""
      : pendingLegs > 0 && filledLegs > 0
        ? ` · ${pendingLegs} pending · ${filledLegs} filled`
        : pendingLegs > 0
          ? ` · ${pendingLegs} pending (not on Coinbase Positions yet)`
          : ` · ${filledLegs} filled`;
  const legsTitle =
    "Scalp internal legs (pending unfilled entries + open/filled positions). " +
    "Coinbase “Positions” only lists contracts you hold after an entry fills — resting entry limits and working stops/TPs are under Orders. " +
    "If this count disagrees with the exchange, check for stale pending entries in Terminal open orders or restart after a crash.";
  const daily = scalp?.trader?.daily_pnl ?? 0;
  const venue = scalp?.venue ?? "—";
  const venueDisplay =
    venue === "coinbase_perps" ? "CDE PERPS" : venue === "—" ? "—" : String(venue).replace(/_/g, " ").toUpperCase();
  const venueTitle =
    venue === "coinbase_perps"
      ? "Coinbase Derivatives nano perpetuals. Config key coinbase_perps; product_ids may include a far date (e.g. XPP-20DEC30-CDE) — those are still perp instruments on CDE, not a separate futures product in this bot."
      : "Configured execution venue for scalp.";
  const unacked = snapshot?.exchange_errors?.filter((e: ExchangeErrorEvent) => !e.acknowledged) ?? [];
  const lastErr = unacked.length ? unacked[unacked.length - 1] : null;

  const engineLive = modeLc === "live";
  const enginePaper = modeLc === "paper";
  const scalpLiveArmed = se === true && phaseLc === "live" && sim !== true;
  const scalpSimArmed = se === true && phaseLc === "live" && sim === true;
  const scalpWarming = se === true && phaseLc !== "live" && phaseLc !== "standby" && phaseLc !== "—";
  const venueLit = connected && se === true && venue !== "—";

  return (
    <div className="flight-deck" role="region" aria-label="Session overview">
      <div className="flight-deck-inner">
        <span
          className={`fd-chip fd-ws${connected ? " on" : ""}`}
          title={connected ? "This dashboard is connected to the trading bot server (live updates)." : "No connection to the bot server — start the backend or check the URL."}
        >
          {connected ? "SERVER CONNECTED" : "SERVER OFFLINE"}
        </span>
        <span
          className={`fd-chip${engineLive ? " fd-lit" : enginePaper ? " fd-lit-info" : ""}`}
          title={engineLive ? "Portfolio mode is LIVE (Coinbase CDE when scalp is live)." : "Paper / sim / standby."}
        >
          Engine {String(mode).toUpperCase()}
        </span>
        <span
          className={`fd-chip${
            scalpLiveArmed ? " fd-lit" : scalpSimArmed ? " fd-lit-sim" : scalpWarming ? " fd-lit-info" : ""
          }`}
          title={
            se
              ? sim
                ? "Scalp sim mode — no live exchange entries from the scalp loop."
                : phaseLc === "live"
                  ? "Scalp live — loop armed for this session."
                  : `Scalp enabled · phase ${phaseRaw}`
              : "Scalp disabled (off or not loaded)."
          }
        >
          Scalp {se ? (sim ? "SIM" : "LIVE") : "OFF"} · {String(phaseRaw)}
        </span>
        <span className={`fd-chip${venueLit ? " fd-lit-info" : ""}`} title={venueTitle}>
          Venue {venueDisplay}
        </span>
        <span
          className={`fd-chip${legCount > 0 ? " fd-lit-info" : ""}`}
          title={legsTitle}
        >
          Legs {legCount}
          {legsSubtitle ? <span className="fd-chip-sub">{legsSubtitle}</span> : null}
        </span>
        <span className={`fd-chip${daily >= 0 ? " fd-pnl-pos" : " fd-pnl-neg"}`}>
          Today {daily >= 0 ? "+" : ""}{daily.toFixed(2)} USD
        </span>
        {lastErr ? (
          <button
            type="button"
            className="fd-chip fd-chip-alert"
            onClick={() => onOpenLogs({ focusExchangeId: lastErr.id })}
            title={lastErr.detail}
          >
            {unacked.length} exchange alert{unacked.length > 1 ? "s" : ""} · {lastErr.title.slice(0, 48)}
            {lastErr.title.length > 48 ? "…" : ""} → Logs
          </button>
        ) : (
          <button type="button" className="fd-chip fd-chip-link" onClick={() => onOpenLogs()}>
            Open full log
          </button>
        )}
      </div>
      {lastErr ? (
        <div className="flight-deck-sub">
          Last: {tsShort(lastErr.ts)} · {lastErr.source} ·{" "}
          <button type="button" className="fd-inline-btn" onClick={() => onOpenLogs({ focusExchangeId: lastErr.id })}>
            show in Logs
          </button>
        </div>
      ) : null}
    </div>
  );
}
