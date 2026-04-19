# EONET Hazard-Aware Dashboard Design

Date: 2026-04-19
Project: `miniclaw`
Status: Approved design for planning

## Goal

Enhance the existing dashboard so natural hazards from NASA EONET can compete with ordinary news when they are genuinely important, without letting routine local weather crowd out global events.

The intended outcome is a hazard-aware news experience:

- keep the current dashboard structure intact
- promote high-signal natural hazards into the news panel
- keep routine weather in the weather panel
- allow local hazards to rise when they are unusual or severe
- preserve global visibility when global events are more important

This feature is explicitly not for geopolitical or military OSINT. EONET is used only for natural-event awareness. Existing RSS and GDELT flows remain the source for geopolitical and general news.

## Existing Context

MiniClaw already has:

- a native `dashboard` skill that opens, closes, and live-updates the dashboard
- a dashboard container that aggregates RSS, GDELT, weather, stocks, and music state
- a `news` panel that is already the natural place for "what matters now"

The user wants EONET to improve dashboard usefulness when they ask for news or current happenings, but only when the events are materially important. Routine local rain or ordinary storms should remain weather information, not top-priority alerts.

## Selected Approach

Use EONET as a ranked hazard layer inside the existing `news` panel.

Why this approach:

- it fits the current dashboard mental model
- it avoids spending a full panel on hazards before demand is proven
- it keeps orchestration changes small
- it makes EONET compete directly with ordinary news, which matches the product goal

Rejected for v1:

- a separate `hazards` panel
- fully implicit signal blending with no explicit hazard block

## Product Behavior

When the user opens the dashboard or asks for news/current happenings, the dashboard may show a `Priority hazards` block at the top of the news panel.

This block appears only when EONET returns events that clear a ranking threshold. If no events are strong enough, the news panel behaves as it does today.

The user-facing behavior should be:

- ordinary weather stays in the weather panel
- natural hazards can be promoted into the news panel when they are notable
- local relevance boosts importance but does not guarantee dominance
- globally significant hazards can outrank weaker local events
- if EONET is unavailable, the dashboard quietly falls back to normal news behavior

Examples:

- local light rain: weather panel only
- local wildfire or major flood: promoted hazard
- major earthquake abroad: promoted if globally significant
- broad but lower-signal haze event: shown only if little else is happening or if strongly relevant

## Architecture

Keep the current native `dashboard` skill and `ContainerManager` flow.

Host-side responsibilities:

- continue deciding which panels are active
- continue passing dashboard configuration through `DASHBOARD_CONFIG`
- add optional hazard-related config fields when `news` is active

Dashboard container responsibilities:

- fetch EONET data from the current API version
- normalize event payloads into a compact internal structure
- rank events for dashboard use
- merge top hazards into the news panel rendering path

This keeps most of the logic inside the dashboard container, where the existing aggregation and presentation code already lives. It avoids expanding host-side orchestration into another complex data pipeline.

## Configuration Changes

Extend `DASHBOARD_CONFIG` with a small hazard-aware section. Exact field names may change during implementation, but the configuration should support:

- enabling hazard-aware news mode
- optional preferred categories
- optional location or bounding preference when available
- threshold and item-count controls for display

The default behavior should require no user-facing setup beyond existing dashboard use. If location is known, it can be used as a relevance signal. If location is unknown, ranking should still work globally.

## EONET Data Ingestion

The dashboard container should call the current EONET API and request open or current-relevance events suitable for dashboard display.

Normalization should extract:

- stable event id
- title
- category
- sources
- geometry
- event date / freshest geometry date
- open/closed state
- magnitude value, unit, and description when present
- optional derived region label

The container should transform raw EONET payloads into a dashboard-focused internal model rather than passing API payloads directly into templates.

## Ranking Model

Each normalized event should receive a composite score used only for dashboard prioritization.

Inputs to scoring:

- category severity baseline
- recency
- open vs closed status
- magnitude presence and strength when available
- local relevance boost when the event overlaps or plausibly affects the user’s region
- rarity / unusualness relative to routine weather expectations

High-priority categories should include:

- wildfires
- severe storms
- volcanoes
- floods
- earthquakes
- extreme heat or major temperature events if represented with strong signal

Lower-signal categories may still appear, but should rank below the events above unless they are unusually relevant or severe.

Ranking rules:

- local relevance boosts score but does not automatically outrank major global hazards
- routine local weather should not become an EONET alert
- events below threshold should not create UI clutter
- if multiple hazards qualify, show only the strongest few

## UI Design

Add a `Priority hazards` block at the top of the existing news panel.

This block should display roughly `1-3` items with stronger visual hierarchy than normal news cards.

Each item should include:

- category badge
- short title
- relative freshness or event date
- region label when useful
- magnitude snippet when available
- source attribution or source link label

Presentation goals:

- make hazards visually distinct from ordinary news
- keep the block compact enough to avoid starving the rest of the news panel
- avoid showing an empty or weak hazards section

The dashboard should remain legible on the existing monitor-oriented layout without requiring a new panel or major navigation changes.

## Failure Handling

Failure behavior should be quiet and resilient:

- if the EONET request fails, show ordinary news only
- if EONET returns no strong events, omit the hazards block
- if location is unknown, rank globally without blocking
- if a field is missing from an event, degrade gracefully instead of hiding the whole panel

The system should never make the dashboard feel broken just because EONET is empty or temporarily unavailable.

## Scope Boundaries

Included in v1:

- EONET fetch logic in the dashboard container
- event normalization
- dashboard-specific ranking
- `Priority hazards` rendering inside the news panel
- quiet fallback behavior

Explicitly excluded from v1:

- separate `hazards` panel
- map rendering
- historical analytics or trend analysis
- advanced geospatial distance calculations
- mixing geopolitical/OSINT reporting into EONET logic
- new voice intents beyond what the existing dashboard skill already supports

## Testing Strategy

Testing should focus on behavior, not only transport:

- normalization tests for representative EONET payloads
- ranking tests across category, recency, open/closed, and magnitude cases
- threshold tests proving weak events do not render
- merge/render tests proving strong hazards appear above normal news
- fallback tests for empty responses and API errors

Where possible, ranking and normalization should be implemented as focused units so they can be validated without full end-to-end dashboard startup.

## Implementation Notes for Planning

The most likely code impact area is the dashboard container and its template/render helpers, with only light changes in the native dashboard config handoff.

The implementation plan should favor:

- container-local EONET integration
- small, testable ranking helpers
- minimal change to host-side orchestration
- preserving the current dashboard update behavior when the display is already open

## Success Criteria

This design is successful when:

- opening the dashboard with news can surface important natural hazards
- routine weather does not hijack the news panel
- local and global events can compete in a sensible way
- the dashboard still works normally when EONET has nothing useful to add
- the feature fits naturally into the existing dashboard architecture rather than feeling bolted on
