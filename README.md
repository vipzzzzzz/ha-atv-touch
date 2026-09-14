# Apple TV Touch for Home Assistant

Adds Siri-Remote **clickpad gestures** to Home Assistant's built-in Apple TV
integration, using the connection it already holds (no extra pairing).

Why: `remote.send_command` only exposes HID *buttons*, and its `hold_secs`
maps to pyatv's fixed 1-second hold. On tvOS that is long enough to open the
context menu **and** trigger key-repeat on the menu that just opened (on
Music's Now Playing this shows up as play/pause churn). tvOS apps also treat
the plain HID Select button differently from a clickpad click (Music: Select =
previous track, click = play/pause).

## Services

| Service | What it does |
|---|---|
| `atv_touch.click` | Clickpad click (`action: single` or `double`) at `x`/`y` (0–1000, default centre). pyatv's own click lands in the pad's corner, which tvOS grids treat as an edge/directional press — that is why a "select" occasionally only moved focus |
| `atv_touch.hold` | Press → wait `hold_ms` (default 600) → release, at `x`/`y` (0–1000, default centre) |
| `atv_touch.swipe` | Swipe from `start_x/y` to `end_x/y` over `duration_ms` |
| `atv_touch.button_hold` | Hold a HID **button** (`select`, `menu`, `home`, d-pad, `play_pause`) for exactly `hold_ms` (default 700) — the long-press tvOS apps use for item context menus, with a hold short enough to avoid key-repeat |

All take an optional `entity_id` of any `apple_tv` entity; with one Apple TV
it is picked automatically.

```yaml
action: atv_touch.hold
data:
  hold_ms: 600     # tvOS context menu without the key-repeat storm
```

## Install

HACS → custom repositories → add this repo (Integration) → install → restart
→ Settings → Integrations → add **Apple TV Touch** (no settings).
Requires a working Apple TV integration (Companion protocol paired).
