"""Google: sign-in through the existing Google Workspace provider (workspace-mcp, OAuth with PKCE and
state, loopback callback on 127.0.0.1), verified here with read-only Google API calls.

Progressive permissions: the card lets the user tick what Jarvis may do (read mail, send mail,
calendar, Drive, contacts…). Only those levels are passed to workspace-mcp (`--permissions`), so the
consent screen asks for exactly that, and asking for more later adds to what was granted.

The probe uses the account's own credential file (workspace-mcp keeps it in Jarvis's owner-only data
directory; it is not copied anywhere). An expired access token is refreshed in memory only, so the
file workspace-mcp owns is never rewritten by Jarvis. Each capability is checked with a call that
changes nothing: profile, Gmail profile, one calendar list entry, Drive `about`, People `me`.
Sending mail has no harmless probe, so it is reported as "granted" (scope present), not "verified".
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import httpx

from jarvis import config as configmod
from jarvis.accounts import Connector, lifecycle as lc
from jarvis.accounts.errors import AccountError, classify

S = "https://www.googleapis.com/auth/"
# capability -> (workspace-mcp service, level, scopes that grant it)
CAPS = {
    "mail_read": ("gmail", "readonly", {S + "gmail.readonly", S + "gmail.modify", "https://mail.google.com/"}),
    "mail_send": ("gmail", "send", {S + "gmail.send", S + "gmail.compose", "https://mail.google.com/"}),
    "calendar_read": ("calendar", "readonly", {S + "calendar.readonly", S + "calendar", S + "calendar.events"}),
    "calendar_edit": ("calendar", "full", {S + "calendar", S + "calendar.events"}),
    "drive_read": ("drive", "readonly", {S + "drive.readonly", S + "drive"}),
    "contacts_read": ("contacts", "readonly", {S + "contacts.readonly", S + "contacts"}),
    "docs_edit": ("docs", "full", {S + "documents"}),
    "sheets_edit": ("sheets", "full", {S + "spreadsheets"}),
    "tasks": ("tasks", "manage", {S + "tasks"}),
}
LEVELS = {"readonly": 0, "organize": 1, "drafts": 2, "send": 3, "manage": 1, "full": 4}
DEFAULT_CAPS = ("mail_read", "calendar_read", "drive_read")


def permissions_for(caps: list[str], current: list[str] | None = None) -> list[str]:
    """workspace-mcp --permissions for these capabilities, never fewer than what is already allowed."""
    best: dict[str, str] = {}
    for item in current or []:
        service, _, level = str(item).partition(":")
        if service and level:
            best[service] = level
    for cap in caps:
        if cap not in CAPS:
            continue
        service, level, _ = CAPS[cap]
        if LEVELS.get(level, 0) >= LEVELS.get(best.get(service, ""), -1):
            best[service] = level
    return [f"{s}:{lv}" for s, lv in sorted(best.items())]


class Google(Connector):
    id = "google"
    label = "Google"
    icon = "google"
    group = "mail"
    auth = "oauth"
    keywords = ("gmail", "google", "email", "mail", "inbox", "calendar", "schedule", "drive", "docs", "sheets",
                "contacts", "почта", "почту", "письма", "календарь", "диск", "контакты", "courriel", "e-mail",
                "agenda", "calendrier", "fichiers", "e-mails", "correio", "calendário")
    description = {"en": "Gmail, Calendar, Drive and Contacts through your own Google sign-in.",
                   "ru": "Gmail, Календарь, Диск и Контакты через ваш вход в Google.",
                   "fr": "Gmail, Agenda, Drive et Contacts via votre connexion Google."}
    can = ({"en": "Read and search mail", "ru": "Читать и искать письма", "fr": "Lire et chercher vos e-mails"},
           {"en": "Show your schedule", "ru": "Показывать расписание", "fr": "Afficher votre agenda"},
           {"en": "Search Drive files", "ru": "Искать файлы на Диске", "fr": "Chercher dans Drive"})
    cannot = ({"en": "Send, change or delete anything without asking you first",
               "ru": "Отправлять, менять или удалять без вашего подтверждения",
               "fr": "Envoyer, modifier ou supprimer sans vous demander"},)
    limitations = ({"en": "Needs a free Google Cloud OAuth client of your own (one-time setup).",
                    "ru": "Нужен собственный бесплатный OAuth-клиент Google Cloud (настраивается один раз).",
                    "fr": "Nécessite votre propre client OAuth Google Cloud gratuit (une seule fois)."},
                   {"en": "Unverified apps show a Google warning and allow up to 100 test users.",
                    "ru": "Непроверенное приложение показывает предупреждение Google, до 100 тестовых пользователей.",
                    "fr": "Une application non vérifiée affiche un avertissement Google (100 testeurs maximum)."})
    setup_steps = (
        {"en": "Google Cloud Console > APIs & Services > Credentials > Create OAuth client > Desktop app.",
         "ru": "Google Cloud Console > API и сервисы > Учётные данные > OAuth-клиент > Приложение для ПК.",
         "fr": "Google Cloud Console > API et services > Identifiants > Client OAuth > Application de bureau."},
        {"en": "Enable the Gmail, Calendar, Drive and People APIs; add yourself as a test user.",
         "ru": "Включите Gmail, Calendar, Drive и People API; добавьте себя тестовым пользователем.",
         "fr": "Activez les API Gmail, Calendar, Drive et People ; ajoutez-vous comme testeur."},
        {"en": "Paste the client ID and secret here (they go to the keyring).",
         "ru": "Вставьте сюда ID и секрет клиента (они сохранятся в связке ключей).",
         "fr": "Collez ici l'ID et le secret du client (ils vont dans le trousseau)."},
    )

    setup_fields = ({"name": "client_id", "label": "OAuth client ID", "kind": "text"},
                    {"name": "client_secret", "label": "OAuth client secret", "kind": "password"})

    async def configure(self, values: dict) -> str:
        cid, secret = values.get("client_id", "").strip(), values.get("client_secret", "").strip()
        if not cid.endswith(".apps.googleusercontent.com"):
            raise AccountError("not_configured", detail="The client ID ends with .apps.googleusercontent.com.")
        self.a.config.save_secret("GOOGLE_OAUTH_CLIENT_ID", cid)
        if secret:
            self.a.config.save_secret("GOOGLE_OAUTH_CLIENT_SECRET", secret)
        return "Saved in the keyring."

    # Overridable in tests (a fake Google).
    token_url = "https://oauth2.googleapis.com/token"
    api = {"userinfo": "https://www.googleapis.com/oauth2/v2/userinfo",
           "gmail": "https://gmail.googleapis.com/gmail/v1/users/me/profile",
           "calendar": "https://www.googleapis.com/calendar/v3/users/me/calendarList?maxResults=1",
           "drive": "https://www.googleapis.com/drive/v3/about?fields=user",
           "contacts": "https://people.googleapis.com/v1/people/me?personFields=names"}

    @property
    def provider(self):
        integ = getattr(self.j, "integrations", None)
        return integ.providers.get("google") if integ else None

    def capabilities(self) -> list[dict]:
        L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
        return [
            {"id": "mail_read", "label": L("Read mail", "Читать почту", "Lire les e-mails"), "kind": "read",
             "default": True, "permission": L("Gmail: read", "Gmail: чтение", "Gmail : lecture")},
            {"id": "mail_send", "label": L("Send mail (asks first)", "Отправлять письма (с подтверждением)",
                                           "Envoyer des e-mails (avec confirmation)"), "kind": "send", "default": False,
             "permission": L("Gmail: send", "Gmail: отправка", "Gmail : envoi")},
            {"id": "calendar_read", "label": L("See calendar", "Видеть календарь", "Voir l'agenda"), "kind": "read",
             "default": True, "permission": L("Calendar: read", "Календарь: чтение", "Agenda : lecture")},
            {"id": "calendar_edit", "label": L("Add and change events", "Создавать и менять события",
                                               "Créer et modifier des événements"), "kind": "write", "default": False,
             "permission": L("Calendar: edit", "Календарь: изменение", "Agenda : modification")},
            {"id": "drive_read", "label": L("Search Drive", "Искать на Диске", "Chercher dans Drive"), "kind": "read",
             "default": True, "permission": L("Drive: read", "Диск: чтение", "Drive : lecture")},
            {"id": "contacts_read", "label": L("Contacts", "Контакты", "Contacts"), "kind": "read", "default": False,
             "permission": L("Contacts: read", "Контакты: чтение", "Contacts : lecture")},
            {"id": "docs_edit", "label": L("Edit Docs", "Редактировать Документы", "Modifier Docs"), "kind": "write",
             "default": False, "permission": L("Docs: edit", "Документы: изменение", "Docs : modification")},
            {"id": "sheets_edit", "label": L("Edit Sheets", "Редактировать Таблицы", "Modifier Sheets"), "kind": "write",
             "default": False, "permission": L("Sheets: edit", "Таблицы: изменение", "Sheets : modification")},
        ]

    def availability(self):
        p = self.provider
        if p is None:
            return lc.NOT_CONFIGURED, AccountError("missing_dependency", detail="the Google Workspace connector")
        if p.command() is None:
            return lc.NOT_CONFIGURED, AccountError("missing_dependency", detail="workspace-mcp (installed by install-jarvis.sh)")
        if not self.a.config.secret("GOOGLE_OAUTH_CLIENT_ID"):
            return lc.NOT_CONFIGURED, AccountError("not_configured", detail="Add your Google OAuth client (Desktop app) first.")
        return lc.READY, None

    # ------------------------------------------------------------ connect
    async def run(self, flow) -> dict:
        p = self.provider
        caps = [c for c in (flow.options.get("capabilities") or DEFAULT_CAPS) if c in CAPS]
        current = p.permissions() if flow.options.get("add") else None
        perms = permissions_for(caps, current)
        if perms != p.permissions():
            self.a.set_config(("integrations", "google", "permissions"), perms)
            await p.server.stop()   # the next start asks for exactly these permissions
            p._tools = None
        flow.progress("starting")
        try:
            pending = await p.connect("gmail", account_hint=str(flow.options.get("account_hint") or ""))
        except Exception as exc:
            raise classify(exc) from None
        if not await flow.open_browser(pending["url"]):
            flow.progress("browser", "Open the link shown here in your browser.")
        while True:
            await asyncio.sleep(1.5)
            done = await p.poll_connection(pending)
            if done:
                break
        email = done["account_ref"]
        hint = str(flow.options.get("account_hint") or "")
        if hint and "@" in hint and hint.casefold() != email.casefold():
            raise AccountError("wrong_account", detail=email)
        # The tool layer picks accounts from the integrations store: register it there too.
        self.j.integrations.store.upsert_connection("google", "google", email, label=email, scopes=done.get("scopes", []),
                                                    status="active", detail=done.get("detail"))
        return {"ref": email, "label": email, "detail": {"capabilities_requested": caps}}

    async def existing(self) -> list[dict]:
        p = self.provider
        if p is None:
            return []
        return [{"ref": email, "label": email} for email in p._credential_files()]

    # ------------------------------------------------------------ probe
    def _credentials(self, email: str) -> dict:
        p = self.provider
        path = p._credential_files().get(email) if p else None
        if path is None:
            raise AccountError("expired_auth", technical="no credential file for this account")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise AccountError("bad_session", technical="credential file unreadable") from None
        if not isinstance(data, dict) or not (data.get("token") or data.get("refresh_token")):
            raise AccountError("bad_session", technical="credential file has no token")
        return data

    async def _access_token(self, data: dict, http: httpx.AsyncClient) -> str:
        expiry = data.get("expiry")
        fresh = False
        if expiry and data.get("token"):
            try:
                when = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
                when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
                fresh = when.timestamp() - time.time() > 60
            except ValueError:
                fresh = False
        if fresh:
            return data["token"]
        if not data.get("refresh_token"):
            raise AccountError("expired_auth", technical="no refresh token")
        form = {"grant_type": "refresh_token", "refresh_token": data["refresh_token"],
                "client_id": data.get("client_id") or self.a.config.secret("GOOGLE_OAUTH_CLIENT_ID") or ""}
        secret = data.get("client_secret") or self.a.config.secret("GOOGLE_OAUTH_CLIENT_SECRET")
        if secret:
            form["client_secret"] = secret
        r = await http.post(data.get("token_uri") or self.token_url, data=form)
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {}
            raise classify(status=r.status_code, body=f"{body.get('error', '')} {body.get('error_description', '')}")
        return r.json()["access_token"]

    async def probe(self, account: dict) -> dict:
        data = self._credentials(account["ref"])
        granted = set(data.get("scopes") or [])
        caps: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=15) as http:
            try:
                token = await self._access_token(data, http)
                headers = {"Authorization": f"Bearer {token}"}
                r = await http.get(self.api["userinfo"], headers=headers)
            except httpx.HTTPError as exc:
                raise classify(exc) from None
            if r.status_code != 200:
                raise classify(status=r.status_code, body=r.text[:300])
            date = r.headers.get("date", "")
            email = (r.json() or {}).get("email") or account["ref"]
            checks = {"mail_read": "gmail", "calendar_read": "calendar", "drive_read": "drive", "contacts_read": "contacts"}
            for cap, api in checks.items():
                if not granted & CAPS[cap][2]:
                    continue
                try:
                    res = await http.get(self.api[api], headers=headers)
                except httpx.HTTPError:
                    caps[cap] = "error"
                    continue
                if res.status_code == 200:
                    caps[cap] = "verified"
                elif res.status_code == 403 and ("accessNotConfigured" in res.text or "SERVICE_DISABLED" in res.text):
                    caps[cap] = "unavailable"     # the API is not enabled in the user's Cloud project
                elif res.status_code in (401, 403):
                    caps[cap] = "missing_permission"
                else:
                    caps[cap] = "error"
        for cap in ("mail_send", "calendar_edit", "docs_edit", "sheets_edit"):
            if granted & CAPS[cap][2]:
                caps[cap] = "granted"
        return {"capabilities": caps, "identity": {"label": email}, "server_date": date}

    async def disconnect(self, account: dict) -> str:
        integ = self.j.integrations
        result = await self.provider.disconnect("google", account["ref"])
        integ.store.remove_connection("google", "google", account["ref"])
        return result.get("message", "")
