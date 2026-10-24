"""First-run setup: welcome -> language -> about you -> connect services -> check -> ready.

The progress lives in `onboarding.json` (owner-only, in Jarvis's data directory), so a restart lands
on the same step; finishing (or skipping) it marks it done and it is never forced again (Settings >
Run setup again resets it). Existing installs (Jarvis already has a conversation history) count as
set up. Nothing here blocks the rest of Jarvis: the island, voice and tools work at every step.

The suggestions on the last step are built from what is *verified*: "Summarize my inbox" appears
only when a mail account's read capability passed its probe.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from jarvis import config as configmod

STAGES = ("welcome", "language", "personal", "connect", "verify", "ready", "done")

# (text per language, the request it sends, (provider ids…), capability) — shown only when available.
SUGGESTIONS = (
    ({"en": "Summarize my inbox", "ru": "Кратко о моей почте", "fr": "Résume ma boîte mail", "pt": "Resuma meu e-mail"},
     {"en": "Summarize my unread email", "ru": "Кратко перескажи мои непрочитанные письма",
      "fr": "Résume mes e-mails non lus", "pt": "Resuma meus e-mails não lidos"}, ("google", "microsoft"), "mail_read"),
    ({"en": "Show my schedule", "ru": "Покажи моё расписание", "fr": "Montre mon agenda", "pt": "Mostre minha agenda"},
     {"en": "What's on my calendar today?", "ru": "Что у меня сегодня в календаре?",
      "fr": "Qu'y a-t-il dans mon agenda aujourd'hui ?", "pt": "O que tenho na agenda hoje?"},
     ("google", "microsoft"), "calendar_read"),
    ({"en": "Search my Drive", "ru": "Поиск на моём Диске", "fr": "Cherche dans mon Drive", "pt": "Buscar no meu Drive"},
     {"en": "Find my most recent documents on Google Drive", "ru": "Найди мои последние документы на Google Диске",
      "fr": "Trouve mes documents récents sur Google Drive", "pt": "Encontre meus documentos recentes no Google Drive"},
     ("google",), "drive_read"),
    ({"en": "Find a OneDrive file", "ru": "Найти файл в OneDrive", "fr": "Trouver un fichier OneDrive", "pt": "Achar arquivo no OneDrive"},
     {"en": "Show my recent OneDrive files", "ru": "Покажи последние файлы в OneDrive",
      "fr": "Montre mes fichiers OneDrive récents", "pt": "Mostre meus arquivos recentes do OneDrive"}, ("microsoft",), "files_read"),
    ({"en": "Find a Telegram message", "ru": "Найти сообщение в Telegram", "fr": "Trouver un message Telegram",
      "pt": "Achar mensagem no Telegram"},
     {"en": "Find my latest Telegram messages", "ru": "Найди мои последние сообщения в Telegram",
      "fr": "Trouve mes derniers messages Telegram", "pt": "Ache minhas últimas mensagens do Telegram"}, ("telegram",), "read"),
    ({"en": "Open a document", "ru": "Открыть документ", "fr": "Ouvrir un document", "pt": "Abrir um documento"},
     {"en": "Open my most recent document", "ru": "Открой мой последний документ",
      "fr": "Ouvre mon document le plus récent", "pt": "Abra meu documento mais recente"}, ("files",), "search"),
    ({"en": "Search the web", "ru": "Поиск в интернете", "fr": "Chercher sur le web", "pt": "Pesquisar na web"},
     {"en": "What's new in tech today?", "ru": "Что нового в технологиях сегодня?",
      "fr": "Quoi de neuf dans la tech aujourd'hui ?", "pt": "O que há de novo em tecnologia hoje?"}, ("research",), "search"),
)


class Onboarding:
    def __init__(self, jarvis, path: Path | None = None) -> None:
        self.j = jarvis
        self.path = path or configmod.data_dir() / "onboarding.json"
        self.state = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("stage") in STAGES:
                return data
        except (OSError, ValueError):
            pass
        existing = False
        try:
            existing = bool(self.j.memory.recent_turns(1))
        except Exception:
            pass
        state = {"stage": "done" if existing else "welcome", "mode": "text", "name": "", "voice": None,
                 "connect_later": False, "started": time.time(), "completed": time.time() if existing else None,
                 "existing_install": existing, "skipped": []}
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, self.path)

    @property
    def done(self) -> bool:
        return self.state["stage"] == "done"

    def should_open(self) -> bool:
        return not self.done and bool(self.j.config.values.get("onboarding", {}).get("auto_open", True))

    def suggestions(self) -> list[dict]:
        lang = self._lang()
        acc = getattr(self.j, "accounts", None)
        out = []
        for label, ask, providers, cap in SUGGESTIONS:
            ok = False
            if acc is not None:
                for pid in providers:
                    c = acc.connectors.get(pid)
                    if c is None:
                        continue
                    if c.kind == "local":
                        caps = acc._local_cache.get(pid, (0, {}))[1].get("capabilities") or {}
                        ok = ok or caps.get(cap) == "verified"
                    else:
                        ok = ok or acc.capability_ok(pid, cap)
            out.append({"label": label.get(lang) or label["en"], "ask": ask.get(lang) or ask["en"], "available": ok})
        return out

    def _lang(self) -> str:
        try:
            return self.j.ui_lang()
        except Exception:
            return "en"

    def public(self) -> dict:
        s = self.state
        return {**s, "stages": list(STAGES), "index": STAGES.index(s["stage"]), "open": self.should_open(),
                "language": self.j.config["ui"]["language"], "ui_lang": self._lang(),
                "suggestions": self.suggestions() if s["stage"] in ("verify", "ready", "done") else []}

    def publish(self) -> None:
        self.j.bus.publish("onboarding", state=self.public())

    async def _apply(self, fields: dict) -> list[str]:
        """Settings the user chose on a step. Returns the setting keys changed."""
        changed = []
        cfg = self.j.config
        if "language" in fields and fields["language"] in ("ru", "en", "fr", "pt", "auto"):
            cfg.set("ui.language", fields["language"])
            changed.append("ui.language")
        if "name" in fields:
            name = " ".join(str(fields["name"]).split())[:60]
            self.state["name"] = name
            cfg.set("ui.user_name", name)
            changed.append("ui.user_name")
            if name:
                self.j.memory.remember(f"The user's preferred name is {name}.")
        if "voice" in fields:
            self.state["voice"] = bool(fields["voice"])
            cfg.set("wake.enabled", bool(fields["voice"]))
            changed.append("wake.enabled")
        if "mode" in fields and fields["mode"] in ("voice", "text"):
            self.state["mode"] = fields["mode"]
        if "connect_later" in fields:
            self.state["connect_later"] = bool(fields["connect_later"])
        if changed:
            cfg.save()
        return changed

    async def op(self, req: dict) -> dict:
        action = str(req.get("action") or "status")
        s = self.state
        if action == "status":
            return {"ok": True, "state": self.public()}
        if action == "set":
            changed = await self._apply(req.get("fields") or {})
            for key in changed:
                apply = getattr(self.j, "_apply_setting", None)
                if apply:
                    await apply(key)
            if changed and hasattr(self.j, "publish_settings"):
                self.j.publish_settings()
        elif action in ("next", "skip"):
            if action == "skip" and s["stage"] not in s["skipped"]:
                s["skipped"].append(s["stage"])
            i = STAGES.index(s["stage"])
            nxt = STAGES[min(i + 1, len(STAGES) - 1)]
            if s["stage"] == "personal" and s.get("connect_later"):
                nxt = "ready"
            s["stage"] = nxt
        elif action == "back":
            i = STAGES.index(s["stage"])
            s["stage"] = STAGES[max(0, i - 1)] if s["stage"] != "done" else "ready"
        elif action == "goto" and req.get("stage") in STAGES:
            s["stage"] = req["stage"]
        elif action == "finish":
            s["stage"] = "done"
        elif action == "reset":
            s.update(stage="welcome", completed=None, skipped=[], started=time.time())
        else:
            return {"ok": False, "message": f"unknown onboarding action {action!r}"}
        if s["stage"] == "done" and not s.get("completed"):
            s["completed"] = time.time()
        self.save()
        self.publish()
        return {"ok": True, "state": self.public()}
