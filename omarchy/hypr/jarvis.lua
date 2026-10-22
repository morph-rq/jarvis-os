-- Jarvis WorkOS keybindings, loaded from ~/.config/hypr/bindings.lua by install.sh.
-- Plain commands rather than o.bind's { panel = ... } form, which Omarchy 4.0.x does not have.
-- Every key here is free in Omarchy's own bindings (default/hypr/bindings/*.lua at the pinned commit):
-- Hyprland runs every bind that matches, so a shared key would trigger both. Omarchy keeps
-- SUPER+ALT+SPACE (apps), SUPER+ALT+COMMA (last notification), Right-Alt and F9 (its dictation).
local jw = (os.getenv("HOME") or "") .. "/.local/bin/jarvis-workos"
-- The island, panel and approvals live in whichever shell hosts Jarvis (Omarchy's or Caelestia's);
-- jarvis-ui sends the command to the right one (jarvis-shell-mode records which).
local ui = (os.getenv("HOME") or "") .. "/.local/bin/jarvis-ui"

o.bind("SUPER + ALT + J", "Jarvis panel", ui .. " toggle")
o.bind("SUPER + ALT + A", "Jarvis: ask (conversation)", ui .. " commandBar")
o.bind("SUPER + ALT + I", "Jarvis Island: open / close", ui .. " island toggle")
o.bind("SUPER + ALT + Y", "Jarvis: allow the pending request", ui .. " approveLatest true")
o.bind("SUPER + ALT + N", "Jarvis: deny the pending request", ui .. " approveLatest false")
o.bind("SUPER + ALT + V", "Jarvis: talk (push-to-talk)", jw .. " listen toggle")
o.bind("SUPER + ALT + D", "Jarvis: dictation on/off", jw .. " dictate toggle")
o.bind("SUPER + ALT + PERIOD", "Jarvis: stop talking", jw .. " stop")
o.bind("SUPER + ALT + X", "Jarvis: cancel tasks", jw .. " cancel")
o.bind("SUPER + ALT + M", "Jarvis: microphone on/off", jw .. " mute")

-- Touchpad: a four-finger swipe up opens the command bar, down starts talking (Hyprland's Lua
-- gestures; skipped where hl.gesture is missing). Three-finger swipes stay Omarchy's.
if hl and hl.gesture then
  hl.gesture({ fingers = 4, direction = "up", action = function() hl.exec_cmd(ui .. " commandBar") end })
  hl.gesture({ fingers = 4, direction = "down", action = function() hl.exec_cmd(jw .. " listen toggle") end })
end

-- Hold to dictate, release to insert (newer Omarchy only; 4.0.x has no o.bind_hold).
if o.bind_hold then
  o.bind_hold("SUPER + ALT + H", "Jarvis: dictate while held", jw .. " dictate start",
              "Jarvis: insert dictation", jw .. " dictate stop")
end

-- The Living Workspace: the larger interactive view (results, documents, approvals, connected apps).
o.bind("SUPER + ALT + W", "Jarvis workspace", jw .. " workspace")

-- Caelestia mode (jarvis-shell-mode caelestia writes this file: Caelestia's autostart and drawer keys).
pcall(require, "hypr.jarvis-shell")
