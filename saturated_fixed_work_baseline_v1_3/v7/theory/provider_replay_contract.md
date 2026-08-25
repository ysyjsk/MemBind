# Provider Replay Contract

V6 evidence establishes exact request identity comparison, 92/92 logical
capture/consume and no duplicate consume for its sealed probe. It does not
establish that a provider response is replayable when server/session/history,
tool state or external side effects are not recorded.

`ReplayAllowed=true` therefore requires a declaration from the provider or
deployment authority containing an authority/version, exact model/schema/tool/
config/policy epochs, complete response artifact, hidden-state exclusions and
an external-side-effect prohibition. Temperature zero, a seed, or repeated
agreement is not a declaration. Current V7 status is `UNKNOWN`, so a live
response is always fresh; a stable read/request may still be maintained.
