"""Microsoft 365 / Outlook.com: delegated OAuth (authorization code + PKCE, loopback redirect) against
the Microsoft identity platform, then Microsoft Graph.

Why not MSAL: MSAL's interactive helper opens the browser and runs its own loopback server, which
would bypass Jarvis's consent step and progress events. The flow here is the same protocol MSAL uses
for public desktop clients (RFC 8252, PKCE S256, `http://localhost` redirect with any port), small
enough to test end to end against a fake identity server.

The app registration is the user's (or their admin's): a public client with the "Mobile and desktop"
redirect `http://localhost`. Jarvis never ships a shared client id. Personal accounts (Outlook.com)
and work/school accounts both work with tenant `common`; organizations can require admin consent,
which is reported as such (AADSTS65001/90094) rather than as a failure.

Each capability is verified separately with a read-only Graph call. Teams exists only for
work/school accounts; on a personal account it is shown as unsupported, not broken. Excel Online
works through OneDrive files, so it is verified with Files.Read.
"""

from __future__ import annotations

import time

import httpx

from jarvis.accounts import Connector, lifecycle as lc, oauth
from jarvis.accounts.errors import AccountError, classify

BASE_SCOPES = ["openid", "profile", "offline_access", "User.Read"]
CAP_SCOPES = {
    "mail_read": ["Mail.Read"], "mail_send": ["Mail.Send"], "calendar_read": ["Calendars.Read"],
    "calendar_edit": ["Calendars.ReadWrite"], "files_read": ["Files.Read"], "excel": ["Files.Read"],
    "contacts_read": ["Contacts.Read"], "teams_read": ["Team.ReadBasic.All"],
}
DEFAULT_CAPS = ("mail_read", "calendar_read", "files_read")
PERSONAL_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"   # Microsoft accounts (Outlook.com, Hotmail)


def scopes_for(caps: list[str]) -> list[str]:
    out = list(BASE_SCOPES)
    for cap in caps:
        for s in CAP_SCOPES.get(cap, []):
            if s not in out:
                out.append(s)
    return out


class Microsoft(Connector):
    id = "microsoft"
    label = "Microsoft 365"
    icon = "microsoft"
    group = "mail"
    auth = "oauth"
    keywords = ("microsoft", "outlook", "office 365", "onedrive", "excel", "teams", "hotmail", "email", "mail", "calendar",
                "почта", "аутлук", "календарь", "courriel", "e-mail", "agenda", "calendrier")
    description = {"en": "Outlook mail and calendar, OneDrive, Excel Online, contacts and Teams.",
                   "ru": "Почта и календарь Outlook, OneDrive, Excel Online, контакты и Teams.",
                   "fr": "Courrier et agenda Outlook, OneDrive, Excel Online, contacts et Teams."}
    can = ({"en": "Read mail and your schedule", "ru": "Читать почту и расписание", "fr": "Lire le courrier et l'agenda"},
           {"en": "Find OneDrive and Excel files", "ru": "Находить файлы OneDrive и Excel", "fr": "Trouver vos fichiers OneDrive et Excel"})
    cannot = ({"en": "Use Teams with a personal Microsoft account", "ru": "Teams с личным аккаунтом Microsoft",
               "fr": "Teams avec un compte Microsoft personnel"},
              {"en": "Act without asking when something would be sent or changed",
               "ru": "Отправлять или менять что-то без вашего подтверждения",
               "fr": "Envoyer ou modifier quoi que ce soit sans vous demander"})
    limitations = ({"en": "Needs an app registration (free) in Microsoft Entra; work accounts may need admin approval.",
                    "ru": "Нужна регистрация приложения в Microsoft Entra (бесплатно); рабочим аккаунтам может понадобиться одобрение администратора.",
                    "fr": "Nécessite une inscription d'application (gratuite) dans Microsoft Entra ; un compte pro peut exiger l'accord d'un admin."},)
    setup_steps = (
        {"en": "entra.microsoft.com > App registrations > New registration (any account type).",
         "ru": "entra.microsoft.com > Регистрация приложений > Новая (любой тип аккаунтов).",
         "fr": "entra.microsoft.com > Inscriptions d'applications > Nouvelle inscription (tous types de comptes)."},
        {"en": "Authentication > Add a platform > Mobile and desktop > http://localhost; allow public client flows.",
         "ru": "Проверка подлинности > Платформа > Мобильные и классические > http://localhost; разрешите общедоступные клиенты.",
         "fr": "Authentification > Ajouter une plateforme > Mobile et bureau > http://localhost ; autorisez les clients publics."},
        {"en": "Paste the Application (client) ID here.", "ru": "Вставьте сюда ID приложения (клиента).",
         "fr": "Collez ici l'ID d'application (client)."},
    )

    authority = "https://login.microsoftonline.com"
    graph = "https://graph.microsoft.com/v1.0"

    def client(self) -> oauth.Client:
        cfg = self.config
        tenant = str(cfg.get("microsoft_tenant") or "common")
        return oauth.Client(f"{self.authority}/{tenant}/oauth2/v2.0/authorize", f"{self.authority}/{tenant}/oauth2/v2.0/token",
                            str(cfg.get("microsoft_client_id") or ""), redirect_host="localhost", redirect_path="/",
                            extra={"prompt": "select_account"})

    def capabilities(self) -> list[dict]:
        L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
        return [
            {"id": "mail_read", "label": L("Read Outlook mail", "Читать почту Outlook", "Lire le courrier Outlook"),
             "kind": "read", "default": True, "permission": "Mail.Read"},
            {"id": "mail_send", "label": L("Send mail (asks first)", "Отправлять (с подтверждением)", "Envoyer (avec confirmation)"),
             "kind": "send", "default": False, "permission": "Mail.Send"},
            {"id": "calendar_read", "label": L("See calendar", "Видеть календарь", "Voir l'agenda"), "kind": "read",
             "default": True, "permission": "Calendars.Read"},
            {"id": "calendar_edit", "label": L("Add and change events", "Создавать и менять события", "Créer et modifier"),
             "kind": "write", "default": False, "permission": "Calendars.ReadWrite"},
            {"id": "files_read", "label": L("OneDrive files", "Файлы OneDrive", "Fichiers OneDrive"), "kind": "read",
             "default": True, "permission": "Files.Read"},
            {"id": "excel", "label": L("Excel Online (read)", "Excel Online (чтение)", "Excel Online (lecture)"),
             "kind": "read", "default": False, "permission": "Files.Read"},
            {"id": "contacts_read", "label": L("Contacts", "Контакты", "Contacts"), "kind": "read", "default": False,
             "permission": "Contacts.Read"},
            {"id": "teams_read", "label": L("Teams (work/school)", "Teams (рабочий аккаунт)", "Teams (compte pro)"),
             "kind": "read", "default": False, "permission": "Team.ReadBasic.All"},
        ]

    def availability(self):
        if not self.config.get("microsoft_client_id"):
            return lc.NOT_CONFIGURED, AccountError("not_configured", detail="Add your Microsoft app registration's client ID first.")
        return lc.READY, None

    setup_fields = ({"name": "client_id", "label": "Application (client) ID", "kind": "text"},
                    {"name": "tenant", "label": "Tenant (optional: common, organizations, consumers or an ID)",
                     "kind": "text", "required": False})

    async def configure(self, values: dict) -> str:
        cid = values.get("client_id", "").strip()
        if not cid or len(cid) > 64:
            raise AccountError("not_configured", detail="That doesn't look like a client ID.")
        self.a.set_config(("accounts", "microsoft_client_id"), cid)
        if values.get("tenant", "").strip():
            self.a.set_config(("accounts", "microsoft_tenant"), values["tenant"].strip())
        return "Saved."

    # ------------------------------------------------------------ tokens
    def _vault_key(self, ref: str) -> str:
        return f"microsoft:{ref.lower()}"

    async def _token(self, account: dict, http: httpx.AsyncClient) -> str:
        blob = self.a.vault.get_json(self._vault_key(account["ref"]))
        if not blob or not blob.get("refresh_token"):
            raise AccountError("expired_auth", technical="no saved session")
        if blob.get("access_token") and blob.get("expires_at", 0) - time.time() > 120:
            return blob["access_token"]
        tokens = await oauth.refresh(self.client(), blob["refresh_token"], blob.get("scopes"), http=http)
        tokens["scopes"] = blob.get("scopes")
        self.a.vault.put(self._vault_key(account["ref"]), tokens)
        return tokens["access_token"]

    # ------------------------------------------------------------ connect
    async def run(self, flow) -> dict:
        caps = [c for c in (flow.options.get("capabilities") or DEFAULT_CAPS) if c in CAP_SCOPES]
        scopes = scopes_for(caps)
        client = self.client()
        req = await oauth.begin(client, scopes, login_hint=str(flow.options.get("account_hint") or ""))
        try:
            if not await flow.open_browser(req.url):
                flow.progress("browser", "Open the link shown here in your browser.")
            code = await req.wait(timeout=540)
        finally:
            await req.close()
        flow.progress("verifying")
        async with httpx.AsyncClient(timeout=20) as http:
            tokens = await oauth.exchange(client, req, code, http=http)
            try:
                me = await http.get(f"{self.graph}/me?$select=id,displayName,userPrincipalName,mail",
                                    headers={"Authorization": f"Bearer {tokens['access_token']}"})
            except httpx.HTTPError as exc:
                raise classify(exc) from None
        if me.status_code != 200:
            raise classify(status=me.status_code, body=me.text[:300])
        info = me.json()
        ref = info.get("userPrincipalName") or info.get("mail") or info.get("id")
        tokens["scopes"] = scopes
        self.a.vault.put(self._vault_key(ref), tokens)   # raises keyring_unavailable rather than writing plain text
        return {"ref": ref, "label": info.get("mail") or ref,
                "detail": {"name": info.get("displayName", ""), "capabilities_requested": caps}}

    # ------------------------------------------------------------ probe
    async def probe(self, account: dict) -> dict:
        blob = self.a.vault.get_json(self._vault_key(account["ref"])) or {}
        granted = set(blob.get("scope", "").split()) | set(blob.get("scopes") or [])
        granted_lower = {g.lower().rsplit("/", 1)[-1] for g in granted}
        caps: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=20) as http:
            token = await self._token(account, http)
            h = {"Authorization": f"Bearer {token}"}
            try:
                me = await http.get(f"{self.graph}/me?$select=id,userPrincipalName,mail", headers=h)
            except httpx.HTTPError as exc:
                raise classify(exc) from None
            if me.status_code != 200:
                raise classify(status=me.status_code, body=me.text[:300])
            date = me.headers.get("date", "")
            personal = self._personal(token)
            checks = {"mail_read": "/me/messages?$top=1&$select=id", "calendar_read": "/me/calendars?$top=1&$select=id",
                      "files_read": "/me/drive?$select=id,driveType", "excel": "/me/drive?$select=id",
                      "contacts_read": "/me/contacts?$top=1&$select=id", "teams_read": "/me/joinedTeams?$select=id"}
            for cap, path in checks.items():
                needed = {s.lower() for s in CAP_SCOPES[cap]}
                if not needed & granted_lower:
                    continue
                if cap == "teams_read" and personal:
                    caps[cap] = "unsupported"
                    continue
                try:
                    r = await http.get(self.graph + path, headers=h)
                except httpx.HTTPError:
                    caps[cap] = "error"
                    continue
                caps[cap] = ("verified" if r.status_code == 200 else "missing_permission" if r.status_code in (401, 403)
                             else "unsupported" if r.status_code == 404 else "error")
        for cap in ("mail_send", "calendar_edit"):
            if {s.lower() for s in CAP_SCOPES[cap]} & granted_lower:
                caps[cap] = "granted"
        return {"capabilities": caps, "server_date": date, "detail": {"personal": personal}}

    @staticmethod
    def _personal(token: str) -> bool:
        """Personal Microsoft accounts have the consumers tenant id in the token (when it is a JWT)."""
        import base64
        import json
        try:
            part = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            return claims.get("tid") == PERSONAL_TENANT
        except Exception:
            return False   # Microsoft account tokens are often opaque; Teams then probes for real

    async def disconnect(self, account: dict) -> str:
        self.a.vault.delete(self._vault_key(account["ref"]))
        return ("Removed from Jarvis. To revoke access completely, open account.microsoft.com/privacy/app-access "
                "(personal) or myapps.microsoft.com (work).")
