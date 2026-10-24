"""What went wrong with an account, said the way a person would understand it, plus what to do next.

Every failure an account flow or health probe can hit is reduced to one code. The code decides the
message (Russian, English, French, Portuguese), whether it is transient (retry quietly with backoff)
and which recovery buttons the card shows. These actions work without any AI model: they are plain
daemon requests. Technical details (status codes, provider error names) go in `details` only, and
never contain tokens, codes or QR contents (`scrub` removes anything that looks like one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

# action ids the UI knows: reconnect, retry, open_signin, details, disconnect, help, setup, install, new_qr
ACTIONS = {
    "expired_auth": ("reconnect", "disconnect"),
    "revoked": ("reconnect", "disconnect"),
    "consent_denied": ("retry", "help"),
    "missing_permissions": ("reconnect", "help"),
    "admin_required": ("help", "retry"),
    "wrong_account": ("retry", "disconnect"),
    "timeout": ("retry",),
    "cancelled": ("retry",),
    "qr_expired": ("new_qr",),
    "mfa_required": (),
    "network": ("retry",),
    "provider_down": ("retry",),
    "rate_limited": ("retry",),
    "clock_skew": ("retry", "help"),
    "missing_dependency": ("install", "help"),
    "not_configured": ("setup", "help"),
    "keyring_unavailable": ("help",),
    "bad_session": ("reconnect", "disconnect"),
    "logged_out": ("reconnect",),
    "invalid_callback": ("retry",),
    "unsupported": ("help",),
    "busy": (),
    "unknown": ("retry", "help"),
}
TRANSIENT = frozenset({"network", "provider_down", "rate_limited", "timeout"})

MESSAGES: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "expired_auth": ("{p} needs you to sign in again", "Your files are safe and your Jarvis settings are unchanged."),
        "revoked": ("{p} access was removed", "Access was turned off in your account settings. Reconnect to use it again."),
        "consent_denied": ("Sign-in was not allowed", "{p} didn't get permission. Nothing was changed; you can try again."),
        "missing_permissions": ("{p} is connected, but some permissions are missing",
                                "Reconnect and tick the permissions for the features you want."),
        "admin_required": ("Your organization must approve Jarvis",
                           "{p} says an administrator has to allow this app for your work account."),
        "wrong_account": ("A different account was chosen", "You signed in as {detail}. Try again with the account you meant."),
        "timeout": ("Sign-in took too long", "Nothing was changed. Start again when you're ready."),
        "cancelled": ("Connection cancelled", "Nothing was changed."),
        "qr_expired": ("The code expired", "Codes only last a short time. Show a new one and scan it right away."),
        "mfa_required": ("One more step", "{p} asks for your second-factor password."),
        "network": ("No connection to {p}", "Check the internet connection. Jarvis will try again by itself."),
        "provider_down": ("{p} isn't answering right now", "This is on their side. Jarvis will try again later."),
        "rate_limited": ("{p} asked us to slow down", "Jarvis will try again in a few minutes."),
        "clock_skew": ("This computer's clock is off", "Sign-ins fail when the time is wrong. Turn on automatic time, then retry."),
        "missing_dependency": ("A component isn't installed", "{p} needs {detail}. Install it, then connect."),
        "not_configured": ("{p} needs a one-time setup", "{detail}"),
        "keyring_unavailable": ("The password keyring is locked or missing",
                                "Jarvis keeps sign-ins in your desktop keyring. Unlock it (or install gnome-keyring) and retry."),
        "bad_session": ("The saved {p} session is damaged", "Reconnect to make a fresh one. Nothing else is affected."),
        "logged_out": ("{p} was logged out", "The linked device was removed on your phone. Link it again to continue."),
        "invalid_callback": ("That sign-in response didn't match", "For your safety it was ignored. Start the sign-in again."),
        "unsupported": ("Not available for this account", "{detail}"),
        "busy": ("Already connecting", "Finish the sign-in that is already open."),
        "unknown": ("{p} couldn't be connected", "Something unexpected happened. Try again, or open the details."),
    },
    "ru": {
        "expired_auth": ("{p}: нужно войти снова", "Ваши файлы в безопасности, настройки Jarvis не изменились."),
        "revoked": ("Доступ к {p} отозван", "Доступ выключили в настройках аккаунта. Подключите заново, чтобы пользоваться."),
        "consent_denied": ("Вход не разрешён", "{p} не получил разрешение. Ничего не изменилось — можно попробовать снова."),
        "missing_permissions": ("{p} подключён, но не все разрешения выданы",
                                "Подключите заново и отметьте разрешения для нужных функций."),
        "admin_required": ("Нужно одобрение организации", "{p}: администратор должен разрешить это приложение для рабочего аккаунта."),
        "wrong_account": ("Выбран другой аккаунт", "Вы вошли как {detail}. Попробуйте снова с нужным аккаунтом."),
        "timeout": ("Вход занял слишком много времени", "Ничего не изменилось. Начните заново, когда будете готовы."),
        "cancelled": ("Подключение отменено", "Ничего не изменилось."),
        "qr_expired": ("Код устарел", "Коды действуют недолго. Покажите новый и сразу отсканируйте."),
        "mfa_required": ("Ещё один шаг", "{p} просит пароль двухэтапной проверки."),
        "network": ("Нет связи с {p}", "Проверьте интернет. Jarvis попробует снова сам."),
        "provider_down": ("{p} сейчас не отвечает", "Проблема на их стороне. Jarvis попробует позже."),
        "rate_limited": ("{p} просит подождать", "Jarvis попробует снова через несколько минут."),
        "clock_skew": ("Часы компьютера сбиты", "При неверном времени вход не работает. Включите автоматическое время и повторите."),
        "missing_dependency": ("Компонент не установлен", "{p} нужен {detail}. Установите и подключите."),
        "not_configured": ("{p}: нужна разовая настройка", "{detail}"),
        "keyring_unavailable": ("Хранилище паролей заблокировано или отсутствует",
                                "Jarvis хранит входы в связке ключей. Разблокируйте её (или установите gnome-keyring) и повторите."),
        "bad_session": ("Сохранённый вход в {p} повреждён", "Подключите заново. Остальное не затронуто."),
        "logged_out": ("Выход из {p}", "Связанное устройство удалили на телефоне. Свяжите снова."),
        "invalid_callback": ("Ответ входа не совпал", "Для безопасности он проигнорирован. Начните вход заново."),
        "unsupported": ("Недоступно для этого аккаунта", "{detail}"),
        "busy": ("Подключение уже идёт", "Завершите уже открытый вход."),
        "unknown": ("Не удалось подключить {p}", "Что-то пошло не так. Попробуйте снова или откройте подробности."),
    },
    "fr": {
        "expired_auth": ("{p} vous demande de vous reconnecter", "Vos fichiers sont en sécurité et vos réglages Jarvis n'ont pas changé."),
        "revoked": ("L'accès à {p} a été retiré", "L'accès a été désactivé dans votre compte. Reconnectez-le pour l'utiliser."),
        "consent_denied": ("Connexion non autorisée", "{p} n'a pas reçu l'autorisation. Rien n'a changé ; vous pouvez réessayer."),
        "missing_permissions": ("{p} est connecté, mais des autorisations manquent",
                                "Reconnectez-vous et cochez les autorisations des fonctions voulues."),
        "admin_required": ("Votre organisation doit approuver Jarvis",
                           "{p} indique qu'un administrateur doit autoriser cette application pour votre compte pro."),
        "wrong_account": ("Un autre compte a été choisi", "Vous êtes connecté en tant que {detail}. Réessayez avec le bon compte."),
        "timeout": ("La connexion a pris trop de temps", "Rien n'a changé. Recommencez quand vous voulez."),
        "cancelled": ("Connexion annulée", "Rien n'a changé."),
        "qr_expired": ("Le code a expiré", "Les codes durent peu. Affichez-en un nouveau et scannez-le tout de suite."),
        "mfa_required": ("Encore une étape", "{p} demande votre mot de passe de validation en deux étapes."),
        "network": ("Pas de connexion à {p}", "Vérifiez Internet. Jarvis réessaiera tout seul."),
        "provider_down": ("{p} ne répond pas pour le moment", "Le problème vient de leur côté. Jarvis réessaiera plus tard."),
        "rate_limited": ("{p} demande de ralentir", "Jarvis réessaiera dans quelques minutes."),
        "clock_skew": ("L'horloge de l'ordinateur est décalée", "Les connexions échouent si l'heure est fausse. Activez l'heure automatique, puis réessayez."),
        "missing_dependency": ("Un composant n'est pas installé", "{p} a besoin de {detail}. Installez-le, puis connectez."),
        "not_configured": ("{p} demande une configuration unique", "{detail}"),
        "keyring_unavailable": ("Le trousseau de mots de passe est verrouillé ou absent",
                                "Jarvis garde les connexions dans le trousseau. Déverrouillez-le (ou installez gnome-keyring) et réessayez."),
        "bad_session": ("La session {p} enregistrée est abîmée", "Reconnectez-vous pour en créer une nouvelle. Rien d'autre n'est touché."),
        "logged_out": ("Déconnecté de {p}", "L'appareil associé a été retiré sur votre téléphone. Associez-le à nouveau."),
        "invalid_callback": ("La réponse de connexion ne correspond pas", "Par sécurité, elle a été ignorée. Recommencez la connexion."),
        "unsupported": ("Indisponible pour ce compte", "{detail}"),
        "busy": ("Connexion déjà en cours", "Terminez la connexion déjà ouverte."),
        "unknown": ("Impossible de connecter {p}", "Un imprévu est survenu. Réessayez ou ouvrez les détails."),
    },
    "pt": {
        "expired_auth": ("{p} precisa que você entre de novo", "Seus arquivos estão seguros e as configurações do Jarvis não mudaram."),
        "revoked": ("O acesso a {p} foi removido", "O acesso foi desligado na sua conta. Conecte de novo para usar."),
        "consent_denied": ("A entrada não foi autorizada", "{p} não recebeu permissão. Nada mudou; pode tentar de novo."),
        "missing_permissions": ("{p} está conectado, mas faltam permissões", "Conecte de novo e marque as permissões que quiser."),
        "admin_required": ("Sua organização precisa aprovar o Jarvis", "{p}: um administrador precisa permitir este app na conta de trabalho."),
        "wrong_account": ("Outra conta foi escolhida", "Você entrou como {detail}. Tente de novo com a conta certa."),
        "timeout": ("A entrada demorou demais", "Nada mudou. Recomece quando quiser."),
        "cancelled": ("Conexão cancelada", "Nada mudou."),
        "qr_expired": ("O código expirou", "Os códigos duram pouco. Mostre um novo e escaneie logo."),
        "mfa_required": ("Mais um passo", "{p} pede sua senha de verificação em duas etapas."),
        "network": ("Sem conexão com {p}", "Verifique a internet. O Jarvis tentará de novo sozinho."),
        "provider_down": ("{p} não está respondendo agora", "O problema é do lado deles. O Jarvis tentará mais tarde."),
        "rate_limited": ("{p} pediu para ir mais devagar", "O Jarvis tentará de novo em alguns minutos."),
        "clock_skew": ("O relógio do computador está errado", "Com a hora errada a entrada falha. Ative a hora automática e tente de novo."),
        "missing_dependency": ("Um componente não está instalado", "{p} precisa de {detail}. Instale e conecte."),
        "not_configured": ("{p} precisa de uma configuração única", "{detail}"),
        "keyring_unavailable": ("O chaveiro de senhas está bloqueado ou ausente",
                                "O Jarvis guarda as entradas no chaveiro. Desbloqueie-o (ou instale o gnome-keyring) e tente de novo."),
        "bad_session": ("A sessão salva do {p} está corrompida", "Conecte de novo para criar outra. Nada mais é afetado."),
        "logged_out": ("Saiu do {p}", "O aparelho conectado foi removido no celular. Conecte de novo."),
        "invalid_callback": ("A resposta da entrada não confere", "Por segurança, foi ignorada. Recomece a entrada."),
        "unsupported": ("Indisponível para esta conta", "{detail}"),
        "busy": ("Já conectando", "Termine a entrada que já está aberta."),
        "unknown": ("Não foi possível conectar {p}", "Algo inesperado aconteceu. Tente de novo ou veja os detalhes."),
    },
}

_SECRETS = re.compile(r"(?i)(access_token|refresh_token|id_token|code|token|secret|password|qr|auth)([=:\"' ]+)[^\s&\"',}]{6,}")
_LONG = re.compile(r"[A-Za-z0-9_\-+/=.]{40,}")


def scrub(text: str) -> str:
    """Remove anything that could be a credential from text shown as 'details' or logged."""
    text = _SECRETS.sub(lambda m: m.group(1) + m.group(2) + "…", str(text or ""))
    return _LONG.sub("…", text)[:400]


@dataclass
class AccountError(Exception):
    code: str
    detail: str = ""       # shown inside the message ({detail}); must be safe to show
    technical: str = ""    # expandable details; scrubbed

    def __str__(self) -> str:
        return f"{self.code}: {self.technical or self.detail}"

    @property
    def transient(self) -> bool:
        return self.code in TRANSIENT


def describe(code: str, provider: str, lang: str = "en", detail: str = "", technical: str = "") -> dict:
    table = MESSAGES.get(lang) or MESSAGES["en"]
    title, body = table.get(code) or table["unknown"]
    return {"code": code if code in ACTIONS else "unknown", "title": title.format(p=provider, detail=detail),
            "body": body.format(p=provider, detail=detail), "actions": list(ACTIONS.get(code, ACTIONS["unknown"])),
            "transient": code in TRANSIENT, "details": scrub(technical)}


# Provider error names -> codes (OAuth 2.0 RFC 6749, Microsoft identity AADSTS codes, Google API reasons).
PATTERNS = (
    (re.compile(r"invalid_grant|token (?:has been )?expired|AADSTS70008|AADSTS700082|AADSTS50173", re.I), "expired_auth"),
    (re.compile(r"has been revoked|AADSTS50133|AADSTS70043|token_revoked", re.I), "revoked"),
    (re.compile(r"AADSTS90094|AADSTS90008|AADSTS65001|admin_policy_enforced|admin approval|AADSTS53003|"
                r"org_internal|restricted_client", re.I), "admin_required"),
    (re.compile(r"access_denied|AADSTS65004|user declined", re.I), "consent_denied"),
    (re.compile(r"insufficient[_ ](?:authentication[_ ])?scopes?|ACCESS_TOKEN_SCOPE_INSUFFICIENT|ErrorAccessDenied|"
                r"Authorization_RequestDenied", re.I), "missing_permissions"),
    (re.compile(r"AADSTS700016|invalid_client|unauthorized_client|AADSTS7000218", re.I), "not_configured"),
    (re.compile(r"AADSTS50020|AADSTS500200", re.I), "unsupported"),
    (re.compile(r"AADSTS50058|AADSTS50079|AADSTS50076|SESSION_PASSWORD_NEEDED", re.I), "mfa_required"),
    (re.compile(r"clock skew|AADSTS700024|not yet valid|issued in the future|iat.*future", re.I), "clock_skew"),
    (re.compile(r"rate.?limit|too many requests|FLOOD_WAIT|quotaExceeded|userRateLimitExceeded", re.I), "rate_limited"),
)


def classify(exc: BaseException | None = None, *, status: int = 0, body: str = "") -> AccountError:
    """One AccountError for an exception and/or an HTTP status and response text."""
    if isinstance(exc, AccountError):
        return exc
    text = f"{body} {exc or ''}"
    for pattern, code in PATTERNS:
        if pattern.search(text):
            return AccountError(code, technical=scrub(f"{status or ''} {text}".strip()))
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return AccountError("network", technical=type(exc).__name__)
    if isinstance(exc, (httpx.TransportError, ConnectionError, OSError)):
        return AccountError("network", technical=type(exc).__name__)
    if status == 401:
        return AccountError("expired_auth", technical=scrub(f"401 {body}"))
    if status == 403:
        return AccountError("missing_permissions", technical=scrub(f"403 {body}"))
    if status == 429:
        return AccountError("rate_limited", technical="429")
    if status >= 500:
        return AccountError("provider_down", technical=f"{status}")
    return AccountError("unknown", technical=scrub(f"{status or ''} {type(exc).__name__ if exc else ''} {body}".strip()))


def skew_s(date_header: str, now: float) -> float:
    """Seconds this computer's clock differs from a server's Date header (0 when unknown)."""
    from email.utils import parsedate_to_datetime
    try:
        server = parsedate_to_datetime(date_header).timestamp()
    except (TypeError, ValueError, IndexError):
        return 0.0
    return now - server


CLOCK_SKEW_LIMIT_S = 300
