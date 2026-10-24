"""What is already on this computer: found by asking the system, never by reading private files.

Each check is cheap and harmless: `shutil.which` for installed programs, a GET of a local model
runner's model list on 127.0.0.1, and the status Jarvis already keeps for Hermes, OpenClaw and web
research. No browser profile, cookie store or password database is opened here (importing bookmarks
is a separate, opt-in step: jarvis/imports.py).
"""

from __future__ import annotations

import shutil

import httpx

from jarvis.accounts import Connector, lifecycle as lc
from jarvis.accounts.errors import AccountError

L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
FREE = L("Free, on this computer", "Бесплатно, на этом компьютере", "Gratuit, sur cet ordinateur")


class Local(Connector):
    kind = "local"
    auth = "none"
    external = False
    cost = FREE
    group = "local"
    multi_account = False

    def availability(self):
        return lc.READY, None


class Browser(Local):
    id = "browser"
    label = "Web browser"
    icon = "browser"
    keywords = ("browser", "web", "website", "open site", "chromium", "firefox", "браузер", "сайт", "navigateur", "site")
    description = L("Jarvis can open pages and, when you ask, work in a browser window it controls.",
                    "Jarvis открывает страницы и по вашей просьбе работает в своём окне браузера.",
                    "Jarvis ouvre des pages et, si vous le demandez, travaille dans une fenêtre qu'il pilote.")
    can = (L("Open and read web pages", "Открывать и читать страницы", "Ouvrir et lire des pages"),)
    cannot = (L("Use your browser's saved passwords or cookies", "Пользоваться сохранёнными паролями и cookies",
                "Utiliser vos mots de passe ou cookies enregistrés"),)
    BINARIES = ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "brave", "brave-browser",
                "firefox", "vivaldi-stable")

    def capabilities(self):
        return [{"id": "open", "label": L("Open pages", "Открывать страницы", "Ouvrir des pages"), "kind": "read"},
                {"id": "automate", "label": L("Work in pages (asks first)", "Действовать на страницах (с подтверждением)",
                                              "Agir sur les pages (avec confirmation)"), "kind": "write"}]

    async def local_state(self):
        found = [b for b in self.BINARIES if shutil.which(b)]
        chromium = any(b.startswith(("chrom", "google", "brave", "vivaldi")) for b in found)
        return {"installed": bool(found), "detail": ", ".join(found[:3]),
                "capabilities": {"open": "verified" if found else "",
                                 "automate": "verified" if chromium else ("unsupported" if found else "")}}


class Office(Local):
    id = "office"
    label = "Documents & LibreOffice"
    icon = "doc"
    keywords = ("libreoffice", "office", "word", "excel", "docx", "xlsx", "pdf", "document", "документ", "таблица",
                "документы", "tableur", "documents")
    description = L("Create and read Word, Excel, PowerPoint and PDF files locally.",
                    "Создание и чтение файлов Word, Excel, PowerPoint и PDF прямо на компьютере.",
                    "Créer et lire des fichiers Word, Excel, PowerPoint et PDF en local.")
    can = (L("Make documents and spreadsheets", "Делать документы и таблицы", "Créer documents et tableurs"),)

    def capabilities(self):
        return [{"id": "documents", "label": L("Office files", "Офисные файлы", "Fichiers bureautiques"), "kind": "write"},
                {"id": "convert", "label": L("Convert with LibreOffice", "Конвертация LibreOffice", "Conversion LibreOffice"),
                 "kind": "read"}]

    async def local_state(self):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        try:
            import docx  # noqa: F401
            docs = True
        except ImportError:
            docs = False
        return {"installed": bool(soffice or docs), "detail": "LibreOffice" if soffice else "",
                "capabilities": {"documents": "verified" if docs else "", "convert": "verified" if soffice else ""}}


class Files(Local):
    id = "files"
    label = "Your files"
    icon = "folder"
    keywords = ("files", "folder", "find file", "файлы", "папка", "файл", "fichiers", "dossier", "arquivos")
    description = L("Find and open files in the folders you allow (Settings > Files).",
                    "Поиск и открытие файлов в разрешённых папках (Настройки > Файлы).",
                    "Trouver et ouvrir des fichiers dans les dossiers autorisés (Réglages > Fichiers).")
    cannot = (L("Look outside the folders you allowed", "Смотреть за пределами разрешённых папок",
                "Regarder hors des dossiers autorisés"),)

    def capabilities(self):
        return [{"id": "search", "label": L("Search files", "Поиск файлов", "Chercher des fichiers"), "kind": "read"}]

    async def local_state(self):
        roots = self.a.config.values.get("files", {}).get("roots") or []
        return {"installed": bool(roots), "detail": ", ".join(map(str, roots[:3])),
                "capabilities": {"search": "verified" if roots else ""}}


class LocalAI(Local):
    id = "local_ai"
    label = "Local AI models"
    icon = "chip"
    keywords = ("local model", "ollama", "offline", "llama", "private ai", "локальная модель", "без интернета",
                "modèle local", "hors ligne")
    description = L("Use a model running on this computer (Ollama, LM Studio, llama.cpp) for private, free answers.",
                    "Модель на этом компьютере (Ollama, LM Studio, llama.cpp): приватно и бесплатно.",
                    "Un modèle sur cet ordinateur (Ollama, LM Studio, llama.cpp) : privé et gratuit.")
    limitations = (L("Slow on computers without a capable GPU.", "Медленно без мощной видеокарты.",
                     "Lent sans carte graphique performante."),)
    RUNNERS = (("Ollama", "http://127.0.0.1:11434/api/tags", "models"),
               ("LM Studio", "http://127.0.0.1:1234/v1/models", "data"),
               ("llama.cpp", "http://127.0.0.1:8080/v1/models", "data"))

    def capabilities(self):
        return [{"id": "chat", "label": L("Answer questions locally", "Отвечать локально", "Répondre en local"), "kind": "read"}]

    async def local_state(self):
        found = []
        async with httpx.AsyncClient(timeout=1.5) as http:
            for name, url, key in self.RUNNERS:
                try:
                    r = await http.get(url)
                    if r.status_code == 200:
                        models = r.json().get(key) or []
                        found.append(f"{name} ({len(models)} models)")
                except (httpx.HTTPError, ValueError):
                    continue
        if not found and shutil.which("ollama"):
            return {"installed": False, "detail": "Ollama is installed but not running"}
        return {"installed": bool(found), "detail": ", ".join(found), "capabilities": {"chat": "verified" if found else ""}}


class _Backend(Local):
    backend = ""

    async def local_state(self):
        b = getattr(self.j, "backends", None)
        st = (b.snapshot() if b else {}).get(self.backend) or {}
        healthy = bool(st.get("healthy"))
        return {"installed": healthy or st.get("state") not in (None, "off", "missing"),
                "detail": f"{st.get('state', 'off')}{' ' + st['version'] if st.get('version') else ''}",
                "capabilities": {"agent": "verified" if healthy else ""}}

    def capabilities(self):
        return [{"id": "agent", "label": L("Delegated tasks", "Делегированные задачи", "Tâches déléguées"), "kind": "write"}]


class HermesAgent(_Backend):
    id = "hermes"
    label = "Hermes Agent"
    icon = "agent"
    group = "agents"
    backend = "hermes"
    keywords = ("hermes", "skills", "навыки", "compétences", "agent")
    description = L("Specialist agent with skills; Jarvis hands it longer tasks and answers its approvals.",
                    "Агент-специалист с навыками: Jarvis передаёт ему длинные задачи и отвечает на его запросы.",
                    "Agent spécialisé avec des compétences ; Jarvis lui confie les longues tâches.")


class OpenClaw(_Backend):
    id = "openclaw"
    label = "OpenClaw"
    icon = "agent"
    group = "agents"
    backend = "openclaw"
    keywords = ("openclaw", "clawhub", "plugins", "плагины", "extensions")
    description = L("Plugins and ClawHub skills, on this computer only.", "Плагины и навыки ClawHub, только на этом компьютере.",
                    "Plugins et compétences ClawHub, uniquement sur cet ordinateur.")


class Research(Local):
    id = "research"
    label = "Web research"
    icon = "search"
    keywords = ("search the web", "research", "news", "google it", "поиск", "найди в интернете", "recherche", "pesquisa")
    description = L("Private web search (SearXNG) with cited answers.", "Приватный поиск (SearXNG) с источниками.",
                    "Recherche web privée (SearXNG) avec sources.")

    def capabilities(self):
        return [{"id": "search", "label": L("Search with sources", "Поиск с источниками", "Recherche sourcée"), "kind": "read"}]

    async def local_state(self):
        r = getattr(self.j, "research", None)
        snap = r.snapshot() if r and hasattr(r, "snapshot") else {}
        ready = bool(snap.get("ready"))
        return {"installed": bool(snap.get("enabled")) and ready, "detail": "ready" if ready else "not running",
                "capabilities": {"search": "verified" if ready else ""}}


class Composio(Local):
    id = "composio"
    label = "Composio (Slack, Notion, GitHub…)"
    icon = "app"
    group = "apps"
    external = True
    cost = L("Free tier, then paid by Composio", "Бесплатный тариф, дальше платно (Composio)",
             "Offre gratuite, puis payante (Composio)")
    keywords = ("slack", "notion", "trello", "github", "jira", "asana", "todoist", "hubspot", "dropbox", "airtable", "zoom",
                "linear", "composio")
    description = L("Many cloud apps through Composio's service: you sign in to each app on its own page.",
                    "Много облачных приложений через сервис Composio: вход в каждое — на его странице.",
                    "De nombreuses applis cloud via Composio : connexion à chacune sur sa propre page.")
    limitations = (L("Your app sign-ins are kept by Composio (a third party).",
                     "Входы в приложения хранит Composio (сторонний сервис).",
                     "Vos connexions sont conservées par Composio (un tiers)."),)
    setup_fields = ({"name": "key", "label": "Composio API key", "kind": "password"},)

    async def configure(self, values: dict) -> str:
        key = values.get("key", "").strip()
        if len(key) < 10:
            raise AccountError("not_configured", detail="That doesn't look like a Composio key.")
        self.a.config.save_secret("COMPOSIO_API_KEY", key)
        return "Saved in the keyring. Connect apps from Settings > Connected apps."

    async def local_state(self):
        ok = bool(self.a.config.secret("COMPOSIO_API_KEY"))
        return {"installed": ok, "detail": "API key set" if ok else "needs an API key"}
