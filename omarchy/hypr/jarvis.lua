-- Jarvis WorkOS keybindings, loaded from ~/.config/hypr/bindings.lua by install.sh.
-- Plain commands rather than o.bind's { panel = ... } form, which Omarchy 4.0.x does not have.
local jw = (os.getenv("HOME") or "") .. "/.local/bin/jarvis-workos"

o.bind("SUPER + ALT + J", "Jarvis panel", "omarchy-shell shell toggle jarvis.companion '{}'")
o.bind("SUPER + ALT + V", "Jarvis: talk (push-to-talk)", jw .. " listen toggle")
o.bind("SUPER + ALT + D", "Jarvis: dictation on/off", jw .. " dictate toggle")
o.bind("SUPER + ALT + PERIOD", "Jarvis: stop talking", jw .. " stop")
o.bind("SUPER + ALT + COMMA", "Jarvis: cancel tasks", jw .. " cancel")
o.bind("SUPER + ALT + M", "Jarvis: microphone on/off", jw .. " mute")

-- Hold to dictate, release to insert (newer Omarchy only; 4.0.x has no o.bind_hold).
if o.bind_hold then
  o.bind_hold("SUPER + ALT + H", "Jarvis: dictate while held", jw .. " dictate start",
              "Jarvis: insert dictation", jw .. " dictate stop")
end
