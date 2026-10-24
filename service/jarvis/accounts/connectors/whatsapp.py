"""WhatsApp, two separate things:

* Personal WhatsApp (experimental): the Hermes Agent WhatsApp bridge (Baileys, the unofficial
  WhatsApp Web protocol) in its pair-only mode (`bridge.js --pair-only --pair-json`), which prints one
  JSON line per event: started, qr, connected {user}, disconnected {reason}, error. Jarvis renders
  each QR inside the panel, keeps the session in Hermes's own WhatsApp folder (so Hermes uses the
  same link, and no second session is made), and never runs the bridge while Hermes's bridge
  already serves that session. WhatsApp can restrict accounts that use unofficial clients and the
  protocol can break when WhatsApp changes it: the card says so, and nothing is ever sent from here.

* WhatsApp Business (official Cloud API): Jarvis's own gateway channel (gateway/whatsapp.py) with
  its signed webhook. This connector is only the guided form and the health probe (a read of the
  phone number's name and quality, which changes nothing).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import time
from pathlib import Path

import httpx

from jarvis import config as configmod
from jarvis.accounts import Connector, lifecycle as lc
from jarvis.accounts.errors import AccountError, classify

QR_LIFETIME_S = 20          # WhatsApp rotates the pairing QR about every 20 s
MAX_QRS = 6                 # then the code is shown as expired until the user asks for a new one
PROBE_TIMEOUT_S = 30
BRIDGE_PIN = "e0c479382e3609f7878d6e243e47d81f368a920c"   # NousResearch/hermes-agent, scripts/whatsapp-bridge


def _number(jid: str) -> str:
    m = re.match(r"^(\d{5,20})", str(jid or ""))
    return "+" + m.group(1) if m else ""


class WhatsAppPersonal(Connector):
    id = "whatsapp"
    label = "WhatsApp"
    icon = "whatsapp"
    group = "messaging"
    auth = "qr"
    official = False
    experimental = True
    multi_account = False
    keywords = ("whatsapp", "ватсап", "вотсап", "messages", "chat", "сообщения", "messagerie", "mensagens", "zap")
    description = {"en": "Link Jarvis to your own WhatsApp as a linked device, by scanning a code.",
                   "ru": "Подключите Jarvis к вашему WhatsApp как связанное устройство — по QR-коду.",
                   "fr": "Associez Jarvis à votre WhatsApp comme appareil lié, en scannant un code."}
    can = ({"en": "Read your chats (with your permission per contact)", "ru": "Читать чаты (с разрешения для каждого контакта)",
            "fr": "Lire vos discussions (avec votre accord, contact par contact)"},)
    cannot = ({"en": "Send anything during setup or without your approval", "ru": "Отправлять что-либо при настройке или без вашего согласия",
               "fr": "Envoyer quoi que ce soit pendant la configuration ou sans votre accord"},)
    limitations = ({"en": "Experimental and unofficial (WhatsApp Web protocol via Baileys): WhatsApp may restrict "
                          "accounts using unofficial clients, and it can stop working when WhatsApp changes.",
                    "ru": "Экспериментально и неофициально (протокол WhatsApp Web через Baileys): WhatsApp может "
                          "ограничить аккаунт за неофициальный клиент, а связь может перестать работать после обновлений WhatsApp.",
                    "fr": "Expérimental et non officiel (protocole WhatsApp Web via Baileys) : WhatsApp peut restreindre "
                          "les comptes qui utilisent un client non officiel, et cela peut cesser de marcher après ses mises à jour."},
                   {"en": "For an official option use WhatsApp Business (Cloud API).",
                    "ru": "Официальный вариант — WhatsApp Business (Cloud API).",
                    "fr": "Pour une option officielle, utilisez WhatsApp Business (Cloud API)."})
    installable = True

    def capabilities(self) -> list[dict]:
        L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
        return [{"id": "linked", "label": L("Linked device", "Связанное устройство", "Appareil lié"), "kind": "read",
                 "default": True, "permission": L("Linked device access", "Доступ связанного устройства", "Accès appareil lié")}]

    # ------------------------------------------------------------ where things are
    def bridge_dir(self) -> Path | None:
        from jarvis.backends import hermes_home
        candidates = [self.config.get("whatsapp_bridge") or "", configmod.data_dir() / "whatsapp-bridge",
                      hermes_home(self.a.config) / "scripts" / "whatsapp-bridge"]
        for c in candidates:
            if not c:
                continue
            d = Path(os.path.expanduser(str(c)))
            if (d / "bridge.js").is_file() and (d / "node_modules" / "@whiskeysockets" / "baileys").is_dir():
                return d
        return None

    def session_dir(self) -> Path:
        from jarvis.backends import hermes_home
        return hermes_home(self.a.config) / "whatsapp" / "session"

    def node(self) -> str | None:
        return shutil.which("node")

    def availability(self):
        if self.node() is None:
            return lc.NOT_CONFIGURED, AccountError("missing_dependency", detail="Node.js")
        if self.bridge_dir() is None:
            return lc.NOT_CONFIGURED, AccountError("missing_dependency", detail="the WhatsApp bridge (experimental)")
        return lc.READY, None

    def _paired(self) -> dict | None:
        creds = self.session_dir() / "creds.json"
        try:
            data = json.loads(creds.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        me = data.get("me") or {}
        return me if me.get("id") else None

    def _hermes_bridge(self) -> int | None:
        """The pid of a bridge already serving this session (Hermes's gateway), if one is alive."""
        pid_file = self.session_dir() / "bridge.pid"
        try:
            pid = int(pid_file.read_text().split()[0])
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except (OSError, ValueError, IndexError):
            return None
        return pid if "node" in cmdline and str(self.session_dir()) in cmdline else None

    async def existing(self) -> list[dict]:
        me = self._paired()
        if not me:
            return []
        number = _number(me.get("id", ""))
        return [{"ref": number or me["id"].split("@")[0], "label": (me.get("name") or "") + (f" {number}" if number else ""),
                 "detail": {"experimental": True, "session": "hermes"}}]

    # ------------------------------------------------------------ the bridge in pair-only mode
    async def _spawn(self) -> asyncio.subprocess.Process:
        bridge = self.bridge_dir()
        session = self.session_dir()
        session.mkdir(parents=True, exist_ok=True)
        os.chmod(session, 0o700)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TZ", "NODE_EXTRA_CA_CERTS",
                                                             "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")}
        env["WHATSAPP_MODE"] = "self-chat"
        return await asyncio.create_subprocess_exec(
            self.node(), str(bridge / "bridge.js"), "--pair-only", "--pair-json", "--session", str(session),
            cwd=str(bridge), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True)

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    @staticmethod
    async def _events(proc: asyncio.subprocess.Process):
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            try:
                event = json.loads(line)
            except ValueError:
                continue    # human text from the bridge; never logged (it may contain the QR)
            if isinstance(event, dict) and event.get("event"):
                yield event

    async def run(self, flow) -> dict:
        if self._hermes_bridge():
            me = self._paired()
            if me:
                return self._identity(me)
            raise AccountError("busy", technical="Hermes's WhatsApp bridge is running for this session")
        attempts = 0
        while True:
            attempts += 1
            proc = await self._spawn()
            flow.progress("starting", "")
            qrs, user, error = 0, None, ""
            restart = asyncio.create_task(flow.restart.wait())
            try:
                events = self._events(proc)
                while True:
                    nxt = asyncio.create_task(events.__anext__())
                    done, _ = await asyncio.wait({nxt, restart}, return_when=asyncio.FIRST_COMPLETED)
                    if restart in done:
                        nxt.cancel()
                        break
                    try:
                        event = nxt.result()
                    except StopAsyncIteration:
                        break
                    kind = event["event"]
                    if kind == "qr":
                        qrs += 1
                        if qrs > MAX_QRS:
                            break
                        flow.show_qr(str(event.get("qr", "")), QR_LIFETIME_S)
                    elif kind == "connected":
                        user = event.get("user") or {}
                        flow.progress("verifying")
                        break
                    elif kind == "error":
                        error = str(event.get("error") or "")
                        break
            finally:
                restart.cancel()
                if user is None:
                    await self._stop(proc)
            if user is not None:
                try:   # pair-only exits by itself after saving the credentials
                    await asyncio.wait_for(proc.wait(), 10)
                except asyncio.TimeoutError:
                    await self._stop(proc)
                me = self._paired() or {"id": user.get("id", ""), "name": user.get("name", "")}
                return self._identity(me)
            if flow.restart.is_set():
                flow.restart.clear()
                continue
            if error == "logged_out" and attempts == 1:
                self._set_aside_session()     # the old link was removed on the phone: start a clean pairing
                continue
            if error:
                raise AccountError("logged_out" if error == "logged_out" else "unknown", technical=error[:200])
            if qrs:
                flow.error = None
                flow.qr = None
                flow.progress("qr_expired")
                await flow.restart.wait()      # "show a new code"
                flow.restart.clear()
                continue
            raise AccountError("network", technical="the bridge stopped before showing a code")

    def _identity(self, me: dict) -> dict:
        number = _number(me.get("id", ""))
        return {"ref": number or str(me.get("id", "")).split("@")[0], "label": f"{me.get('name') or ''} {number}".strip(),
                "detail": {"experimental": True, "session": "hermes"}}

    def _set_aside_session(self) -> None:
        session = self.session_dir()
        if session.exists():
            aside = session.with_name(f"session.logged-out-{int(time.time())}")
            session.rename(aside)

    # ------------------------------------------------------------ probe
    async def probe(self, account: dict) -> dict:
        if self.availability()[0] != lc.READY:
            raise AccountError("missing_dependency", detail="the WhatsApp bridge")
        if self._paired() is None:
            raise AccountError("logged_out", technical="no saved session")
        if self._hermes_bridge():
            port = 3000
            try:
                async with httpx.AsyncClient(timeout=5) as http:
                    r = await http.get(f"http://127.0.0.1:{port}/health")
                state = r.json().get("status")
            except Exception as exc:
                raise classify(exc) from None
            if state == "connected":
                return {"capabilities": {"linked": "verified"}}
            raise AccountError("network", technical=f"bridge state {state}")
        # Connect once with the saved session in pair-only mode: it either opens (verified) or asks for a
        # QR (the session is no longer valid). Nothing is sent and the process exits by itself.
        proc = await self._spawn()
        try:
            async def watch():
                async for event in self._events(proc):
                    if event["event"] == "connected":
                        return "verified"
                    if event["event"] == "qr":
                        return "expired"
                    if event["event"] == "error":
                        return "logged_out" if event.get("error") == "logged_out" else "error"
                return "error"
            outcome = await asyncio.wait_for(watch(), PROBE_TIMEOUT_S)
        except asyncio.TimeoutError:
            outcome = "timeout"
        finally:
            await self._stop(proc)
        if outcome == "verified":
            return {"capabilities": {"linked": "verified"}}
        if outcome in ("expired", "logged_out"):
            raise AccountError("logged_out")
        raise AccountError("network", technical=f"probe: {outcome}")

    async def disconnect(self, account: dict) -> str:
        if self._hermes_bridge():
            raise AccountError("busy", technical="stop Hermes's WhatsApp gateway first")
        self._set_aside_session()
        return ("Jarvis forgot this WhatsApp link. On your phone, open WhatsApp > Settings > Linked devices and log out "
                "the 'Hermes Agent' device to finish.")

    async def install(self) -> str:
        script = Path(__file__).resolve().parents[4] / "omarchy" / "whatsapp" / "setup.sh"
        if not script.is_file():
            raise AccountError("missing_dependency", detail="omarchy/whatsapp/setup.sh (part of the Jarvis checkout)")
        proc = await asyncio.create_subprocess_exec("bash", str(script), str(configmod.data_dir() / "whatsapp-bridge"),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 900)
        if proc.returncode != 0:
            raise AccountError("missing_dependency", detail="the WhatsApp bridge",
                               technical=out.decode(errors="replace")[-400:])
        return f"WhatsApp bridge installed (hermes-agent {BRIDGE_PIN[:10]}, locked npm dependencies)."

    async def local_state(self) -> dict:
        return {"installed": self.bridge_dir() is not None}


class WhatsAppBusiness(Connector):
    id = "whatsapp_business"
    label = "WhatsApp Business"
    icon = "whatsapp"
    group = "messaging"
    auth = "form"
    multi_account = False
    keywords = ("whatsapp business", "whatsapp cloud", "business", "клиенты", "бизнес", "entreprise", "clients")
    cost = {"en": "Meta charges per conversation beyond the free tier", "ru": "Meta берёт плату за диалоги сверх бесплатного лимита",
            "fr": "Meta facture les conversations au-delà du quota gratuit"}
    description = {"en": "Meta's official WhatsApp Business Cloud API: customers message your business number.",
                   "ru": "Официальный WhatsApp Business Cloud API от Meta: клиенты пишут на бизнес-номер.",
                   "fr": "L'API officielle WhatsApp Business Cloud de Meta : vos clients écrivent à votre numéro pro."}
    can = ({"en": "Answer customers who wrote in the last 24 hours", "ru": "Отвечать клиентам, написавшим за последние 24 часа",
            "fr": "Répondre aux clients qui ont écrit dans les dernières 24 h"},)
    cannot = ({"en": "Read groups or your personal chats", "ru": "Читать группы и личные чаты",
               "fr": "Lire les groupes ou vos discussions personnelles"},
              {"en": "Start conversations without an approved template", "ru": "Начинать диалог без одобренного шаблона",
               "fr": "Lancer une conversation sans modèle approuvé"})
    limitations = ({"en": "Needs a Meta Business app, a business phone number and a public HTTPS webhook (a tunnel).",
                    "ru": "Нужны приложение Meta Business, бизнес-номер и публичный HTTPS-вебхук (туннель).",
                    "fr": "Nécessite une app Meta Business, un numéro professionnel et un webhook HTTPS public (tunnel)."},)
    setup_steps = (
        {"en": "developers.facebook.com > Create app (Business) > add WhatsApp.",
         "ru": "developers.facebook.com > Создать приложение (Business) > добавить WhatsApp.",
         "fr": "developers.facebook.com > Créer une app (Business) > ajouter WhatsApp."},
        {"en": "Copy the phone number ID, a permanent system-user token and the app secret.",
         "ru": "Скопируйте ID номера, постоянный токен системного пользователя и секрет приложения.",
         "fr": "Copiez l'ID du numéro, un jeton utilisateur système permanent et le secret de l'app."},
    )

    @property
    def gateway(self):
        return getattr(self.j, "gateway", None)

    def availability(self):
        if self.gateway is None or "whatsapp" not in self.gateway.channels:
            return lc.UNSUPPORTED, AccountError("unsupported", detail="The messaging gateway is not running.")
        return lc.READY, None

    def capabilities(self) -> list[dict]:
        L = lambda en, ru, fr: {"en": en, "ru": ru, "fr": fr}   # noqa: E731
        return [{"id": "number", "label": L("Business number", "Бизнес-номер", "Numéro professionnel"), "kind": "read",
                 "default": True, "permission": "whatsapp_business_messaging"},
                {"id": "webhook", "label": L("Receives messages (webhook)", "Приём сообщений (вебхук)", "Réception (webhook)"),
                 "kind": "read", "default": True}]

    async def run(self, flow) -> dict:
        ch = self.gateway.channels["whatsapp"]
        values = await flow.ask([
            {"name": "phone_id", "label": "Phone number ID", "kind": "text", "hint": "WhatsApp > API Setup"},
            {"name": "token", "label": "Access token (system user)", "kind": "password"},
            {"name": "app_secret", "label": "App secret", "kind": "password", "hint": "App settings > Basic"},
        ], "From your Meta app.")
        flow.progress("verifying")
        result = await self.gateway.connect("whatsapp", {"WHATSAPP_ACCESS_TOKEN": values["token"],
                                                         "WHATSAPP_APP_SECRET": values["app_secret"]},
                                            {"gateway.whatsapp_phone_id": values["phone_id"]})
        if not result.get("ok"):
            raise classify(body=result.get("message", ""))
        label = result.get("account") or values["phone_id"]
        return {"ref": values["phone_id"], "label": label, "detail": {"webhook": ch.setup().get("webhook", "")}}

    async def probe(self, account: dict) -> dict:
        ch = self.gateway.channels["whatsapp"]
        try:
            info = await ch.graph("GET", f"{ch.phone_id}?fields=display_phone_number,verified_name,quality_rating", retries=1)
        except Exception as exc:
            raise classify(exc, body=str(exc)) from None
        caps = {"number": "verified", "webhook": "verified" if ch.state == "connected" else "error"}
        return {"capabilities": caps, "identity": {"label": f"{info.get('verified_name', '')} "
                                                             f"{info.get('display_phone_number', '')}".strip()},
                "detail": {"quality": info.get("quality_rating", ""), "webhook": ch.setup().get("webhook", ""),
                           "verify_token_set": bool(ch.secret("WHATSAPP_VERIFY_TOKEN"))}}

    async def disconnect(self, account: dict) -> str:
        result = await self.gateway.op({"action": "disconnect", "channel": "whatsapp"})
        return result.get("message", "")
