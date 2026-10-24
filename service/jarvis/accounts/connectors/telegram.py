"""Telegram, two separate things:

* Your Telegram account (a user session, MTProto through Telethon, MIT). Telegram requires every
  client to have its own api_id/api_hash from my.telegram.org; Jarvis has none built in and never
  borrows another app's. The login is Telegram's QR login (auth.exportLoginToken): Jarvis shows the
  `tg://login?token=…` code, you scan it in Telegram > Settings > Devices > Link Desktop Device, the
  token is refreshed when it expires, and a two-step password is asked for when the account has one.
  If QR login is not possible, the phone-number code works instead. The session string goes to the
  keyring. The probe is `users.getFullUser(self)`: it reads, never sends.

* A Telegram bot (Jarvis's own messaging gateway, Bot API): talk to Jarvis from Telegram. The card is
  a form for the @BotFather token; the probe is the bot's own `getMe`.
"""

from __future__ import annotations

import asyncio
import time

from jarvis.accounts import Connector, lifecycle as lc
from jarvis.accounts.errors import AccountError, classify

QR_RETRIES = 8


def _telethon():
    try:
        import telethon  # noqa: F401
        from telethon import TelegramClient, errors
        from telethon.sessions import StringSession
    except ImportError:
        return None
    return TelegramClient, StringSession, errors


def _label(me) -> str:
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or (me.username or str(me.id))
    return name + (f" @{me.username}" if me.username else "")


class TelegramUser(Connector):
    id = "telegram"
    label = "Telegram"
    icon = "telegram"
    group = "messaging"
    auth = "qr"
    keywords = ("telegram", "телеграм", "телеграмм", "tg", "messages", "chats", "сообщения", "чаты", "messagerie")
    description = {"en": "Your own Telegram account, linked like Telegram Desktop by scanning a code.",
                   "ru": "Ваш аккаунт Telegram, связанный как Telegram Desktop — по QR-коду.",
                   "fr": "Votre compte Telegram, associé comme Telegram Desktop en scannant un code."}
    can = ({"en": "Find messages and chats", "ru": "Находить сообщения и чаты", "fr": "Retrouver messages et discussions"},)
    cannot = ({"en": "Send anything without your approval", "ru": "Отправлять без вашего подтверждения",
               "fr": "Envoyer quoi que ce soit sans votre accord"},)
    limitations = ({"en": "Telegram requires your own free API ID and hash from my.telegram.org (once).",
                    "ru": "Telegram требует собственные API ID и hash с my.telegram.org (один раз, бесплатно).",
                    "fr": "Telegram exige votre propre API ID et hash (gratuits) sur my.telegram.org (une fois)."},)
    setup_steps = (
        {"en": "Open my.telegram.org > API development tools, create an app (any name).",
         "ru": "Откройте my.telegram.org > API development tools, создайте приложение (любое имя).",
         "fr": "Ouvrez my.telegram.org > API development tools, créez une application (nom libre)."},
        {"en": "Paste the api_id and api_hash here.", "ru": "Вставьте сюда api_id и api_hash.",
         "fr": "Collez ici api_id et api_hash."},
    )
    setup_fields = ({"name": "api_id", "label": "api_id", "kind": "number"},
                    {"name": "api_hash", "label": "api_hash", "kind": "password"})
    installable = True

    def capabilities(self) -> list[dict]:
        L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
        return [{"id": "read", "label": L("Read chats", "Читать чаты", "Lire les discussions"), "kind": "read",
                 "default": True, "permission": L("Account session", "Сеанс аккаунта", "Session du compte")}]

    def availability(self):
        if _telethon() is None:
            return lc.NOT_CONFIGURED, AccountError("missing_dependency", detail="Telethon (pip install 'jarvis-workos[telegram]')")
        if not (self.a.config.secret("TELEGRAM_API_ID") and self.a.config.secret("TELEGRAM_API_HASH")):
            return lc.NOT_CONFIGURED, AccountError("not_configured", detail="Add your api_id and api_hash from my.telegram.org.")
        return lc.READY, None

    async def configure(self, values: dict) -> str:
        api_id, api_hash = values.get("api_id", "").strip(), values.get("api_hash", "").strip()
        if not api_id.isdigit() or len(api_hash) != 32:
            raise AccountError("not_configured", detail="api_id is a number and api_hash has 32 characters.")
        self.a.config.save_secret("TELEGRAM_API_ID", api_id)
        self.a.config.save_secret("TELEGRAM_API_HASH", api_hash)
        return "Saved in the keyring."

    def _client(self, session: str = ""):
        TelegramClient, StringSession, _ = _telethon()
        return TelegramClient(StringSession(session), int(self.a.config.secret("TELEGRAM_API_ID")),
                              self.a.config.secret("TELEGRAM_API_HASH"), device_model="Jarvis", app_version="jarvis-workos",
                              receive_updates=False)

    def _vault_key(self, ref: str) -> str:
        return f"telegram:{ref}"

    async def run(self, flow) -> dict:
        _, StringSession, errors = _telethon()
        client = self._client()
        try:
            try:
                await client.connect()
            except Exception as exc:
                raise classify(exc) from None
            if flow.options.get("method") == "phone":
                await self._phone_login(client, flow, errors)
            else:
                await self._qr_login(client, flow, errors)
            me = await client.get_me()
            session = client.session.save()
        finally:
            await client.disconnect()
        ref = str(me.id)
        self.a.vault.put(self._vault_key(ref), session)
        return {"ref": ref, "label": _label(me), "detail": {"user_id": me.id}}

    async def _qr_login(self, client, flow, errors) -> None:
        qr = await client.qr_login()
        for _ in range(QR_RETRIES):
            expires = max(5.0, qr.expires.timestamp() - time.time())
            flow.show_qr(qr.url, expires, "Telegram > Settings > Devices > Link Desktop Device")
            restart = asyncio.create_task(flow.restart.wait())
            wait = asyncio.create_task(qr.wait(timeout=expires))
            done, _ = await asyncio.wait({wait, restart}, return_when=asyncio.FIRST_COMPLETED)
            restart.cancel()
            if restart in done:
                wait.cancel()
                flow.restart.clear()
                await qr.recreate()
                continue
            try:
                wait.result()
                return
            except asyncio.TimeoutError:
                await qr.recreate()     # the token expired: a fresh code, same flow
            except errors.SessionPasswordNeededError:
                await self._password(client, flow, errors)
                return
        raise AccountError("qr_expired")

    async def _password(self, client, flow, errors) -> None:
        for attempt in range(3):
            values = await flow.ask([{"name": "password", "label": "Two-step verification password", "kind": "password"}],
                                    "Telegram asks for your two-step password." if not attempt else "That password didn't work.")
            try:
                await client.sign_in(password=values["password"])
                return
            except errors.PasswordHashInvalidError:
                continue
        raise AccountError("consent_denied", technical="two-step password rejected 3 times")

    async def _phone_login(self, client, flow, errors) -> None:
        values = await flow.ask([{"name": "phone", "label": "Phone number (with country code)", "kind": "text"}])
        try:
            sent = await client.send_code_request(values["phone"])
        except errors.FloodWaitError as exc:
            raise AccountError("rate_limited", technical=f"wait {exc.seconds}s") from None
        code = await flow.ask([{"name": "code", "label": "Code from Telegram", "kind": "number"}],
                              "Telegram sent a code to your app.")
        try:
            await client.sign_in(values["phone"], code["code"], phone_code_hash=sent.phone_code_hash)
        except errors.SessionPasswordNeededError:
            await self._password(client, flow, errors)
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
            raise AccountError("consent_denied", technical=type(exc).__name__) from None

    async def probe(self, account: dict) -> dict:
        if _telethon() is None:
            raise AccountError("missing_dependency", detail="Telethon")
        session = self.a.vault.get(self._vault_key(account["ref"]))
        if not session:
            raise AccountError("expired_auth", technical="no saved session")
        _, _, errors = _telethon()
        client = self._client(session)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise AccountError("revoked", technical="session no longer authorized")
            me = await client.get_me()
        except AccountError:
            raise
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError, errors.UserDeactivatedError) as exc:
            raise AccountError("revoked", technical=type(exc).__name__) from None
        except Exception as exc:
            raise classify(exc) from None
        finally:
            await client.disconnect()
        return {"capabilities": {"read": "verified"}, "identity": {"label": _label(me)}}

    async def disconnect(self, account: dict) -> str:
        session = self.a.vault.get(self._vault_key(account["ref"]))
        note = "Removed from Jarvis."
        if session and _telethon():
            client = self._client(session)
            try:
                await client.connect()
                await client.log_out()      # ends the session on Telegram's side too
                note = "Logged out on Telegram and removed from Jarvis."
            except Exception:
                note = "Removed from Jarvis; also end the 'Jarvis' session in Telegram > Settings > Devices."
            finally:
                await client.disconnect()
        self.a.vault.delete(self._vault_key(account["ref"]))
        return note

    async def install(self) -> str:
        import sys
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                                                    "telethon==1.42.0", stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 600)
        if proc.returncode != 0:
            raise AccountError("missing_dependency", detail="Telethon", technical=out.decode(errors="replace")[-300:])
        return "Telethon 1.42.0 installed."

    async def local_state(self) -> dict:
        return {"installed": _telethon() is not None}


class TelegramBot(Connector):
    id = "telegram_bot"
    label = "Telegram bot"
    icon = "telegram"
    group = "messaging"
    auth = "form"
    multi_account = False
    keywords = ("telegram bot", "bot", "бот", "talk from phone", "с телефона", "depuis le téléphone")
    description = {"en": "Talk to Jarvis from Telegram through a bot you create with @BotFather.",
                   "ru": "Говорите с Jarvis из Telegram через бота, созданного в @BotFather.",
                   "fr": "Parlez à Jarvis depuis Telegram via un bot créé avec @BotFather."}
    can = ({"en": "Chat with Jarvis from your phone", "ru": "Общаться с Jarvis с телефона", "fr": "Discuter avec Jarvis depuis votre téléphone"},)
    cannot = ({"en": "Read your personal chats (use Telegram account for that)", "ru": "Читать личные чаты (для этого — аккаунт Telegram)",
               "fr": "Lire vos discussions personnelles (utilisez le compte Telegram)"},)

    @property
    def gateway(self):
        return getattr(self.j, "gateway", None)

    def availability(self):
        if self.gateway is None or "telegram" not in self.gateway.channels:
            return lc.UNSUPPORTED, AccountError("unsupported", detail="The messaging gateway is not running.")
        return lc.READY, None

    def capabilities(self) -> list[dict]:
        L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
        return [{"id": "bot", "label": L("Bot", "Бот", "Bot"), "kind": "read", "default": True}]

    async def run(self, flow) -> dict:
        values = await flow.ask([{"name": "token", "label": "Bot token from @BotFather", "kind": "password"}])
        flow.progress("verifying")
        result = await self.gateway.connect("telegram", {"TELEGRAM_BOT_TOKEN": values["token"]}, {})
        if not result.get("ok"):
            raise classify(body=result.get("message", ""))
        return {"ref": "bot", "label": result.get("account") or "Telegram bot"}

    async def probe(self, account: dict) -> dict:
        ch = self.gateway.channels["telegram"]
        token = ch.secret("TELEGRAM_BOT_TOKEN")
        if not token:
            raise AccountError("expired_auth", technical="no bot token")
        try:
            info = await ch.validate({"TELEGRAM_BOT_TOKEN": token})
        except Exception as exc:
            raise classify(exc, body=str(exc)) from None
        return {"capabilities": {"bot": "verified"}, "identity": {"label": info.get("account", "")}}

    async def disconnect(self, account: dict) -> str:
        return (await self.gateway.op({"action": "disconnect", "channel": "telegram"})).get("message", "")
