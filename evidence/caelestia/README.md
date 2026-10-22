# Caelestia + Jarvis: colour schemes (headless test desktop, 2026-10-22)

The wallpaper is a generated warm gradient, and Caelestia's dynamic scheme derives the palette from it
(`caelestia wallpaper -f …; caelestia scheme set -n dynamic`). The island reads the palette through the
theme bridge (`omarchy/caelestia/overlay/services/Jarvis.qml`).

- `scheme-dyn-dark.png`: dark mode, the island's conversation open. Its surface, bubbles and accents
  follow the wallpaper's palette.
- `scheme-dyn-dash.png`: Caelestia's dashboard open. The island steps aside, so there's one drawer at
  the top edge.
- `scheme-dyn-light.png`: `caelestia scheme set -m light`. The island switches to the light palette
  with no restart.
