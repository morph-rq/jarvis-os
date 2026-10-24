# User context and application launching

Owner chat, desktop voice, and owner calls receive the configured user name
(`ui.user_name`), home city (`web.city`), local account name, device timezone,
current date/time, and all saved facts. Facts are read anew for each typed/local
voice request and when a realtime voice session starts. Guest calls do not
receive owner context. Forgetting a fact removes it from future context.

“My location?” opens a map using the configured city or a remembered home city.
If neither exists, it uses the city in the device's timezone and labels it as
approximate in both the reply and map card. A timezone is not GPS and may differ
from the user's current location. UTC and fixed-offset zones provide no city.
An explicit city always takes precedence over the timezone hint.

On Hyprland, opening an app checks for its window before reporting a successful
launch. LibreOffice's shared document recovery dialog blocks Calc, Writer and
Impress; Jarvis focuses that dialog and asks the user to finish recovery rather
than claiming the requested editor opened. Recoverable documents are preserved.
Excel, Word and PowerPoint resolve through the installed desktop entries to
Calc, Writer and Impress respectively.
