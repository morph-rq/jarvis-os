.pragma library
// Quick requests offered by the island's home and conversation views and the command bar. Each is sent
// as an ordinary request in the user's words (router or model, same risk levels and approvals).

function list(lang) {
  var ru = [["☀  Брифинг на сегодня", "брифинг"], ["🌤  Погода", "погода"], ["🩺  Проверь систему", "проверь систему"],
              ["🎯  Фокус 25 минут", "включи режим фокуса на 25 минут"], ["📋  Объясни, что я скопировал", "объясни что я скопировал"],
              ["🖥  Что на экране?", "что у меня на экране"], ["⏰  Мои напоминания", "какие у меня напоминания"],
              ["🧹  Почисти диск", "почисти диск"], ["📶  Есть ли интернет?", "есть ли интернет"], ["🎵  Что играет?", "что сейчас играет"],
              ["📝  Запиши встречу", "запиши встречу"], ["🧠  Мои навыки", "какие у тебя навыки"], ["↶  Верни как было", "верни как было"],
              ["🌙  Тёмная тема", "тёмная тема"], ["🔕  Не беспокоить", "не беспокоить"], ["🗺  Где МКС?", "где находится мкс"]]
  var pt = [["☀  Resumo do dia", "resumo do dia"], ["🌤  Tempo", "como está o tempo"], ["🩺  Verifique o sistema", "verifique o sistema"],
              ["🎯  Foco 25 minutos", "modo foco de 25 minutos"], ["📋  Explique o que copiei", "explique o que copiei"],
              ["⏰  Meus lembretes", "meus lembretes"], ["🧹  Liberar espaço", "liberar espaço"], ["🎵  O que está tocando?", "o que está tocando"],
              ["↶  Desfazer", "desfazer"], ["🔕  Não perturbe", "não perturbe"]]
  var en = [["☀  Brief me", "brief me"], ["🌤  Weather", "weather"], ["🩺  Check my computer", "check my computer"],
              ["🎯  Focus for 25 minutes", "start a focus session for 25 minutes"], ["📋  Explain what I copied", "explain what i copied"],
              ["🖥  What's on my screen?", "what's on my screen"], ["⏰  My reminders", "my reminders"], ["🧹  Clean up disk", "clean up disk"],
              ["📶  Am I online?", "am i online"], ["🎵  What's playing?", "what's playing"], ["📝  Take meeting notes", "start meeting notes"],
              ["🧠  My skills", "list skills"], ["↶  Undo", "undo"], ["🌙  Dark theme", "dark theme"], ["🔕  Do not disturb", "do not disturb"],
              ["🗺  Where is the ISS?", "where is the iss on the map"]]
  return lang === "ru" ? ru : lang === "pt" ? pt : en
}
