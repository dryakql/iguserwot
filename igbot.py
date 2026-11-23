#!/usr/bin/env python3
"""
ρ 𝕣 ꪜ 𝕣 अब्बू ☽  BOT  -  Instagram Group Chat Bot (instagrapi)

Just run:   python ig_gc_bot.py

On start it will
  1. auto-install every missing pip package,
  2. show the flashy banner + credits,
  3. ask in the terminal for your sessionid, owner username and welcome text,
  4. fetch group chats and let you choose which ones to ignore,
  5. print the commands & rules menu, and stream live logs.

While it runs, type a terminal command + Enter at any time:
  help | rules | status | groups | ignore | unignore | ignored | sudo | rate | stats | verbose | credits | clear | quit

Get the sessionid: log in to instagram.com with the BOT account -> DevTools (F12)
-> Application/Storage -> Cookies -> instagram.com -> copy the "sessionid" value.
(Treat it like a password.)

Or log in with USERNAME + PASSWORD instead (pick option 2 at the first prompt):
  - the saved session (session.json) is reused, so a real login only happens when it is needed
  - 2FA: type the code when asked, or set IG_TOTP_SEED (the 2FA secret key) and it is automatic
  - environment variables skip the prompts:  IG_SESSIONID   or   IG_USERNAME + IG_PASSWORD [+ IG_TOTP_SEED]
  - optional proxy (best: one per bot account):  IG_PROXY=http://user:pass@host:port

STAYING UNDER INSTAGRAM'S RATE LIMITS (all optional, all environment variables)
  POLL_MIN / POLL_MAX   seconds between inbox checks (default 3-5, never below 2)
  IDLE_MAX_MULT         quiet chats are polled up to this many times slower (default 3, 1 = off)
  API_DELAY_MIN / MAX   random pause before every API call (default 0.3-0.9s)
  SEND_CAP_PER_MIN      most messages/actions per minute (default 20)
  Type  rate  in the terminal to see cool-down, poll speed and how many requests were sent.

MULTIPLE BOT ACCOUNTS (no pasting in the terminal)
  python ig_gc_bot_updated.py --init     -> creates a template accounts.json
  Put each bot's sessionid (or "sessionid_env") in it, then just run the script again.
  Every account runs as its own process in bots/<name>/ with its own session + settings,
  and its logs are shown with a [name] prefix. Use --single to ignore accounts.json
  and run one bot with the usual prompts.

ADD MORE BOTS FROM CHAT (no terminal pasting at all)
  As the owner, send this to any bot (in a GC it watches, or - safer - in a private DM with the bot):
      !addbot <sessionid>                     -> new bot starts (auto name bot2, bot3 ...)
      !addbot <sessionid> mybot               -> choose the name
      !addbot <sessionid> mybot .             -> choose the name AND a different command prefix
      !addbot login <username> <password> [name] [prefix]   -> same, with username + password
                                                 (accounts with 2FA: use a sessionid instead)
      !bots                                   -> list running bots
      !stopbot <name>                         -> stop one (and forget it)
  The new bot gets the same owner. Its session is saved in bots/<name>/, so it comes back by
  itself the next time you start the script. UNSEND the message after sending it: a sessionid
  is a password and everybody in a GC can read it.

NOTES
- The bot account must be a group ADMIN to kick/add people.
- Use a separate account for the bot. Automation violates Instagram's ToS.
- Instagram doesn't let bots delete other people's messages, so moderation
  works through warnings + kicks.
"""

# ============================ AUTO-INSTALL ===================================
import importlib
import importlib.util
import os
import subprocess
import sys

if os.name == "nt":
    os.system("")  # enables ANSI colours in the Windows console

REQUIRED = {  # import name -> pip name
    "instagrapi": "instagrapi",
    "psutil": "psutil",
    "colorama": "colorama",
}


def ensure_packages() -> None:
    tag = "\033[38;5;213m\033[1m[setup]\033[0m"
    missing = [pip for mod, pip in REQUIRED.items() if importlib.util.find_spec(mod) is None]
    if not missing:
        if os.getenv("BOT_CHILD") != "1":
            print(f"{tag} ✔ all packages already installed ({', '.join(REQUIRED.values())})")
        return
    print(f"{tag} Installing missing packages: \033[38;5;51m{', '.join(missing)}\033[0m ...")
    if subprocess.call([sys.executable, "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL) != 0:
        print(f"{tag} pip not found - bootstrapping it with ensurepip ...")
        subprocess.call([sys.executable, "-m", "ensurepip", "--upgrade"])
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", *missing]
    code = subprocess.call(cmd)
    if code != 0:  # externally-managed environments (Debian/Ubuntu/Termux etc.)
        code = subprocess.call(cmd + ["--break-system-packages"])
    if code != 0:
        sys.exit(f"{tag} \033[38;5;196mpip install failed.\033[0m Install manually: pip install " + " ".join(missing))
    importlib.invalidate_caches()
    print(f"{tag} \033[38;5;46m✔ all packages installed.\033[0m\n")


ensure_packages()

# ================================ IMPORTS ====================================
import re
import json
import hmac
import base64
import hashlib
import struct
import inspect
import copy
import time
import random
import getpass
import logging
import platform
import shutil
import threading
import unicodedata
import datetime as dt
from collections import deque
from pathlib import Path
from urllib.parse import unquote

import psutil
import colorama
from instagrapi import Client
from instagrapi.exceptions import (
    LoginRequired, ChallengeRequired, ClientThrottledError, PleaseWaitFewMinutes,
    RateLimitError, FeedbackRequired,
)
import instagrapi.exceptions as igx


def _exc_class(name: str):
    """An instagrapi exception class, or a harmless stand-in on versions that don't have it."""
    return getattr(igx, name, None) or type(name, (Exception,), {})


TwoFactorRequired = _exc_class("TwoFactorRequired")
BadPassword = _exc_class("BadPassword")
BadCredentials = _exc_class("BadCredentials")

try:
    colorama.just_fix_windows_console()
except Exception:
    colorama.init()
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ================================ CONFIG =====================================
PREFIX = os.getenv("PREFIX", "!")
OWNER_USERNAME = ""                 # filled in from the terminal prompt
OWNER_PK = ""                       # owner's user id (resolved at login)
MY_PK = ""                          # bot account's user id (resolved at login)
BOT_NAME = "ρ 𝕣 ꪜ 𝕣 अब्बू ☽"
HEADLESS = os.getenv("HEADLESS") == "1"   # set by the multi-account launcher: no prompts / console

SESSION_FILE = Path("session.json")
STATE_FILE = Path("bot_state.json")
# ---- polling / rate-limit settings (all can be overridden with environment variables) ----
# Seconds between inbox checks (randomised). Every check is ONE request (the newest 20 chats).
# The bot slows itself down automatically after a rate-limit hit and speeds up again when calm.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# Polling faster than every 2s is the #1 reason bots get rate-limited, so that is the floor.
POLL_MIN = max(2.0, _env_float("POLL_MIN", 3.0))
POLL_MAX = max(POLL_MIN + 1.0, _env_float("POLL_MAX", 5.0))
# When nobody has written for a while the bot polls less often (up to x IDLE_MAX_MULT) and returns to
# full speed the moment something happens. IDLE_MAX_MULT=1 turns this off.
IDLE_AFTER = 20                                    # quiet polls before it starts slowing down
IDLE_RAMP = 40                                     # further quiet polls for each extra x1 of slow-down
IDLE_MAX_MULT = max(1.0, _env_float("IDLE_MAX_MULT", 3.0))
API_DELAY_MAX = max(0.0, _env_float("API_DELAY_MAX", 0.9))     # random pause before every API call
API_DELAY_MIN = min(max(0.0, _env_float("API_DELAY_MIN", 0.3)), API_DELAY_MAX)   # (MAX=0 switches it off)
IG_PROXY = os.getenv("IG_PROXY", "").strip()       # optional: http://user:pass@host:port
# How many inbox threads to index ONCE at startup (fetched 20 per request, with short pauses).
try:
    THREADS_TO_SCAN = max(1, min(200, int(os.getenv("THREADS_TO_SCAN", "60"))))
except ValueError:
    THREADS_TO_SCAN = 60
SEND_GAP_MIN = float(os.getenv("SEND_GAP_MIN", "1.2"))      # pause between bot messages/actions
SEND_GAP_MAX = float(os.getenv("SEND_GAP_MAX", "2.2"))
SEND_CAP_PER_MIN = int(os.getenv("SEND_CAP_PER_MIN", "20"))  # hard cap on bot messages/actions per minute
RL_BASE_COOLDOWN = float(os.getenv("RL_BASE_COOLDOWN", "60"))   # first pause after a rate-limit hit (s)
RL_MAX_COOLDOWN = float(os.getenv("RL_MAX_COOLDOWN", "1800"))    # longest pause (s); doubles per strike
SEND_RETRIES = 2                                                 # retries for a rate-limited message
MSG_CHUNK = 900                     # max chars per DM chunk
try:
    MAX_BOTS = max(1, int(os.getenv("MAX_BOTS", "10")))   # most bots !addbot may run at once
except ValueError:
    MAX_BOTS = 10
AUTOREPLY_COOLDOWN = 30             # seconds per keyword per chat

DEFAULT_WELCOME = "👋 Welcome to {group}, {mentions}! Please read the rules and enjoy your stay 🎉"

URL_RE = re.compile(
    r"(?i)(?:\b(?:https?|ftp)://[^\s<>()]+|\bwww\.[^\s<>()]+|"
    r"\b(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}"
    r"(?::\d{2,5})?(?:[/?#][^\s<>()]*)?)"
)

START_TIME = time.time()

# ============================ TERMINAL STYLING ===============================
R = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"


def c(n: int) -> str:
    return f"\033[38;5;{n}m"


PALETTE = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46, 48, 51, 45, 39, 33, 63, 99, 135, 171, 207, 201]

BANNER = [
    r" █████╗ ██████╗ ██████╗ ██╗   ██╗",
    r"██╔══██╗██╔══██╗██╔══██╗██║   ██║",
    r"███████║██████╔╝██████╔╝██║   ██║",
    r"██╔══██║██╔══██╗██╔══██╗██║   ██║",
    r"██║  ██║██████╔╝██████╔╝╚██████╔╝",
    r"╚═╝  ╚═╝╚═════╝ ╚═════╝  ╚═════╝ ",
]


ANSI_RE = re.compile(r"\033\[[0-9;]*m")

CREDITS_ART = [
    r"╔═╗╦═╗╔═╗╔╦╗╦╔╦╗╔═╗",
    r"║  ╠╦╝║╣  ║║║ ║ ╚═╗",
    r"╚═╝╩╚═╚═╝═╩╝╩ ╩ ╚═╝",
]


def rule_width() -> int:
    return max(20, min(58, shutil.get_terminal_size((80, 24)).columns - 6))


def is_mark(ch: str) -> bool:
    """Combining marks (Devanagari matras, virama, emoji selectors) must stay glued to their base letter."""
    return unicodedata.category(ch) in ("Mn", "Mc", "Me", "Cf")


def vis_len(text: str) -> int:
    """Approximate on-screen width (ignores colour codes, combining marks; emoji/CJK count as 2)."""
    n = 0
    for ch in ANSI_RE.sub("", text):
        if is_mark(ch):
            continue
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def rainbow(text: str, offset: int = 0, step: int = 1) -> str:
    out, i = "", 0
    for ch in text:
        if is_mark(ch):          # don't put a colour code between a letter and its mark
            out += ch
            continue
        out += f"{c(PALETTE[(i // step + offset) % len(PALETTE)])}{ch}"
        i += 1
    return out + R


def set_title(title: str) -> None:
    sys.stdout.write(f"\033]0;{title}\007")
    sys.stdout.flush()


def typewriter(text: str, color: int = 250, delay: float = 0.008) -> None:
    for ch in text:
        sys.stdout.write(f"{c(color)}{ch}{R}")
        sys.stdout.flush()
        time.sleep(delay)
    print()


def print_credits() -> None:
    w = rule_width()
    print()
    for i, line in enumerate(CREDITS_ART):
        print("   " + BOLD + rainbow(line, offset=i * 3, step=1))
    print("   " + rainbow("─" * w, offset=4))
    print(f"   {c(213)}{BOLD}Crafted by{R}  {c(250)}➜{R}  {BOLD}{rainbow(BOT_NAME, offset=7)}")
    print(f"   {c(213)}{BOLD}Engine    {R}  {c(250)}➜{R}  {c(250)}instagrapi · psutil · colorama{R}")
    print(f"   {c(213)}{BOLD}Made with {R}  {c(250)}➜{R}  {c(196)}♥{R} {c(250)}for Instagram group chats{R}")
    print("   " + rainbow("─" * w, offset=10))
    print()


def banner(animate: bool = True) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    set_title(f"{BOT_NAME} BOT")
    w = rule_width()
    frame_h = len(BANNER) + 5          # blank + art + blank + rule + title + rule
    frames = 22 if animate else 1
    for f in range(frames):
        if f:
            sys.stdout.write(f"\033[{frame_h}A")
        print()
        for i, line in enumerate(BANNER):
            print("   " + rainbow(line, offset=i + f, step=2))
        print()
        print("   " + rainbow("═" * w, offset=f, step=1))
        print("   " + BOLD + rainbow(f"  {BOT_NAME}   B O T", offset=f * 2, step=1))
        print("   " + rainbow("═" * w, offset=f + 6, step=1))
        sys.stdout.flush()
        if animate:
            time.sleep(0.07)
    print()
    tagline = "   Instagram Group Chat Manager  •  instagrapi  •  psutil  •  made with ♥"
    if animate:
        typewriter(tagline)
    else:
        print(f"{c(250)}{tagline}{R}")
    print_credits()


def boot_steps(steps: list) -> None:
    """Little animated loading bar - purely cosmetic, runs ~1s."""
    width = 24
    for label in steps:
        for n in range(width + 1):
            bar = rainbow("█" * n) + f"{DIM}{'░' * (width - n)}{R}"
            sys.stdout.write(f"\r   {c(213)}◆{R} {label:<26} {bar}")
            sys.stdout.flush()
            time.sleep(0.008)
        sys.stdout.write(f"  {c(46)}✔{R}\n")
    print()


def box(lines: list, color: int = 51) -> None:
    width = max(vis_len(ln) for ln in lines) + 2
    print(f"   {c(color)}╔{'═' * width}╗{R}")
    for ln in lines:
        print(f"   {c(color)}║{R} {ln}{' ' * (width - vis_len(ln) - 1)}{c(color)}║{R}")
    print(f"   {c(color)}╚{'═' * width}╝{R}")


def ask(label: str, default: str = "", secret: bool = False, required: bool = True) -> str:
    shown = ""
    if default and not secret:
        d = default if len(default) <= 45 else default[:42] + "..."
        shown = f" {DIM}[{d}]{R}"
    while True:
        prompt = f"   {c(213)}❯{R} {BOLD}{label}{R}{shown}{c(213)}:{R} "
        val = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if val:
            return val
        if default:
            return default
        if not required:
            return ""
        print(f"   {c(196)}✖ This field is required.{R}")


# ================================ LOGGING ====================================
class ColorFormatter(logging.Formatter):
    LEVELS = {
        logging.DEBUG: (244, "DBUG"),
        logging.INFO: (51, "INFO"),
        logging.WARNING: (220, "WARN"),
        logging.ERROR: (196, "FAIL"),
        logging.CRITICAL: (201, "CRIT"),
    }

    def format(self, record: logging.LogRecord) -> str:
        col, tag = self.LEVELS.get(record.levelno, (255, "LOG "))
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        name = record.name[:11]
        msg = f"{DIM}{stamp}{R} {c(col)}{BOLD}{tag}{R} {DIM}{name:<11}{R} {c(col) if record.levelno >= logging.WARNING else ''}{record.getMessage()}{R}"
        if record.exc_info:
            msg += "\n" + c(244) + self.formatException(record.exc_info) + R
        return msg


def setup_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(ColorFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("bot")


log = logging.getLogger("bot")

# ================================== STATE ====================================
DEFAULT_STATE = {
    "owner_username": "",
    "welcome_enabled": {},      # tid -> bool (default True)
    "welcome_text": DEFAULT_WELCOME,
    "banned": {},               # pk -> username
    "warnings": {},             # tid -> {pk: count}
    "max_warns": 3,
    "mod": {},                  # tid -> {"antispam": bool, "antilink": bool, "autoreply": bool}
    "spam_msgs": 5,
    "spam_secs": 8,
    "link_whitelist": [],          # legacy setting; anti-link now warns for every detected link
    "autoreplies": {},          # keyword -> reply text
    "ignored": {},              # tid -> group name (totally skipped by the bot)
    "sudo": {},                 # pk -> username (extra users allowed to control the bot)
    "user_replies": {},         # tid -> {pk: {"name": str, "text": str}}  (targeted replies)
    "user_reply_cooldown": 20,  # min seconds between targeted replies to the same user
    "user_reacts": {},          # tid -> {pk: {"name": str, "emojis": [str, ...]}}  (auto-reactions)
    "user_react_cooldown": 5,   # min seconds between auto-reactions to the same user
    "name_lock": {},            # tid -> locked group-chat name (bot reverts any change)
    "login_method": "session",  # "session" | "password" - what was used last time
    "login_username": "",       # username used for password login (the password is NEVER saved)
    "session_user": "",         # whose account the saved session.json belongs to
    "owner_pk": "",             # the owner's user id (saves a lookup request on every start)
    "owner_pk_for": "",         # ... and the username that id was looked up for
    "user_cache": {},           # username -> pk for users that had to be looked up through the API
}


def load_state() -> dict:
    st = copy.deepcopy(DEFAULT_STATE)
    if STATE_FILE.exists():
        try:
            st.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"   {c(220)}⚠ Could not read state file: {e}{R}")
    return st


def save_state() -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


state = load_state()
FEATURE_DEFAULTS = {"antispam": False, "antilink": True, "autoreply": True}


def feature_on(tid: str, feature: str) -> bool:
    return state["mod"].get(tid, {}).get(feature, FEATURE_DEFAULTS[feature])


def set_feature(tid: str, feature: str, value: bool) -> None:
    state["mod"].setdefault(tid, {})[feature] = value
    save_state()


RESYNC: set = set()         # chats that were just un-ignored: re-index them silently (no replay of old msgs)


def is_ignored(tid: str) -> bool:
    return tid in state["ignored"]


def ignore_group(tid: str, name: str = "") -> bool:
    if tid in state["ignored"]:
        return False
    state["ignored"][tid] = name or group_names.get(tid) or tid[-6:]
    known_members.pop(tid, None)
    save_state()
    log.warning("🙈 now IGNORING '%s' (…%s)", state["ignored"][tid], tid[-6:])
    return True


def unignore_group(tid: str) -> bool:
    name = state["ignored"].pop(tid, None)
    if name is None:
        return False
    RESYNC.add(tid)
    save_state()
    log.info("👀 no longer ignoring '%s' (…%s)", name, tid[-6:])
    return True


# ================================== LOGIN ====================================
def make_client() -> Client:
    """instagrapi silently retries HTTP 429 three times (after 2s, 4s, 8s). The bot never sees those retries,
    so a throttled account keeps getting hammered. Dropping 429 from that list hands the error straight to the
    bot's rate-limit governor, which then backs off properly."""
    try:
        return Client(session_retry_statuses=[500, 502, 503, 504])
    except TypeError:                      # older instagrapi without that option
        return Client()


cl = make_client()
# A small random pause before every API call keeps bursts (startup scan, several commands in a row) from
# looking robotic. API_DELAY_MAX=0 turns it off.
cl.delay_range = [API_DELAY_MIN, API_DELAY_MAX] if API_DELAY_MAX > 0 else []

# Three ways in, in this order of preference:   sessionid  ->  username + password  ->  saved session.json
CREDS = {"username": "", "password": "", "totp": "", "code": ""}   # filled by prompts / env vars / accounts.json
RELOGIN_TIMES: deque = deque()


def totp_now(seed: str, digits: int = 6, step: int = 30, now: float = None) -> str:
    """RFC 6238 authenticator code from the 2FA secret key (the text key shown when 2FA was set up)."""
    clean = re.sub(r"[\s-]", "", seed).upper()
    key = base64.b32decode(clean + "=" * (-len(clean) % 8))
    t = time.time() if now is None else now
    digest = hmac.new(key, struct.pack(">Q", int(t // step)), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    num = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(num).zfill(digits)


def get_2fa_code(username: str) -> str:
    """A 2FA code from: the TOTP secret, a one-time IG_2FA_CODE, or (interactive mode) the terminal."""
    if CREDS["totp"]:
        if step_left() < 2.5:                       # the code is about to expire - take the next one
            STOP.wait(2.7)
        return totp_now(CREDS["totp"])
    if CREDS["code"]:
        code, CREDS["code"] = CREDS["code"], ""     # a code only works once
        return code
    if HEADLESS:
        return ""
    return ask(f"2FA code for @{username} (authenticator app or SMS)", required=True).replace(" ", "")


def step_left(step: int = 30) -> float:
    return step - (time.time() % step)


def _challenge_code(username, choice) -> str:
    """Instagram sent a one-time code (email / SMS) to confirm this login: ask for it in the terminal."""
    where = getattr(choice, "name", choice)
    return ask(f"Instagram sent a security code ({where}) - enter it", required=True).replace(" ", "")


def apply_proxy() -> None:
    if IG_PROXY:
        try:
            cl.set_proxy(IG_PROXY)
        except Exception as e:
            log.warning("could not use the proxy: %s", e)


def _save_session(user: str) -> None:
    cl.dump_settings(SESSION_FILE)
    try:
        os.chmod(SESSION_FILE, 0o600)               # cookies = a password, keep the file private
    except OSError:
        pass
    state["session_user"] = (user or "").lstrip("@").lower()
    save_state()


def _login_sid(sid: str) -> None:
    """sessionid login (or, with no sessionid given, the saved session.json)."""
    try:
        if SESSION_FILE.exists():
            cl.load_settings(SESSION_FILE)          # keeps the same device fingerprint
        apply_proxy()
        if not sid:
            sid = getattr(cl, "sessionid", "") or ""
        if not sid:
            raise RuntimeError("No sessionid available")
        cl.login_by_sessionid(sid)
    except Exception as e:
        if is_rate_limited(e) or not sid:           # trying again right now would only make it worse
            raise
        log.warning("Login with stored settings failed (%s). Retrying clean...", e)
        cl.set_settings({})
        apply_proxy()
        cl.login_by_sessionid(sid)
    _save_session(getattr(cl, "username", "") or "")
    state["login_method"] = "session"
    save_state()
    log.info("🔐 Logged in via sessionid")


def _login_password(username: str, password: str) -> None:
    """Username + password. The saved session (cookies + device id) is reused whenever it belongs to the same
    account, so a real fresh login - the thing Instagram rate-limits and challenges - happens as rarely as possible."""
    username = username.strip().lstrip("@")
    same_account = SESSION_FILE.exists() and state.get("session_user", "") == username.lower()
    if same_account:
        try:
            cl.load_settings(SESSION_FILE)
            log.info("↻ reusing the saved session for @%s", username)
        except Exception as e:
            log.warning("saved session unreadable (%s) - logging in fresh", e)
            cl.set_settings({})
            same_account = False
    else:
        cl.set_settings({})                         # never inherit another account's cookies / device
    code, override = "", False
    for attempt in range(3):
        apply_proxy()
        try:
            cl.login(username, password, verification_code=code)
            break
        except TwoFactorRequired:
            if code or attempt >= 2:                # we already sent a code and it was refused
                raise
            code = get_2fa_code(username)
            if not code:
                raise
            log.info("🔢 2FA code needed - sending it")
        except Exception as e:
            if "bloks_versioning_id" in str(e) and same_account and not override:
                override = True                     # old saved session: let instagrapi refresh the app profile
                cl.load_settings(SESSION_FILE, override_app_version=True)
                continue
            raise
    else:
        raise RuntimeError("login did not complete")
    _save_session(username)
    state["login_method"] = "password"
    state["login_username"] = username
    save_state()
    log.info("🔐 Logged in as @%s with username/password", username)


def login(session_id: str = "") -> None:
    """Log in with a sessionid, username + password, or the saved session (whichever is available)."""
    if not HEADLESS:
        cl.challenge_code_handler = _challenge_code
    apply_proxy()
    sid = unquote(session_id) if session_id else ""
    if sid:
        _login_sid(sid)
    elif CREDS["username"] and CREDS["password"]:
        _login_password(CREDS["username"], CREDS["password"])
    else:
        _login_sid("")


def relogin(session_id: str) -> bool:
    """The session was rejected mid-run. Log in again - carefully: Instagram punishes repeated fresh logins, so
    there are at most 3 attempts per hour (1 for a sessionid, which can't be renewed), spaced out, and the
    device id is kept. Returns False when the bot should stop."""
    password_mode = bool(CREDS["username"] and CREDS["password"] and not session_id)
    limit = 3 if password_mode else 1
    now = time.time()
    while RELOGIN_TIMES and now - RELOGIN_TIMES[0] > 3600:
        RELOGIN_TIMES.popleft()
    if len(RELOGIN_TIMES) >= limit:
        log.error("Session rejected again and the re-login limit (%d/hour) is used up - stopping to protect "
                  "the account. %s", limit,
                  "Open the account in the Instagram app (it may need a security check)." if password_mode
                  else "Restart with a fresh sessionid.")
        return False
    if RELOGIN_TIMES and now - RELOGIN_TIMES[-1] < 120:
        STOP.wait(120 - (now - RELOGIN_TIMES[-1]))
    RELOGIN_TIMES.append(time.time())
    log.warning("🔁 session expired - logging in again (%d/%d this hour)", len(RELOGIN_TIMES), limit)
    try:
        uuids = (cl.get_settings() or {}).get("uuids")
    except Exception:
        uuids = None
    cl.set_settings({})
    if uuids:                                       # same "phone" as before, not a brand-new device
        try:
            cl.set_uuids(uuids)
        except Exception:
            pass
    try:
        login(session_id)
        return True
    except Exception as e:
        if is_rate_limited(e):
            throttle.hit(e, "re-login")
            return True                             # the cool-down is waited out before the next attempt
        log.error("Re-login failed: %s", e)
        return False


def login_hint(exc: Exception) -> str:
    """A plain-language next step for a failed login."""
    if isinstance(exc, TwoFactorRequired):
        return ("This account has 2FA on and no code was available. Interactive: type the code when asked. "
                "Launcher / accounts.json: add \"totp_seed\" (the 2FA secret key) to the account.")
    if isinstance(exc, (BadPassword, BadCredentials)):
        return "Instagram rejected the username/password - check both (and that the account isn't locked)."
    if isinstance(exc, ChallengeRequired) or "challenge" in str(exc).lower():
        return ("Instagram wants to verify this login. Open the Instagram app (or instagram.com) with this account, "
                "approve 'This was me', wait a few minutes, then run again.")
    if is_rate_limited(exc):
        return "Instagram is rate-limiting logins from here. Wait 15-30 minutes before trying again (don't retry in a loop)."
    if CREDS["username"] and CREDS["password"]:
        return "Check the username/password (and 2FA) - or log in with a sessionid instead."
    return "Check that the sessionid is valid and copied fully."


# ================================= HELPERS ===================================
# ============================ RATE-LIMIT GOVERNOR ============================
RL_WORDS = ("1404006", "item_ack", "wait a few minutes", "please wait", "try again later",
            "rate limit", "throttl", "feedback_required", "too many requests")


def is_rate_limited(exc: Exception) -> bool:
    """True when Instagram is telling us to slow down (not for plain permission errors)."""
    if isinstance(exc, (ClientThrottledError, PleaseWaitFewMinutes, RateLimitError, FeedbackRequired)):
        return True
    if getattr(getattr(exc, "response", None), "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return any(w in msg for w in RL_WORDS)


class Throttle:
    """Central rate-limit brain: spaces out sends, cools down (with escalation) after a hit,
    and slows polling automatically until things are calm again."""

    def __init__(self) -> None:
        self.strikes = 0
        self.total_hits = 0
        self.cooldown_until = 0.0
        self.mult = 1.0               # poll-interval multiplier (1 = normal)
        self.clean = 0                # clean polls since the last hit
        self.last_send = 0.0
        self.sent_times: deque = deque()
        self.idle = 0                 # polls in a row without any new message
        self.started = time.monotonic()

    def remaining(self) -> float:
        return max(0.0, self.cooldown_until - time.monotonic())

    def idle_mult(self) -> float:
        """Quiet chats are polled less often: x1 for the first IDLE_AFTER quiet polls, then slowly up to IDLE_MAX_MULT."""
        if IDLE_MAX_MULT <= 1.0 or self.idle < IDLE_AFTER:
            return 1.0
        return min(IDLE_MAX_MULT, 1.0 + (self.idle - IDLE_AFTER + 1) / IDLE_RAMP)

    def note_poll(self, active: bool) -> None:
        """Tell the governor whether this poll saw any new message; activity brings full speed back at once."""
        self.idle = 0 if active else self.idle + 1

    def poll_delay(self) -> float:
        return random.uniform(POLL_MIN, POLL_MAX) * self.mult * self.idle_mult()

    def hit(self, exc: Exception, where: str) -> None:
        self.strikes += 1
        self.total_hits += 1
        self.clean = 0
        secs = min(RL_MAX_COOLDOWN, RL_BASE_COOLDOWN * 2 ** (self.strikes - 1)) * random.uniform(0.9, 1.2)
        self.cooldown_until = max(self.cooldown_until, time.monotonic() + secs)
        self.mult = min(8.0, self.mult * 2)
        log.warning("🚦 rate-limited during %s (%s) - pausing %s (strike %d). Polling slowed to ~%.0fs.",
                    where, str(exc).replace("\n", " ")[:80], human_time(secs), self.strikes,
                    (POLL_MIN + POLL_MAX) / 2 * self.mult)

    def ok(self) -> None:
        """Call after every clean poll; relaxes the slow-down step by step."""
        self.clean += 1
        if self.clean >= 20 and (self.strikes or self.mult > 1):
            self.clean = 0
            self.strikes = max(0, self.strikes - 1)
            self.mult = max(1.0, self.mult / 2)
            log.info("🟢 calm again - polling back to ~%.0fs", (POLL_MIN + POLL_MAX) / 2 * self.mult)

    def wait_cooldown(self) -> None:
        rem = self.remaining()
        if rem > 0:
            log.warning("⏳ cooling down for %s before the next request...", human_time(rem))
            STOP.wait(rem)
            if not STOP.is_set():
                log.info("▶️  cool-down over, resuming")

    def wait_turn(self) -> None:
        """Block until one more send/action is allowed (cool-down over, spacing, per-minute cap)."""
        self.wait_cooldown()
        while True:
            now = time.monotonic()
            while self.sent_times and now - self.sent_times[0] > 60:
                self.sent_times.popleft()
            if len(self.sent_times) < SEND_CAP_PER_MIN or STOP.is_set():
                break
            pause = 60 - (now - self.sent_times[0]) + 0.1
            log.warning("🐢 send cap reached (%d/min) - waiting %.0fs", SEND_CAP_PER_MIN, pause)
            STOP.wait(pause)
        wait = self.last_send + random.uniform(SEND_GAP_MIN, SEND_GAP_MAX) - time.monotonic()
        if wait > 0:
            STOP.wait(wait)

    def sent(self) -> None:
        now = time.monotonic()
        self.last_send = now
        self.sent_times.append(now)

    def has_room(self, reserve: int = 5) -> bool:
        """True when it's calm enough for optional extras (reactions): no cool-down and the
        per-minute budget still has `reserve` slots left for real messages."""
        if self.remaining() > 0:
            return False
        now = time.monotonic()
        recent = sum(1 for t in self.sent_times if now - t <= 60)
        return recent < SEND_CAP_PER_MIN - reserve

    def status(self) -> list:
        now = time.monotonic()
        last_min = sum(1 for t in self.sent_times if now - t <= 60)
        rem = self.remaining()
        total = getattr(cl, "private_requests_count", None)
        mins = max((now - self.started) / 60, 1 / 60)
        eff = (POLL_MIN + POLL_MAX) / 2 * self.mult * self.idle_mult()
        lines = [
            f"Cool-down      : {human_time(rem) + ' left' if rem > 0 else 'none'}",
            f"Strikes        : {self.strikes}  (total rate-limit hits: {self.total_hits})",
            f"Poll interval  : ~{eff:.1f}s  (base {POLL_MIN:g}-{POLL_MAX:g}s, x{self.mult:g} after rate limits, "
            f"x{self.idle_mult():.1f} while quiet)",
            f"Sent last 60s  : {last_min}/{SEND_CAP_PER_MIN}",
        ]
        if isinstance(total, int):
            lines.append(f"API requests   : {total} since start (~{total / mins:.1f}/min)")
        return lines

throttle = Throttle()


def paced(func, *args, rate_limit_retry: bool = False, **kwargs):
    """Run an API call with spacing; messages that get rate-limited are retried after a cool-down."""
    for attempt in range(SEND_RETRIES + 1):
        throttle.wait_turn()
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if rate_limit_retry and is_rate_limited(e):
                throttle.hit(e, "send")
                if attempt < SEND_RETRIES:
                    continue
            raise
        finally:
            throttle.sent()


def say(thread_id: str, text: str, reply_to=None) -> None:
    """Send a message. With reply_to=<DirectMessage> it is sent as a quoted reply when the
    installed instagrapi supports it (otherwise / on failure it is a normal message)."""
    extra = {}
    if reply_to is not None:
        try:
            if "reply_to_message" in inspect.signature(cl.direct_send).parameters:
                extra["reply_to_message"] = reply_to
        except (TypeError, ValueError):
            pass
    try:
        try:
            paced(cl.direct_send, text, thread_ids=[int(thread_id)], rate_limit_retry=True, **extra)
        except Exception as e:
            if not extra or is_rate_limited(e):
                raise
            log.warning("quoted reply failed (%s) - sending as a normal message", e)
            paced(cl.direct_send, text, thread_ids=[int(thread_id)], rate_limit_retry=True)
        log.info("➡️  [%s] %s", thread_id[-6:], text.replace("\n", " ⏎ ")[:110])
    except Exception as e:
        log.error("send failed: %s", e)


def say_long(thread_id: str, text: str) -> None:
    """Split long text on line boundaries so DMs don't get rejected."""
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > MSG_CHUNK and chunk:
            say(thread_id, chunk)
            chunk = ""
        chunk += ("\n" if chunk else "") + line
    if chunk:
        say(thread_id, chunk)


USER_CACHE: dict = {}       # username -> pk (filled from every chat's member list = no API call needed)
USER_CACHE.update(state.get("user_cache") or {})   # plus everything that was ever looked up through the API
LOOKUP_LIMITED_AT = 0.0     # when a user lookup was last rate-limited


def user_not_found() -> str:
    if time.time() - LOOKUP_LIMITED_AT < 30:
        return "⏳ Instagram is rate-limiting user lookups right now - try again in a minute."
    return "❌ User not found."


def resolve_user(arg: str):
    """@name -> (pk, name). Looked up in memory first, then in the saved cache, and only then through the API
    (paced, and a rate limit is reported to the governor instead of being swallowed)."""
    global LOOKUP_LIMITED_AT
    name = arg.lstrip("@").strip().lower()
    if not name:
        return None, None
    pk = USER_CACHE.get(name)
    if pk:
        return pk, name
    try:
        pk = str(paced(cl.user_id_from_username, name))
    except Exception as e:
        if is_rate_limited(e):
            LOOKUP_LIMITED_AT = time.time()
            throttle.hit(e, "user lookup")
        return None, None
    USER_CACHE[name] = pk
    cache = state["user_cache"]
    cache[name] = pk
    while len(cache) > 500:                          # keep the file small
        cache.pop(next(iter(cache)))
    save_state()
    return pk, name


def split_target(arg: str):
    parts = arg.split(maxsplit=1)
    if not parts:
        return None, None, ""
    pk, name = resolve_user(parts[0])
    return pk, name, (parts[1] if len(parts) > 1 else "")


def kick_user(thread_id: str, user_pk: str) -> bool:
    try:
        paced(
            cl.private_request,
            f"direct_v2/threads/{thread_id}/remove_users/",
            data={"user_ids": json.dumps([str(user_pk)]), "_uuid": cl.uuid},
            with_signature=False,   # same request format instagrapi uses for add_user
        )
        log.warning("👢 kicked pk=%s from %s", user_pk, thread_id[-6:])
        return True
    except Exception as e:
        log.error("kick failed: %s", e)
        return False


def add_user(thread_id: str, user_pk: str) -> bool:
    try:
        paced(
            cl.private_request,
            f"direct_v2/threads/{thread_id}/add_user/",
            data={"user_ids": json.dumps([str(user_pk)]), "_uuid": cl.uuid},
            with_signature=False,   # same request format instagrapi uses for add_user
        )
        log.info("➕ added pk=%s to %s", user_pk, thread_id[-6:])
        return True
    except Exception as e:
        log.error("add failed: %s", e)
        return False


def admin_pks(thread) -> set:
    """User ids of the current admins of a group chat."""
    return {str(a) for a in (getattr(thread, "admin_user_ids", None) or [])}


def set_admin(thread_id: str, user_pk: str, make_admin: bool) -> bool:
    """Promote / demote a member as group admin (private API; the bot must be an admin)."""
    action = "add_admins" if make_admin else "remove_admins"
    try:
        paced(
            cl.private_request,
            f"direct_v2/threads/{thread_id}/{action}/",
            data={"user_ids": json.dumps([str(user_pk)]), "_uuid": cl.uuid},
            with_signature=False,   # same request format instagrapi uses for add_user
        )
        log.warning("🛡️  %s pk=%s in %s", "promoted" if make_admin else "demoted", user_pk, thread_id[-6:])
        return True
    except Exception as e:
        log.error("%s failed: %s", action, e)
        return False


def demote_many(thread_id: str, pks: list) -> tuple:
    """Remove admin from several users with ONE request. If Instagram refuses the batch
    (for a reason other than rate limiting) fall back to one request per user.
    Returns (done_pks, failed_pks)."""
    pks = [str(pk) for pk in pks]
    try:
        paced(
            cl.private_request,
            f"direct_v2/threads/{thread_id}/remove_admins/",
            data={"user_ids": json.dumps(pks), "_uuid": cl.uuid},
            with_signature=False,
        )
        log.warning("🛡️  demoted %d user(s) in a single request in %s", len(pks), thread_id[-6:])
        return pks, []
    except Exception as e:
        if is_rate_limited(e):
            throttle.hit(e, "bulk demote")
            return [], pks
        log.warning("bulk demote failed (%s) - falling back to one by one", e)
    done, failed = [], []
    for pk in pks:
        (done if set_admin(thread_id, pk, False) else failed).append(pk)
    return done, failed


def is_sudo(pk) -> bool:
    return str(pk) in state["sudo"]


def blocked_target(tid: str, is_owner: bool, pk: str, name: str, verb: str) -> bool:
    """True (and tells the chat why) when this account must not be targeted by `verb`."""
    if pk == MY_PK:
        say(tid, f"😅 I won't {verb} myself.")
        return True
    if pk == OWNER_PK:
        say(tid, f"😅 I won't {verb} my owner.")
        return True
    if not is_owner and is_sudo(pk):
        say(tid, f"🔒 Only the owner can {verb} another sudo user.")
        return True
    return False


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def human_time(seconds: float) -> str:
    return str(dt.timedelta(seconds=int(seconds)))


def system_stats() -> str:
    cpu = psutil.cpu_percent(interval=0.1)
    freq = psutil.cpu_freq()
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    disk = psutil.disk_usage(os.path.abspath(os.sep))
    net = psutil.net_io_counters()
    proc = psutil.Process(os.getpid())
    boot = dt.datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M")

    lines = [
        "🖥️ SYSTEM RESOURCES",
        f"OS: {platform.system()} {platform.release()}",
        f"CPU: {cpu}% | {psutil.cpu_count(logical=False)}C/{psutil.cpu_count()}T"
        + (f" @ {freq.current:.0f}MHz" if freq else ""),
        f"RAM: {vm.percent}% ({human_bytes(vm.used)}/{human_bytes(vm.total)})",
        f"Swap: {sw.percent}% ({human_bytes(sw.used)}/{human_bytes(sw.total)})",
        f"Disk: {disk.percent}% ({human_bytes(disk.used)}/{human_bytes(disk.total)})",
        f"Net: ↑{human_bytes(net.bytes_sent)} ↓{human_bytes(net.bytes_recv)}",
    ]
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            first = next(iter(temps.values()))
            if first:
                lines.append(f"Temp: {first[0].current:.0f}°C")
    except Exception:
        pass
    try:
        batt = psutil.sensors_battery()
        if batt:
            lines.append(f"Battery: {batt.percent:.0f}% {'🔌' if batt.power_plugged else '🔋'}")
    except Exception:
        pass
    lines += [
        f"Bot RAM: {human_bytes(proc.memory_info().rss)}",
        f"Bot uptime: {human_time(time.time() - START_TIME)}",
        f"System booted: {boot}",
    ]
    return "\n".join(lines)


def name_for(thread, pk: str) -> str:
    for u in thread.users:
        if str(u.pk) == pk:
            return u.username
    return pk


def group_name(thread) -> str:
    if getattr(thread, "thread_title", None):
        return thread.thread_title
    names = [u.username for u in thread.users[:3]]
    return ", ".join(names) or str(thread.id)[-6:]


# ============================== GC NAME LOCK ================================
# !setname <name> / !lockname keep a group chat's name fixed. On every poll the bot compares the
# chat's current title with the locked one and renames the chat back if anyone changed it.
NAME_GRACE = 10.0           # s to trust our own rename (the inbox can lag a few seconds behind)
NAME_NOTICE_GAP = 60.0      # min s between "name is locked" notices in one chat (no spam)
NAME_RETRY_BASE = 30.0      # wait after a FAILED revert; doubles on every further failure
NAME_RETRY_MAX = 600.0
NAME_STUCK_AFTER = 3        # pause after this many reverts from the very same title in a row
NAME_LAST_CHANGE: dict = {}     # tid -> time the bot itself last renamed the chat
NAME_LAST_NOTICE: dict = {}     # tid -> time of the last "locked" notice
NAME_FAILS: dict = {}           # tid -> consecutive failed reverts
NAME_NEXT_TRY: dict = {}        # tid -> earliest time of the next revert attempt
NAME_STUCK: dict = {}           # tid -> (title seen, reverts from it in a row)


def _norm_name(text) -> str:
    return unicodedata.normalize("NFC", (text or "")).strip()


def current_title(thread) -> str:
    return _norm_name(getattr(thread, "thread_title", None))


def set_group_name(thread_id: str, title: str):
    """Rename a group chat (the bot must be allowed to edit it). Returns (ok, title Instagram
    reports back or None)."""
    try:
        res = paced(
            cl.private_request,
            f"direct_v2/threads/{thread_id}/update_title/",
            data={"_uuid": cl.uuid, "title": title},
            with_signature=False,   # same request format instagrapi uses for direct_thread_update_title
            rate_limit_retry=True,
        )
    except Exception as e:
        log.error("rename failed: %s", e)
        return False, None
    NAME_LAST_CHANGE[thread_id] = time.time()
    shown = None
    if isinstance(res, dict):
        t = res.get("thread")
        if isinstance(t, dict):
            shown = _norm_name(t.get("thread_title")) or None
    log.warning("🏷️  GC name set to '%s' in …%s", title, thread_id[-6:])
    return (isinstance(res, dict) and res.get("status") == "ok"), shown


def lock_name(tid: str, name: str) -> None:
    state["name_lock"][tid] = _norm_name(name)
    for d in (NAME_FAILS, NAME_NEXT_TRY, NAME_STUCK):
        d.pop(tid, None)
    save_state()
    log.warning("🔒 GC name locked to '%s' in …%s", name, tid[-6:])


def unlock_name(tid: str) -> bool:
    for d in (NAME_FAILS, NAME_NEXT_TRY, NAME_STUCK):
        d.pop(tid, None)
    if state["name_lock"].pop(tid, None) is None:
        return False
    save_state()
    log.warning("🔓 GC name unlocked in …%s", tid[-6:])
    return True


def name_lock_text(tid: str) -> str:
    locked = state["name_lock"].get(tid)
    return f'🔒 "{locked}"' if locked else "off"


def enforce_name_lock(thread) -> None:
    """Revert the chat's name if it is locked and someone changed it."""
    tid = str(thread.id)
    locked = state["name_lock"].get(tid)
    if not locked:
        return
    seen = current_title(thread)
    if seen == locked:                                   # name is fine
        for d in (NAME_FAILS, NAME_NEXT_TRY, NAME_STUCK):
            d.pop(tid, None)
        return
    now = time.time()
    if now - NAME_LAST_CHANGE.get(tid, 0) < NAME_GRACE or now < NAME_NEXT_TRY.get(tid, 0):
        return                                           # our own rename may not show up yet / backing off

    # Safety net: if we keep reverting from the SAME title and never see the locked name, Instagram is
    # probably rewriting it (too long / odd characters). Stop hammering the API and say so.
    last, times = NAME_STUCK.get(tid, (None, 0))
    times = times + 1 if seen == last else 1
    NAME_STUCK[tid] = (seen, times)
    if times > NAME_STUCK_AFTER:
        NAME_STUCK.pop(tid, None)
        NAME_NEXT_TRY[tid] = now + NAME_RETRY_MAX
        log.warning("🔒 name revert stuck in …%s (Instagram keeps showing '%s') - pausing", tid[-6:], seen)
        say(tid, f"⚠️ Instagram keeps showing \"{seen}\" instead of the locked name, so I'm pausing the revert "
                 f"for {int(NAME_RETRY_MAX // 60)} min. The name may be too long or contain characters Instagram "
                 f"rewrites - try another one with {PREFIX}setname.")
        return

    log.warning("🔒 name of …%s was changed to '%s' - reverting to '%s'", tid[-6:], seen, locked)
    ok, _ = set_group_name(tid, locked)
    if ok:
        NAME_FAILS.pop(tid, None)
        NAME_NEXT_TRY.pop(tid, None)
        if time.time() - NAME_LAST_NOTICE.get(tid, 0) >= NAME_NOTICE_GAP:
            NAME_LAST_NOTICE[tid] = time.time()
            say(tid, f"🔒 The GC name is locked - changed it back to \"{locked}\".")
    else:
        n = NAME_FAILS[tid] = NAME_FAILS.get(tid, 0) + 1
        NAME_NEXT_TRY[tid] = now + min(NAME_RETRY_MAX, NAME_RETRY_BASE * 2 ** (n - 1))
        if n == 1:
            say(tid, "⚠️ The GC name is locked but I couldn't change it back (am I a group admin?). "
                     "I'll keep retrying quietly.")


# ================================ WARNINGS ===================================
def add_warning(thread, pk: str, uname: str, reason: str, *, kick_allowed: bool = True) -> None:
    tid = str(thread.id)
    w = state["warnings"].setdefault(tid, {})
    mx = state["max_warns"]
    # Protected accounts can still be warned for links, but are never auto-removed.
    w[pk] = min(w.get(pk, 0) + 1, mx) if not kick_allowed else w.get(pk, 0) + 1
    count = w[pk]
    log.warning("⚠️  warn @%s %d/%d in '%s' — %s", uname, count, mx, group_name(thread), reason)

    if count >= mx and kick_allowed:
        w.pop(pk, None)
        save_state()
        if kick_user(tid, pk):
            say(tid, f"🚫 @{uname} reached {mx}/{mx} warnings ({reason}) and was removed.")
        else:
            say(tid, f"🚫 @{uname} reached {mx}/{mx} warnings ({reason}) but I couldn't remove them (am I admin?).")
    else:
        save_state()
        suffix = " (admin/owner: no automatic removal)" if count >= mx and not kick_allowed else ""
        say(tid, f"⚠️ @{uname} warning {count}/{mx}: {reason}{suffix}")


# =============================== MODERATION ==================================
spam_log: dict = {}


def message_contains_link(message, text: str) -> bool:
    """Detect typed URLs and Instagram link attachments/metadata."""
    extra = ""
    link_obj = getattr(message, "link", None)
    if isinstance(link_obj, dict):
        extra = str(link_obj.get("text") or link_obj.get("url") or "")
    elif link_obj is not None:
        extra = str(getattr(link_obj, "text", "") or getattr(link_obj, "url", "") or "")
    combined = f"{text} {extra}".strip()
    item_type = str(getattr(message, "item_type", "") or "").lower()
    return bool(URL_RE.search(combined)) or item_type == "link"


def moderate(thread, m, exempt: set) -> bool:
    """Returns True if the message violated a rule (a warning was issued)."""
    tid, pk = str(thread.id), str(m.user_id)
    uname = name_for(thread, pk)
    text = (m.text or "").strip()

    # Anti-link applies to every human sender, including admins and the owner.
    # Protected accounts receive warnings but are not auto-kicked at the limit.
    if feature_on(tid, "antilink") and message_contains_link(m, text):
        add_warning(thread, pk, uname, "links are not allowed", kick_allowed=pk not in exempt)
        return True

    # Other moderation rules continue to exempt the owner and group admins.
    if pk in exempt:
        return False

    if feature_on(tid, "antispam"):
        try:
            ts = m.timestamp.timestamp()
        except Exception:
            ts = time.time()
        dq = spam_log.setdefault((tid, pk), deque())
        dq.append((ts, text.lower()))
        while dq and ts - dq[0][0] > max(state["spam_secs"], 30):
            dq.popleft()

        recent = [t for t, _ in dq if ts - t <= state["spam_secs"]]
        dupes = sum(1 for _, tx in dq if tx and tx == text.lower())
        if len(recent) >= state["spam_msgs"]:
            dq.clear()
            add_warning(thread, pk, uname, "flooding the chat")
            return True
        if text and dupes >= 3:
            dq.clear()
            add_warning(thread, pk, uname, "repeating the same message")
            return True
    return False


# ============================== AUTO REPLIES =================================
reply_cooldown: dict = {}


# ========================= TARGETED AUTO-REACTIONS ===========================
user_react_last: dict = {}   # (tid, pk) -> time of the last auto-reaction
_react_warned: set = set()


def api_supports(func, param: str) -> bool:
    try:
        return param in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def parse_emojis(raw: str) -> list:
    """'😂 🔥, ❤️' -> ['😂', '🔥', '❤️'].  Returns [] if anything in there isn't an emoji."""
    out = []
    for tok in re.split(r"[\s,]+", raw.strip()):
        if not tok:
            continue
        if len(tok) > 12 or not any(ord(ch) > 127 for ch in tok) or any(ch.isascii() and ch.isalnum() for ch in tok):
            return []
        if tok not in out:
            out.append(tok)
    return out[:6]


def maybe_user_react(thread, m, sender: str, sname: str) -> bool:
    """React to a message from a targeted user with one of the emoji the owner set.
    Best-effort: skipped (never queued) when the bot is cooling down or close to its send cap,
    so reactions can't crowd out real replies."""
    tid = str(thread.id)
    rule = state["user_reacts"].get(tid, {}).get(sender)
    if not rule or m.item_type == "action_log":
        return False
    if not hasattr(cl, "direct_send_reaction"):
        if "lib" not in _react_warned:
            _react_warned.add("lib")
            log.warning("Auto-react needs a newer instagrapi: pip install -U instagrapi")
        return False
    now = time.time()
    if now - user_react_last.get((tid, sender), 0) < state["user_react_cooldown"]:
        return False
    if not throttle.has_room():
        log.debug("skipping reaction to @%s (rate budget is tight)", sname)
        return False
    user_react_last[(tid, sender)] = now
    emoji = random.choice(rule["emojis"])
    kwargs = {}
    if getattr(m, "client_context", None) and api_supports(cl.direct_send_reaction, "client_context"):
        kwargs["client_context"] = m.client_context
    try:
        paced(cl.direct_send_reaction, int(tid), m.id, emoji, **kwargs)
        log.info("%s reacted to @%s", emoji, sname)
        return True
    except Exception as e:
        if is_rate_limited(e):
            throttle.hit(e, "reaction")
        else:
            log.warning("reaction failed: %s", e)
        return False


# ========================== TARGETED USER REPLIES ============================
user_reply_last: dict = {}   # (tid, pk) -> time of the last targeted reply


def maybe_user_reply(thread, m, sender: str, sname: str) -> bool:
    """If the owner set a reply for this user in this GC, answer their message with it.
    A cool-down (default 20s, `!replycooldown`) keeps it from flooding the chat."""
    tid = str(thread.id)
    rule = state["user_replies"].get(tid, {}).get(sender)
    if not rule or m.item_type == "action_log":
        return False
    now = time.time()
    if now - user_reply_last.get((tid, sender), 0) < state["user_reply_cooldown"]:
        return False
    user_reply_last[(tid, sender)] = now
    log.info("🎯 replying to @%s", sname)
    say(tid, rule["text"].replace("{user}", f"@{sname}"), reply_to=m)
    return True


def maybe_autoreply(tid: str, text: str) -> None:
    if not text or not feature_on(tid, "autoreply") or text.startswith(PREFIX):
        return
    low = text.lower()
    now = time.time()
    for kw, reply in state["autoreplies"].items():
        if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", low):
            if now - reply_cooldown.get((tid, kw), 0) < AUTOREPLY_COOLDOWN:
                continue
            reply_cooldown[(tid, kw)] = now
            log.info("💬 auto-reply triggered by '%s'", kw)
            say(tid, reply)
            return


# ================================== HELP =====================================
def build_help(tid: str = "") -> str:
    p = PREFIX
    on = lambda f: "ON ✅" if feature_on(tid, f) else "OFF ❌"
    return f"""━━━━━━━━━━━━━━━━━━━━
{BOT_NAME}  B O T
━━━━━━━━━━━━━━━━━━━━
🤖 GENERAL
{p}help – this menu
{p}ping – is the bot alive?
{p}stats – CPU / RAM / disk / network
{p}uptime – bot uptime
{p}status – settings of this GC
{p}members – list members
{p}say <text> – bot repeats text
{p}announce <text> – announcement
{p}leave – bot leaves this GC
{p}groups – numbered list of my GCs (🙈 = ignored)
{p}ignore – totally ignore this GC
{p}ignore 2,5 | 2-5 | all | name – ignore GCs from the {p}groups list
{p}unignore – resume this GC (works while ignored)
{p}unignore 2,5 | all | name – resume GCs from the list
{p}ignored – list ignored GCs

👢 MEMBERS
{p}kick @user – remove a member
{p}add @user – add a user
{p}ban @user – kick + block re-adding
{p}unban @user
{p}banlist – show banned users

👋 WELCOME
{p}welcome on|off – toggle welcome
{p}setwelcome <text> – use {{mentions}} {{group}}

⚠️ WARNINGS
{p}warn @user [reason]
{p}unwarn @user – remove 1 warning
{p}clearwarns @user
{p}warns [@user] – show warnings
{p}maxwarns <n> – kick at n warnings

🛡️ PROTECTION
{p}antispam on|off
{p}antispam set <msgs> <secs>
{p}antilink on|off

👑 ADMIN CONTROL
{p}admin @user – make someone GC admin
{p}unadmin @user – remove their GC admin
{p}admins – list GC admins
{p}unadminall – 👑 remove admin from everyone at once except you (I keep mine)
{p}unadminall bot – 👑 same, and I demote myself too
{p}onlyme – 👑 same as {p}unadminall

🏷️ GC NAME LOCK
{p}setname <name> – rename this GC and lock the name
{p}lockname – lock the current name
{p}unlockname – unlock it (name can be changed again)
(while locked, anyone who changes the name gets reverted)

🔑 BOT ACCESS (👑 = owner only)
{p}addsudo @user – 👑 let them control the bot
{p}delsudo @user – 👑 revoke their access
{p}sudolist – 👑 list sudo users

🤖 MULTI-BOT (👑 owner only)
{p}addbot <sessionid> [name] [prefix] – 👑 start another bot account (send it in a private DM, then unsend!)
{p}addbot login <username> <password> [name] [prefix] – 👑 same, with username + password (no 2FA)
{p}bots – 👑 list running bots
{p}stopbot <name> – 👑 stop one bot

💬 AUTO-REPLY
{p}autoreply on|off
{p}addreply <keyword> | <reply>
{p}delreply <keyword>
{p}replies – list all

🎯 REPLY TO ONE USER
{p}replyto @user | text – keep replying to them ({{user}} = their @name)
{p}stopreply @user|all – stop replying
{p}userreplies – list for this GC
{p}replycooldown <secs> – min gap per user (5-3600, now {state['user_reply_cooldown']}s)

😀 AUTO-REACT TO ONE USER
{p}reactto @user | 😂 🔥 – react to all their messages (space-separated, random pick)
{p}stopreact @user|all – stop reacting
{p}reacts – list for this GC
{p}reactcooldown <secs> – min gap per user (0-600, now {state['user_react_cooldown']}s)

📜 GROUP RULES
1. Be respectful to everyone.
2. No spam: max {state['spam_msgs']} msgs in {state['spam_secs']}s, no repeating the same message 3 times. (Anti-spam: {on('antispam')})
3. Any detected link is warned. (Anti-link: {on('antilink')})
4. {state['max_warns']} warnings = automatic removal for regular members.
5. Banned users are removed instantly if re-added.
6. Only the owner and approved sudo users can command the bot; admins/owner/sudo still receive link warnings but are not auto-kicked.
━━━━━━━━━━━━━━━━━━━━"""


# ============================ MULTI-BOT (CHAT COMMANDS) ======================
SECRET_CMDS = ("addbot",)          # commands whose argument is a secret - never printed in the logs
HUB = None                         # local Hub when THIS process is the one you started (single mode)
HUB_REPLIES: deque = deque()       # (thread_id, text) answers waiting to be sent from the main loop
_HUB_LOCK = threading.Lock()
DM_COMMANDS = {"addbot", "bots", "stopbot"}   # the only commands read from private 1:1 DMs


def mask_secret(text: str) -> str:
    """Hide the sessionid / password of '!addbot ...' before a message is written to the logs."""
    t = (text or "").strip()
    if t.lower().startswith(tuple((PREFIX + sc).lower() for sc in SECRET_CMDS)):
        parts = t.split()
        if len(parts) >= 2 and parts[1].lower() in ("login", "user", "pw"):
            return " ".join(parts[:3] + (["••••••••"] if len(parts) > 3 else []) + parts[4:])
        return " ".join([parts[0], "••••••••"] + parts[2:])
    return text


def get_local_hub():
    """Single mode: this process starts the extra bots itself (created on first use)."""
    global HUB
    with _HUB_LOCK:
        if HUB is None:
            HUB = Hub(Path.cwd())
            HUB.start()
        return HUB


def hub_call(req: dict, timeout: float = 30.0) -> dict:
    """Ask whoever hosts the bots (the launcher, or this very process) to add / list / stop a bot."""
    hub_dir = os.getenv("BOT_HUB", "")
    if not hub_dir:
        if HEADLESS:
            return {"ok": False, "msg": "❌ No launcher is running, so I can't start other bots."}
        return get_local_hub().handle(req)
    root = Path(hub_dir)
    rid = f"{int(time.time() * 1000)}-{random.randrange(10 ** 6):06d}"
    tmp = root / "requests" / f"{rid}.tmp"
    tmp.write_text(json.dumps(dict(req, id=rid), ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)             # it may contain a sessionid
    except OSError:
        pass
    tmp.replace(tmp.with_suffix(".json"))
    res = root / "responses" / f"{rid}.json"
    end = time.time() + timeout
    while time.time() < end and not STOP.is_set():
        if res.exists():
            try:
                data = json.loads(res.read_text(encoding="utf-8"))
                res.unlink()
                return data
            except Exception:
                pass
        time.sleep(0.3)
    return {"ok": False, "msg": "⚠️ The launcher didn't answer in time. Check its terminal."}


def _hub_worker(tid: str, req: dict) -> None:
    try:
        res = hub_call(req)
    except Exception as e:
        res = {"ok": False, "msg": f"❌ hub error: {e}"}
    if not res.get("quiet"):
        HUB_REPLIES.append((tid, str(res.get("msg") or "…")))


def flush_hub_replies() -> None:
    """Answers are queued by background threads and sent from the main loop (keeps the rate limiter single-threaded)."""
    while HUB_REPLIES:
        tid, text = HUB_REPLIES.popleft()
        say_long(tid, text)


def hub_command(tid: str, cmd: str, arg: str) -> None:
    p = PREFIX
    parts = arg.split()
    if cmd == "addbot":
        if not parts:
            return say(tid, f"Usage: {p}addbot <sessionid> [name] [prefix]\n"
                            f"   or: {p}addbot login <username> <password> [name] [prefix]\n"
                            f"Tip: send it in a private DM with me, then unsend the message.")
        if parts[0].lower() in ("login", "user", "pw"):
            if len(parts) < 3:
                return say(tid, f"Usage: {p}addbot login <username> <password> [name] [prefix]")
            new_prefix = parts[4] if len(parts) > 4 else ""
            if len(new_prefix) > 3:
                return say(tid, "❌ The prefix can be at most 3 characters (e.g. ! . $ ?).")
            req = {"kind": "add", "username": parts[1].lstrip("@"), "password": parts[2],
                   "name": parts[3] if len(parts) > 3 else "", "owner": OWNER_USERNAME,
                   "prefix": new_prefix or PREFIX}
        else:
            sid = unquote(parts[0].strip("\"'"))
            if sid.lower().startswith("sessionid="):
                sid = sid[len("sessionid="):]
            if len(sid) < 20 or sid.startswith("@"):
                return say(tid, "❌ That doesn't look like a full sessionid (copy the whole cookie value).")
            new_prefix = parts[2] if len(parts) > 2 else ""
            if len(new_prefix) > 3:
                return say(tid, "❌ The prefix can be at most 3 characters (e.g. ! . $ ?).")
            req = {"kind": "add", "sid": sid, "name": parts[1] if len(parts) > 1 else "",
                   "owner": OWNER_USERNAME, "prefix": new_prefix or PREFIX}
    elif cmd == "bots":
        req = {"kind": "list"}
    else:  # stopbot
        if not parts:
            return say(tid, f"Usage: {p}stopbot <n>   (see {p}bots)")
        if parts[0].lower() == os.getenv("BOT_TAG", "").lower() and parts[0]:
            say(tid, "👋 Stopping myself...")
        req = {"kind": "stop", "name": parts[0]}
    threading.Thread(target=_hub_worker, args=(tid, req), daemon=True).start()


def msg_is_new(m) -> bool:
    """True if a message was sent after this process started (so old DMs are never replayed)."""
    ts = getattr(m, "timestamp", None)
    if ts is None:
        return True
    try:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        return ts.timestamp() >= START_TIME - 5
    except Exception:
        return True


def watch_dm(thread, owner_pk: str) -> int:
    """Private 1:1 chats: ONLY the owner's !addbot / !bots / !stopbot are read - nothing else.
    Returns how many new messages the chat had (so the poller can tell quiet from busy)."""
    tid = str(thread.id)
    fresh = 0
    for m in reversed(thread.messages):
        mid = str(m.id)
        if mid in seen_msgs:
            continue
        seen_msgs.add(mid)
        fresh += 1
        if str(m.user_id) != owner_pk or not msg_is_new(m):
            continue
        text = (m.text or "").strip()
        if not text.startswith(PREFIX):
            continue
        body = text[len(PREFIX):].strip().split(maxsplit=1)
        if not body or body[0].lower() not in DM_COMMANDS:
            continue
        log.info("⚡ owner DM command: %s", mask_secret(text)[:80])
        try:
            handle_command(thread, text, is_owner=True)
        except Exception as e:
            log.error("DM command failed: %s", e)
    return fresh


# ================================ COMMANDS ===================================
# Commands that only the real owner may use (sudo users can use everything else)
OWNER_ONLY = {"addsudo", "delsudo", "sudolist", "onlyme", "unadminall", "removealladmins", "leave", "ignore",
              "addbot", "bots", "stopbot", "unignore"}


def handle_command(thread, text: str, is_owner: bool = True) -> None:
    tid = str(thread.id)
    parts = text[len(PREFIX):].strip().split(maxsplit=1)
    if not parts:
        return
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in OWNER_ONLY and not is_owner:
        return say(tid, "👑 That command is owner-only.")

    if cmd in ("help", "commands", "rules", "menu"):
        say_long(tid, build_help(tid))

    elif cmd == "ping":
        say(tid, "🏓 Pong! Bot is online.")

    elif cmd == "stats":
        say_long(tid, system_stats())

    elif cmd == "uptime":
        say(tid, f"⏱️ Uptime: {human_time(time.time() - START_TIME)}")

    elif cmd == "status":
        say(tid, (
            f"⚙️ SETTINGS (this GC)\n"
            f"Welcome: {'on' if state['welcome_enabled'].get(tid, True) else 'off'}\n"
            f"Anti-spam: {'on' if feature_on(tid, 'antispam') else 'off'} "
            f"({state['spam_msgs']} msgs/{state['spam_secs']}s)\n"
            f"Anti-link: {'on' if feature_on(tid, 'antilink') else 'off'} "
            f"(warns on all detected links)\n"
            f"Auto-reply: {'on' if feature_on(tid, 'autoreply') else 'off'} "
            f"({len(state['autoreplies'])} keywords)\n"
            f"Name lock: {name_lock_text(tid)}\n"
            f"Max warnings: {state['max_warns']}\n"
            f"Banned users: {len(state['banned'])}"
        ))

    elif cmd == "members":
        names = [f"@{u.username}" for u in thread.users]
        say_long(tid, f"👥 Members ({len(names)}):\n" + "\n".join(names) if names else "No members found.")

    elif cmd == "kick":
        if not arg:
            return say(tid, f"Usage: {PREFIX}kick @username")
        pk, name = resolve_user(arg.split()[0])
        if not pk:
            return say(tid, user_not_found())
        if blocked_target(tid, is_owner, pk, name, "kick"):
            return
        ok = kick_user(tid, pk)
        say(tid, f"👢 Kicked @{name}" if ok else f"❌ Couldn't kick @{name} (am I a group admin?)")

    elif cmd == "add":
        if not arg:
            return say(tid, f"Usage: {PREFIX}add @username")
        pk, name = resolve_user(arg.split()[0])
        if not pk:
            return say(tid, user_not_found())
        ok = add_user(tid, pk)
        say(tid, f"➕ Added @{name}" if ok else f"❌ Couldn't add @{name}")

    elif cmd == "ban":
        if not arg:
            return say(tid, f"Usage: {PREFIX}ban @username")
        pk, name = resolve_user(arg.split()[0])
        if not pk:
            return say(tid, user_not_found())
        if blocked_target(tid, is_owner, pk, name, "ban"):
            return
        state["banned"][pk] = name
        save_state()
        kick_user(tid, pk)
        say(tid, f"🔨 Banned @{name}. They'll be auto-kicked if re-added.")

    elif cmd == "unban":
        pk, name = resolve_user(arg.split()[0]) if arg else (None, None)
        if pk and pk in state["banned"]:
            del state["banned"][pk]
            save_state()
            say(tid, f"✅ Unbanned @{name}")
        else:
            say(tid, "❌ That user isn't banned.")

    elif cmd == "banlist":
        if state["banned"]:
            say_long(tid, "🔨 Banned:\n" + "\n".join(f"@{n}" for n in state["banned"].values()))
        else:
            say(tid, "Ban list is empty.")

    elif cmd == "warn":
        pk, name, reason = split_target(arg)
        if not pk:
            return say(tid, f"Usage: {PREFIX}warn @username [reason]")
        if blocked_target(tid, is_owner, pk, name, "warn"):
            return
        add_warning(thread, pk, name, reason or "manual warning")

    elif cmd == "unwarn":
        pk, name, _ = split_target(arg)
        w = state["warnings"].get(tid, {})
        if pk and w.get(pk):
            w[pk] -= 1
            if w[pk] <= 0:
                w.pop(pk, None)
            save_state()
            say(tid, f"✅ Removed a warning from @{name} ({w.get(pk, 0)}/{state['max_warns']})")
        else:
            say(tid, "❌ That user has no warnings.")

    elif cmd == "clearwarns":
        pk, name, _ = split_target(arg)
        if pk and state["warnings"].get(tid, {}).pop(pk, None) is not None:
            save_state()
            say(tid, f"🧹 Cleared warnings for @{name}")
        else:
            say(tid, "❌ That user has no warnings.")

    elif cmd == "warns":
        w = state["warnings"].get(tid, {})
        if arg:
            pk, name, _ = split_target(arg)
            if not pk:
                return say(tid, user_not_found())
            say(tid, f"@{name}: {w.get(pk, 0)}/{state['max_warns']} warnings")
        elif w:
            say_long(tid, "⚠️ Warnings:\n" + "\n".join(
                f"@{name_for(thread, pk)}: {n}/{state['max_warns']}" for pk, n in w.items()))
        else:
            say(tid, "No active warnings in this chat. 🎉")

    elif cmd == "maxwarns":
        if arg.isdigit() and 1 <= int(arg) <= 20:
            state["max_warns"] = int(arg)
            save_state()
            say(tid, f"✅ Users are removed at {arg} warnings.")
        else:
            say(tid, f"Usage: {PREFIX}maxwarns <1-20>")

    elif cmd == "antispam":
        a = arg.lower().split()
        if a and a[0] in ("on", "off"):
            set_feature(tid, "antispam", a[0] == "on")
            say(tid, f"Anti-spam {'enabled ✅' if a[0] == 'on' else 'disabled ❌'}")
        elif len(a) == 3 and a[0] == "set" and a[1].isdigit() and a[2].isdigit():
            state["spam_msgs"], state["spam_secs"] = max(2, int(a[1])), max(2, int(a[2]))
            save_state()
            say(tid, f"✅ Flood limit: {state['spam_msgs']} messages in {state['spam_secs']}s")
        else:
            say(tid, f"Usage: {PREFIX}antispam on|off  or  {PREFIX}antispam set <msgs> <secs>")

    elif cmd == "antilink":
        if arg.lower() in ("on", "off"):
            set_feature(tid, "antilink", arg.lower() == "on")
            say(tid, f"Anti-link {'enabled ✅' if arg.lower() == 'on' else 'disabled ❌'}")
        else:
            say(tid, f"Usage: {PREFIX}antilink on|off")

    elif cmd == "autoreply":
        if arg.lower() in ("on", "off"):
            set_feature(tid, "autoreply", arg.lower() == "on")
            say(tid, f"Auto-reply {'enabled ✅' if arg.lower() == 'on' else 'disabled ❌'}")
        else:
            say(tid, f"Usage: {PREFIX}autoreply on|off")

    elif cmd == "addreply":
        if "|" not in arg:
            return say(tid, f"Usage: {PREFIX}addreply <keyword> | <reply>")
        kw, reply = (s.strip() for s in arg.split("|", 1))
        if not kw or not reply:
            return say(tid, f"Usage: {PREFIX}addreply <keyword> | <reply>")
        state["autoreplies"][kw.lower()] = reply
        save_state()
        say(tid, f"✅ I'll reply to '{kw.lower()}'")

    elif cmd == "delreply":
        if state["autoreplies"].pop(arg.lower(), None) is not None:
            save_state()
            say(tid, f"🗑️ Removed reply for '{arg.lower()}'")
        else:
            say(tid, "❌ No such keyword.")

    # ----- targeted replies (keep replying to one specific user) -----
    elif cmd == "replyto":
        if "|" in arg:
            who, _, rest = arg.partition("|")
            who, text_ = who.strip().split(" ")[0] if who.strip() else "", rest.strip()
        else:
            who, _, rest = arg.partition(" ")
            text_ = rest.strip()
        if not who or not text_:
            return say(tid, f"Usage: {PREFIX}replyto @username | text   ({{user}} = their @name)")
        if len(text_) > 500:
            return say(tid, "❌ That text is too long (max 500 characters).")
        pk, name = resolve_user(who)
        if not pk:
            return say(tid, user_not_found())
        if blocked_target(tid, is_owner, pk, name, "target"):
            return
        state["user_replies"].setdefault(tid, {})[pk] = {"name": name, "text": text_}
        save_state()
        log.warning("🎯 targeted reply set for @%s in '%s'", name, group_name(thread))
        say(tid, f"🎯 OK - I'll reply to @{name} in this GC (at most once every "
                 f"{state['user_reply_cooldown']}s) with:\n{text_}")

    elif cmd == "stopreply":
        rules = state["user_replies"].get(tid, {})
        if arg.lower() == "all":
            n = len(rules)
            state["user_replies"][tid] = {}
            save_state()
            return say(tid, f"🛑 Stopped all targeted replies in this GC ({n}).")
        pk, name = resolve_user(arg.split()[0]) if arg else (None, None)
        if pk and rules.pop(pk, None) is not None:
            save_state()
            say(tid, f"🛑 No longer replying to @{name}.")
        else:
            say(tid, f"❌ No targeted reply for that user. Usage: {PREFIX}stopreply @username|all")

    elif cmd == "userreplies":
        rules = state["user_replies"].get(tid, {})
        say_long(tid, "🎯 Targeted replies in this GC:\n" + "\n".join(
            f"@{r['name']} → {r['text'][:80]}" for r in rules.values())
            if rules else "No targeted replies set in this GC.")

    elif cmd == "replycooldown":
        if arg.isdigit() and 5 <= int(arg) <= 3600:
            state["user_reply_cooldown"] = int(arg)
            save_state()
            say(tid, f"✅ Targeted replies: at most one every {arg}s per user.")
        else:
            say(tid, f"Usage: {PREFIX}replycooldown <5-3600>  (now {state['user_reply_cooldown']}s)")

    # ----- targeted auto-reactions -----
    elif cmd in ("reactto", "autoreact"):
        if "|" in arg:
            who, _, rest = arg.partition("|")
            who = who.strip().split(" ")[0] if who.strip() else ""
        else:
            who, _, rest = arg.partition(" ")
        emojis = parse_emojis(rest)
        if not who or not emojis:
            return say(tid, f"Usage: {PREFIX}reactto @username | 😂 🔥   (emoji separated by spaces, max 6; "
                            f"I pick one at random. Works on the owner too.)")
        pk, name = resolve_user(who)
        if not pk:
            return say(tid, user_not_found())
        if pk != OWNER_PK and blocked_target(tid, is_owner, pk, name, "target"):
            return                                   # (the owner may be reacted to; the bot itself may not)
        if pk == MY_PK:
            return say(tid, "😅 I won't target myself.")
        state["user_reacts"].setdefault(tid, {})[pk] = {"name": name, "emojis": emojis}
        save_state()
        log.warning("😀 auto-react set for @%s in '%s': %s", name, group_name(thread), " ".join(emojis))
        say(tid, f"😀 OK - I'll react to @{name}'s messages in this GC with {' '.join(emojis)} "
                 f"(at most once every {state['user_react_cooldown']}s).")

    elif cmd == "stopreact":
        rules = state["user_reacts"].get(tid, {})
        if arg.lower() == "all":
            n = len(rules)
            state["user_reacts"][tid] = {}
            save_state()
            return say(tid, f"🛑 Stopped all auto-reactions in this GC ({n}).")
        pk, name = resolve_user(arg.split()[0]) if arg else (None, None)
        if pk and rules.pop(pk, None) is not None:
            save_state()
            say(tid, f"🛑 No longer reacting to @{name}.")
        else:
            say(tid, f"❌ No auto-reaction for that user. Usage: {PREFIX}stopreact @username|all")

    elif cmd == "reacts":
        rules = state["user_reacts"].get(tid, {})
        say_long(tid, "😀 Auto-reactions in this GC:\n" + "\n".join(
            f"@{r['name']} → {' '.join(r['emojis'])}" for r in rules.values())
            if rules else "No auto-reactions set in this GC.")

    elif cmd == "reactcooldown":
        if arg.isdigit() and 0 <= int(arg) <= 600:
            state["user_react_cooldown"] = int(arg)
            save_state()
            say(tid, f"✅ Auto-reactions: at most one every {arg}s per user." if int(arg)
                else "✅ Auto-reactions: react to every message (when the rate budget allows).")
        else:
            say(tid, f"Usage: {PREFIX}reactcooldown <0-600>  (now {state['user_react_cooldown']}s)")

    elif cmd == "replies":
        ar = state["autoreplies"]
        say_long(tid, "💬 Auto-replies:\n" + "\n".join(f"{k} → {v}" for k, v in ar.items())
                 if ar else "No auto-replies set.")

    elif cmd == "welcome":
        if arg.lower() in ("on", "off"):
            state["welcome_enabled"][tid] = arg.lower() == "on"
            save_state()
            say(tid, f"Welcome messages {'enabled ✅' if arg.lower() == 'on' else 'disabled ❌'}")
        else:
            say(tid, f"Usage: {PREFIX}welcome on|off")

    elif cmd == "setwelcome":
        if not arg:
            return say(tid, f"Usage: {PREFIX}setwelcome <text>  (use {{mentions}} and {{group}})")
        state["welcome_text"] = arg
        save_state()
        say(tid, "✅ Welcome message updated.")

    elif cmd == "say":
        if arg:
            say(tid, arg)

    elif cmd == "announce":
        if arg:
            say_long(tid, f"📢 ANNOUNCEMENT\n━━━━━━━━━━\n{arg}")

    # ----- admin control -----
    elif cmd in ("admin", "makeadmin", "promote"):
        if not arg:
            return say(tid, f"Usage: {PREFIX}admin @username")
        pk, name = resolve_user(arg.split()[0])
        if not pk:
            return say(tid, user_not_found())
        if pk == MY_PK:
            return say(tid, "😅 I can't promote myself - make me admin from the group settings.")
        if pk not in {str(u.pk) for u in thread.users}:
            return say(tid, f"❌ @{name} isn't in this GC.")
        if pk in admin_pks(thread):
            return say(tid, f"ℹ️ @{name} is already an admin.")
        ok = set_admin(tid, pk, True)
        say(tid, f"🛡️ @{name} is now a group admin." if ok
            else f"❌ Couldn't make @{name} admin (am I a group admin?)")

    elif cmd in ("unadmin", "removeadmin", "demote"):
        if not arg:
            return say(tid, f"Usage: {PREFIX}unadmin @username")
        pk, name = resolve_user(arg.split()[0])
        if not pk:
            return say(tid, user_not_found())
        if blocked_target(tid, is_owner, pk, name, "demote"):
            return
        if pk not in admin_pks(thread):
            return say(tid, f"ℹ️ @{name} isn't an admin.")
        ok = set_admin(tid, pk, False)
        say(tid, f"⬇️ @{name} is no longer a group admin." if ok
            else f"❌ Couldn't remove admin from @{name} (am I a group admin?)")

    elif cmd == "admins":
        ids = admin_pks(thread)
        if not ids:
            return say(tid, "No admins found in this GC.")
        names = [f"@{RUNTIME['me']} (bot)" if pk == MY_PK else f"@{name_for(thread, pk)}" for pk in ids]
        say_long(tid, f"🛡️ Group admins ({len(names)}):\n" + "\n".join(names))

    elif cmd in ("unadminall", "removealladmins", "onlyme"):
        # Remove admin from EVERYONE except the owner, all in one request.
        # Add "bot" (e.g. !unadminall bot) to also demote the bot itself, last.
        include_bot = arg.lower().lstrip("+") in ("bot", "all", "everyone")
        current = admin_pks(thread)
        targets = [pk for pk in current if pk not in (OWNER_PK, MY_PK)]
        note = ""
        if OWNER_PK not in current:
            if include_bot:
                return say(tid, f"⚠️ You aren't an admin here, so removing mine too would leave this GC with no admin. "
                                f"Run {PREFIX}admin @{OWNER_USERNAME} first.")
            note = f"\nℹ️ You aren't an admin here. Use {PREFIX}admin @{OWNER_USERNAME} to promote yourself."
        if not targets and not include_bot:
            return say(tid, "✅ Nobody except you (and me) is an admin here." + note)

        done, failed = [], []
        if targets:
            say(tid, f"🧹 Removing admin from {len(targets)} user(s) at once...")
            done, failed = demote_many(tid, targets)
        lines = []
        if done:
            lines.append("✅ Removed: " + ", ".join(f"@{name_for(thread, pk)}" for pk in done))
        if failed:
            lines.append("❌ Failed: " + ", ".join(f"@{name_for(thread, pk)}" for pk in failed)
                         + " (am I a group admin? rate-limited?)")

        if include_bot and not failed:
            lines.append("👋 Removing my own admin now - after this I can't kick, add or change admins "
                         f"until you promote me again.")
            say_long(tid, "\n".join(lines))
            ok = set_admin(tid, MY_PK, False)
            return say(tid, "⬇️ Done. Only you are an admin now." if ok else "❌ Couldn't remove my own admin.")
        if include_bot and failed:
            lines.append("ℹ️ I kept my own admin because some removals failed.")
        elif not failed:
            lines.append("✅ Only you (and me) are admins now.")
        say_long(tid, "\n".join(lines) + note)

    # ----- GC name lock -----
    elif cmd in ("setname", "lockname"):
        new = " ".join(arg.split())                       # one line, no stray whitespace
        have = current_title(thread)
        if not new:                                       # no name given: lock whatever it is now
            if not have:
                return say(tid, f"❌ This GC has no name yet. Use {PREFIX}setname <name> to set and lock one.")
            lock_name(tid, have)
            return say(tid, f"🔒 GC name locked to \"{have}\". If anyone changes it I'll change it back.\n"
                            f"Unlock with {PREFIX}unlockname")
        if _norm_name(new) == have:                       # already has that name: just lock it
            lock_name(tid, have)
            return say(tid, f"🔒 GC name locked to \"{have}\". Unlock with {PREFIX}unlockname")
        ok, shown = set_group_name(tid, new)
        if not ok:
            return say(tid, "❌ Couldn't change the GC name (am I a group admin?)")
        final = shown or new                              # lock what Instagram actually saved
        lock_name(tid, final)
        say(tid, f"🏷️ GC name changed to \"{final}\" and locked 🔒\nUnlock with {PREFIX}unlockname")

    elif cmd == "unlockname":
        if unlock_name(tid):
            say(tid, "🔓 GC name unlocked - it can be changed again.")
        else:
            say(tid, "ℹ️ The GC name isn't locked.")

    # ----- bot access (sudo) -----
    elif cmd == "addsudo":
        if not arg:
            return say(tid, f"Usage: {PREFIX}addsudo @username")
        pk, name = resolve_user(arg.split()[0])
        if not pk:
            return say(tid, user_not_found())
        if pk == OWNER_PK:
            return say(tid, "👑 That's you - you're already the owner.")
        if pk == MY_PK:
            return say(tid, "😅 I can't be my own sudo.")
        if pk in state["sudo"]:
            return say(tid, f"ℹ️ @{name} already has sudo access.")
        state["sudo"][pk] = name
        save_state()
        log.warning("🔑 sudo granted to @%s", name)
        say(tid, f"🔑 @{name} can now control the bot (everything except the 👑 owner-only commands).")

    elif cmd == "delsudo":
        if not arg:
            return say(tid, f"Usage: {PREFIX}delsudo @username")
        pk, name = resolve_user(arg.split()[0])
        if pk and state["sudo"].pop(pk, None) is not None:
            save_state()
            log.warning("🔑 sudo revoked from @%s", name)
            say(tid, f"🗝️ @{name} no longer has sudo access.")
        else:
            say(tid, "❌ That user isn't a sudo user.")

    elif cmd == "sudolist":
        sl = state["sudo"]
        say_long(tid, "🔑 Sudo users:\n" + "\n".join(f"@{n}" for n in sl.values())
                 if sl else "No sudo users yet.")

    # ----- more bots (started from chat) -----
    elif cmd in ("addbot", "bots", "stopbot"):
        hub_command(tid, cmd, arg)

    elif cmd in ("groups", "gcs", "chats"):
        if not group_names:
            return say(tid, "No groups indexed yet - try again in a few seconds.")
        lines = [f"{i}. {gname[:40]}{' 📍' if gid == tid else ''}{' 🙈' if is_ignored(gid) else ''}"
                 for i, (gid, gname) in enumerate(group_names.items(), 1)]
        say_long(tid, f"💬 My group chats ({len(lines)}):\n" + "\n".join(lines) +
                 f"\n\n🙈 = ignored · 📍 = this chat\n"
                 f"Ignore: {PREFIX}ignore 2,5 · 2-5 · all · part of a name\n"
                 f"Resume: {PREFIX}unignore 2,5")

    elif cmd == "ignore":
        if not arg:
            say(tid, f"🙈 Ignoring this GC completely. Send {PREFIX}unignore here to resume.")
            ignore_group(tid, group_name(thread))
        else:
            chosen, misses = select_groups(arg)
            done = [n for g, n in chosen.items() if ignore_group(g, n)]
            lines = []
            if done:
                lines.append("🙈 Now ignoring:\n" + "\n".join(f"• {n[:40]}" for n in done))
            if len(chosen) > len(done):
                lines.append(f"• {len(chosen) - len(done)} already ignored")
            if misses:
                lines.append("❌ No group matches: " + ", ".join(misses) + f"  (see {PREFIX}groups)")
            if tid in chosen:
                lines.append(f"ℹ️ This GC is ignored now - send {PREFIX}unignore here (or {PREFIX}unignore <number> from another GC) to resume.")
            say_long(tid, "\n".join(lines) or f"Usage: {PREFIX}ignore 2,5 | 2-5 | all | name   (see {PREFIX}groups)")

    elif cmd == "unignore":
        if not arg:
            say(tid, f"👀 This GC isn't ignored. Use {PREFIX}unignore <number> to resume another one (see {PREFIX}groups).")
        else:
            chosen, misses = select_groups(arg)
            done = [n for g, n in chosen.items() if unignore_group(g)]
            lines = []
            if done:
                lines.append("👀 Watching again:\n" + "\n".join(f"• {n[:40]}" for n in done))
            if len(chosen) > len(done):
                lines.append(f"• {len(chosen) - len(done)} weren't ignored")
            if misses:
                lines.append("❌ No group matches: " + ", ".join(misses) + f"  (see {PREFIX}groups)")
            say_long(tid, "\n".join(lines) or f"Usage: {PREFIX}unignore 2,5 | 2-5 | all | name")

    elif cmd == "ignored":
        ig = state["ignored"]
        say_long(tid, "🙈 Ignored GCs:\n" + "\n".join(f"• {n}" for n in ig.values())
                 if ig else "No ignored GCs.")

    elif cmd == "leave":
        say(tid, "👋 Leaving the chat. Bye!")
        try:
            paced(cl.private_request, f"direct_v2/threads/{tid}/leave/", data={"_uuid": cl.uuid})
            log.warning("🚪 left group '%s'", group_name(thread))
            if state["name_lock"].pop(tid, None) is not None:
                save_state()
        except Exception as e:
            log.error("leave failed: %s", e)

    else:
        say(tid, f"Unknown command. Try {PREFIX}help")


# ============================ TERMINAL HELP / CONSOLE ========================
STOP = threading.Event()
RUNTIME = {"me": "", "polls": 0, "groups": 0, "msgs": 0, "verbose": False}
group_names: dict = {}      # tid -> group title (filled by the main loop)

CONSOLE_CMDS = [
    ("help", "all bot commands & rules (+ this list)"),
    ("rules", "show only the group rules"),
    ("status", "bot / owner / uptime / counters"),
    ("groups", "list the watched group chats"),
    ("ignore", "pick GCs to ignore (2,5 or 2-5 or all)"),
    ("unignore", "pick GCs to start watching again (numbers, ranges or all)"),
    ("ignored", "list the ignored GCs"),
    ("sudo", "list sudo users  |  sudo add @user  |  sudo del @user"),
    ("bots", "list the extra bots started with !addbot"),
    ("stopbot", "stopbot <name> - stop an extra bot"),
    ("rate", "rate-limit status: cool-down, poll speed, sends/min"),
    ("stats", "CPU / RAM / disk / network"),
    ("verbose", "toggle detailed debug logs"),
    ("credits", "show the credits"),
    ("clear", "clear the screen & redraw banner"),
    ("quit", "stop the bot"),
]


def print_help(tid: str = "", only_rules: bool = False) -> None:
    """Coloured version of the same menu the bot sends with {PREFIX}help."""
    lines = build_help(tid).split("\n")
    if only_rules:
        idx = next((i for i, ln in enumerate(lines) if ln.startswith("📜")), 0)
        lines = lines[idx:]
    for line in lines:
        if not line.strip():
            print()
        elif line.startswith("━"):
            print("   " + rainbow("━" * rule_width()))
        elif BOT_NAME in line:
            print("   " + BOLD + rainbow(line, offset=3))
        elif line.startswith(PREFIX):
            cmd, sep, desc = line.partition(" – ")
            print(f"   {c(51)}{BOLD}{cmd}{R}" + (f" {DIM}– {desc}{R}" if sep else ""))
        elif line[0].isdigit():
            print(f"   {c(222)}{line}{R}")
        else:
            print(f"   {c(213)}{BOLD}{line}{R}")


def print_console_help() -> None:
    print(f"\n   {c(51)}{BOLD}⌨  TERMINAL COMMANDS{R} {DIM}(type + Enter any time while the bot runs){R}")
    for name, desc in CONSOLE_CMDS:
        print(f"   {c(46)}{BOLD}{name:<8}{R} {DIM}– {desc}{R}")
    print()


def live_header() -> None:
    w = rule_width()
    half = max(4, (w - 14) // 2)
    print("   " + rainbow("━" * half + "  LIVE LOGS  " + "━" * half))
    print()


def toggle_verbose() -> None:
    RUNTIME["verbose"] = not RUNTIME["verbose"]
    on = RUNTIME["verbose"]
    logging.getLogger().setLevel(logging.DEBUG if on else logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.DEBUG if on else logging.WARNING)
    log.info("🔬 verbose logs %s", "ON" if on else "OFF")


def select_groups(carg: str):
    """Pick GCs by number (2), list (2,5), range (2-5), 'all' or part of a name.
    Numbers match the order of the groups list. Returns ({tid: name}, [tokens that matched nothing])."""
    pool = [(i, tid, name) for i, (tid, name) in enumerate(group_names.items(), 1)]
    chosen, misses = {}, []
    for tok in (t.strip() for t in carg.split(",")):
        if not tok:
            continue
        low = tok.lower()
        if low in ("all", "*"):
            hits = pool
        elif re.fullmatch(r"\d+\s*-\s*\d+", tok):
            start, end = (int(v.strip()) for v in tok.split("-", 1))
            lo, hi = sorted((start, end))
            hits = [q for q in pool if lo <= q[0] <= hi]
        elif tok.isdigit():
            hits = [q for q in pool if q[0] == int(tok)]
        else:
            hits = [q for q in pool if low in q[2].lower()]
        if not hits:
            misses.append(tok)
        for _, tid, name in hits:
            chosen[tid] = name
    return chosen, misses


def console_pick(carg: str, ignoring: bool) -> None:
    """Terminal helper: choose GCs by number or name to ignore / un-ignore."""
    pool = [(i, tid, name) for i, (tid, name) in enumerate(group_names.items(), 1)]
    if not pool:
        print(f"   {c(220)}No groups indexed yet - wait for the first poll.{R}")
        return
    if not carg:
        print()
        for i, tid, name in pool:
            tag = f" {c(196)}[IGNORED]{R}" if is_ignored(tid) else ""
            print(f"   {c(46)}{i:>2}.{R} {name}{tag}")
        print()
        verb = "IGNORE" if ignoring else "WATCH AGAIN"
        try:
            carg = input(f"   {c(213)}❯{R} {BOLD}Numbers/names to {verb}{R} {DIM}(e.g. 2,5 or 2-5 or all; Enter to cancel){R}{c(213)}:{R} ").strip()
        except (EOFError, OSError):
            return
    if not carg:
        print(f"   {DIM}cancelled.{R}")
        return
    chosen, misses = select_groups(carg)
    for tok in misses:
        print(f"   {c(196)}✖ no group matches '{tok}'{R}")
    for tid, name in chosen.items():
        if ignoring:
            ok = ignore_group(tid, name)
            print(f"   {c(46) if ok else c(220)}{'🙈 ignoring' if ok else '• already ignored'}{R} {name}")
        else:
            ok = unignore_group(tid)
            print(f"   {c(46) if ok else c(220)}{'👀 watching again' if ok else '• was not ignored'}{R} {name}")


def console_sudo(carg: str) -> None:
    """Terminal helper: list / add / remove sudo users (people allowed to control the bot)."""
    parts = carg.split()
    sub = parts[0].lower() if parts else "list"
    if sub in ("add", "del", "remove") and len(parts) > 1:
        pk, name = resolve_user(parts[1])
        if not pk:
            print(f"   {c(196)}✖ user '{parts[1]}' not found{R}")
        elif sub == "add":
            if pk == OWNER_PK:
                print(f"   {c(220)}• that's the owner already{R}")
            else:
                state["sudo"][pk] = name
                save_state()
                print(f"   {c(46)}🔑 sudo granted to @{name}{R}")
        else:
            if state["sudo"].pop(pk, None) is not None:
                save_state()
                print(f"   {c(46)}🗝️  sudo revoked from @{name}{R}")
            else:
                print(f"   {c(220)}• @{name} wasn't a sudo user{R}")
        return
    print()
    if not state["sudo"]:
        print(f"   {c(220)}No sudo users. Add one with: sudo add @username{R}")
    for i, name in enumerate(state["sudo"].values(), 1):
        print(f"   {c(46)}{i:>2}.{R} @{name}")
    print()


def console_loop() -> None:
    """Reads terminal commands in the background so the bot keeps running."""
    while not STOP.is_set():
        try:
            raw = input()
        except (EOFError, OSError):
            return  # no interactive stdin (e.g. running in the background) - bot just keeps going
        cmd, _, carg = raw.strip().lstrip("/!:").partition(" ")
        cmd, carg = cmd.lower(), carg.strip()
        if not cmd:
            continue
        try:
            if cmd in ("help", "h", "?", "menu", "commands"):
                print()
                print_help()
                print_console_help()
            elif cmd == "rules":
                print()
                print_help(only_rules=True)
            elif cmd == "status":
                print()
                box([
                    f"{BOLD}Bot account :{R} @{RUNTIME['me']}",
                    f"{BOLD}Owner       :{R} @{OWNER_USERNAME}",
                    f"{BOLD}Uptime      :{R} {human_time(time.time() - START_TIME)}",
                    f"{BOLD}Polls       :{R} {RUNTIME['polls']}  ·  groups: {RUNTIME['groups']}  ·  msgs seen: {RUNTIME['msgs']}",
                    f"{BOLD}Banned      :{R} {len(state['banned'])}  ·  auto-replies: {len(state['autoreplies'])}  ·  ignored GCs: {len(state['ignored'])}  ·  sudo: {len(state['sudo'])}",
                    f"{BOLD}Verbose     :{R} {'ON' if RUNTIME['verbose'] else 'off'}",
                ], color=46)
                print()
            elif cmd == "groups":
                print()
                if not group_names:
                    print(f"   {c(220)}No groups indexed yet.{R}")
                for i, (gid, gname) in enumerate(group_names.items(), 1):
                    n = len(known_members.get(gid, ()))
                    tag = f" {c(196)}[IGNORED]{R}" if is_ignored(gid) else ""
                    print(f"   {c(46)}{i:>2}.{R} {gname} {DIM}({n} members · …{gid[-6:]}){R}{tag}")
                print()
            elif cmd in ("ignore", "exclude"):
                console_pick(carg, ignoring=True)
            elif cmd in ("unignore", "include"):
                console_pick(carg, ignoring=False)
            elif cmd == "ignored":
                print()
                if not state["ignored"]:
                    print(f"   {c(220)}No ignored GCs.{R}")
                for i, (gid, gname) in enumerate(state["ignored"].items(), 1):
                    print(f"   {c(196)}{i:>2}.{R} {gname} {DIM}(…{gid[-6:]}){R}")
                print()
            elif cmd == "sudo":
                console_sudo(carg)
            elif cmd == "bots":
                print()
                for line in get_local_hub().describe().split("\n"):
                    print(f"   {c(250)}{line}{R}")
                print()
            elif cmd == "stopbot":
                print(f"   {c(250)}{get_local_hub().stop(carg.split()[0] if carg else '')['msg']}{R}")
            elif cmd == "rate":
                print()
                for line in throttle.status():
                    print(f"   {c(250)}{line}{R}")
                print()
            elif cmd == "stats":
                print()
                for line in system_stats().split("\n"):
                    print(f"   {c(250)}{line}{R}")
                print()
            elif cmd == "verbose":
                toggle_verbose()
            elif cmd == "credits":
                print_credits()
            elif cmd == "clear":
                banner(animate=False)
                live_header()
            elif cmd in ("quit", "exit", "stop", "q"):
                log.warning("🛑 stop requested from the terminal")
                STOP.set()
                return
            else:
                print(f"   {c(196)}✖ Unknown terminal command '{cmd}'.{R} {DIM}Type help.{R}")
        except Exception as e:
            log.error("console command failed: %s", e)


# ================================ MAIN LOOP ==================================
known_members: dict = {}
seen_msgs: set = set()


def index_group_chats(threads) -> list:
    """Cache group names and member usernames from a fetched inbox snapshot."""
    groups = [thread for thread in threads if getattr(thread, "is_group", False)]
    for thread in groups:
        tid = str(thread.id)
        group_names[tid] = group_name(thread)
        for user in getattr(thread, "users", ()) or ():
            username = getattr(user, "username", None)
            if username:
                USER_CACHE[username.lower()] = str(user.pk)
    # Keep saved ignored chats selectable for unignore even if they are not in this inbox page.
    for tid, name in state["ignored"].items():
        group_names.setdefault(tid, name)
    return groups


def fetch_recent() -> list:
    """ONE request: the newest 20 chats. New messages / joins always bump a chat to the top."""
    if hasattr(cl, "direct_threads_chunk"):
        return cl.direct_threads_chunk()[0]
    return cl.direct_threads(amount=20)


def fetch_inbox(amount: int, pause: float = 1.5) -> list:
    """Deeper scan used once at startup: 20 chats per request, pausing between pages."""
    if not hasattr(cl, "direct_threads_chunk"):
        return cl.direct_threads(amount=amount)
    threads, cursor = [], None
    while True:
        chunk, cursor = cl.direct_threads_chunk(cursor=cursor)
        threads.extend(chunk)
        if not cursor or len(threads) >= amount:
            break
        time.sleep(pause)
    return threads[:amount]


def process_thread(thread, first_run: bool, owner_pk: str, my_pk: str) -> int:
    """Returns the number of new messages handled."""
    tid = str(thread.id)
    if tid in RESYNC:                 # just un-ignored: silently re-index, don't replay old messages
        RESYNC.discard(tid)
        first_run = True
    elif tid not in known_members:    # never indexed before: index silently, don't replay old messages
        first_run = True
    gname = group_name(thread)
    group_names[tid] = gname
    current = {str(u.pk): u for u in thread.users if str(u.pk) != my_pk}

    if first_run:
        log.info("📚 indexed group '%s' (%d members)", gname, len(current))

    # ---- new member detection ----
    if tid in known_members and not first_run:
        newcomers = []
        for pk in set(current) - known_members[tid]:
            log.info("🆕 new member @%s joined '%s'", current[pk].username, gname)
            if pk in state["banned"]:
                if kick_user(tid, pk):
                    say(tid, f"🔨 @{current[pk].username} is banned. Removed.")
                continue
            newcomers.append(current[pk])

        gone = known_members[tid] - set(current)
        for pk in gone:
            log.info("👋 member pk=%s left '%s'", pk, gname)

        if newcomers and state["welcome_enabled"].get(tid, True):
            mentions = ", ".join(f"@{u.username}" for u in newcomers)
            try:
                msg = state["welcome_text"].format(mentions=mentions, group=gname)
            except Exception:
                msg = DEFAULT_WELCOME.format(mentions=mentions, group=gname)
            log.info("🎉 welcoming %s in '%s'", mentions, gname)
            say(tid, msg)
    known_members[tid] = set(current)

    admins = {str(a) for a in (getattr(thread, "admin_user_ids", None) or [])}
    exempt = admins | {owner_pk, my_pk} | set(state["sudo"])

    handled = 0
    for m in reversed(thread.messages):  # oldest -> newest
        mid = str(m.id)
        if mid in seen_msgs:
            continue
        seen_msgs.add(mid)
        if first_run:
            continue

        sender = str(m.user_id)
        if sender == my_pk:
            continue
        handled += 1
        text = (m.text or "").strip()
        sname = name_for(thread, sender)
        log.info("💬 [%s] @%s: %s", gname[:18], sname, (mask_secret(text) or f"<{m.item_type}>").replace("\n", " ⏎ ")[:110])

        try:
            if moderate(thread, m, exempt):
                continue
            if text.startswith(PREFIX) and (sender == owner_pk or is_sudo(sender)):
                who = "owner" if sender == owner_pk else f"sudo @{sname}"
                log.info("⚡ %s command: %s", who, mask_secret(text)[:80])
                handle_command(thread, text, is_owner=(sender == owner_pk))
                continue
            maybe_user_react(thread, m, sender, sname)      # best-effort, never consumes the message
            if maybe_user_reply(thread, m, sender, sname):
                continue
            maybe_autoreply(tid, text)
        except Exception as e:
            log.exception("message handling error")
            if sender == owner_pk or is_sudo(sender):
                say(tid, f"⚠️ Error: {e}")
    try:                                  # GC name lock: revert if someone renamed the chat
        enforce_name_lock(thread)
    except Exception as e:
        log.error("name-lock check failed: %s", e)
    return handled


def watch_ignored(thread, first_run: bool, owner_pk: str) -> None:
    """Ignored chats are skipped entirely - the ONLY thing still checked is the owner's !unignore."""
    tid = str(thread.id)
    for m in reversed(thread.messages):
        mid = str(m.id)
        if mid in seen_msgs:
            continue
        if first_run:
            seen_msgs.add(mid)
            continue
        if str(m.user_id) != owner_pk:
            continue
        seen_msgs.add(mid)
        if (m.text or "").strip().lower() == f"{PREFIX}unignore".lower():
            if unignore_group(tid):
                say(tid, "👀 I'm watching this GC again.")
            return


def setup_prompts() -> str:
    """Ask for sessionid, owner username and welcome text. Returns sessionid."""
    global OWNER_USERNAME

    print(f"   {c(213)}{BOLD}── SETUP ──{R}  {DIM}(press Enter to keep a [default]){R}\n")

    env_sid = os.getenv("IG_SESSIONID", "").strip()
    env_user, env_pw = os.getenv("IG_USERNAME", "").strip().lstrip("@"), os.getenv("IG_PASSWORD", "")
    CREDS["totp"] = os.getenv("IG_TOTP_SEED", "").strip()
    CREDS["code"] = os.getenv("IG_2FA_CODE", "").strip()
    if env_sid:
        method = "1"                        # supplied through the environment - nothing to type
    elif env_user and env_pw:
        method = "2"
    else:
        print(f"   {c(51)}{BOLD}Login method{R}   {DIM}1 = Session ID    2 = Username & password{R}")
        while True:
            method = ask("Choose 1 or 2", default="2" if state.get("login_method") == "password" else "1")
            if method in ("1", "2"):
                break
            print(f"   {c(196)}✖ Type 1 or 2.{R}")
    sid = ""
    if method == "2":
        user = ask("Bot account username", default=env_user or state.get("login_username", "")).lstrip("@")
        saved = SESSION_FILE.exists() and state.get("session_user", "") == user.lower()
        pw = env_pw or ask("Password (input hidden" + (", Enter = reuse saved session" if saved else "") + ")",
                           secret=True, required=not saved)
        CREDS.update(username=user, password=pw)
        if pw:
            print(f"   {c(46)}✔ username/password received{R} {DIM}(password hidden, never saved){R}")
        else:
            print(f"   {c(220)}↻ reusing the saved session for @{user}{R}")
        print(f"   {DIM}A 2FA / security code is only asked for if Instagram requires one.{R}")
    else:
        if env_sid:
            sid = env_sid
        elif SESSION_FILE.exists():
            sid = ask("Session ID (input hidden, Enter = reuse saved session)", secret=True, required=False)
        else:
            sid = ask("Session ID (input hidden)", secret=True, required=True)
        if sid:
            note = f"  {c(220)}⚠ that looks short - make sure you copied the whole value{R}" if len(sid) < 20 else ""
            print(f"   {c(46)}✔ sessionid received{R} {DIM}({len(sid)} chars, hidden){R}{note}")
        else:
            print(f"   {c(220)}↻ reusing saved session ({SESSION_FILE}){R}")

    OWNER_USERNAME = ask("Owner username (you)",
                         default=os.getenv("OWNER_USERNAME") or state.get("owner_username", "")).lstrip("@").lower()
    state["owner_username"] = OWNER_USERNAME

    print(f"\n   {DIM}Welcome text placeholders: {{mentions}} = new members, {{group}} = group name{R}")
    print(f"   {DIM}Type \\n for a new line.{R}")
    while True:
        wt = ask("Welcome text", default=state.get("welcome_text", DEFAULT_WELCOME)).replace("\\n", "\n")
        try:
            wt.format(mentions="@x", group="y")
            break
        except Exception:
            print(f"   {c(196)}✖ Invalid placeholder. Only {{mentions}} and {{group}} are allowed.{R}")
    state["welcome_text"] = wt
    save_state()
    print()
    return sid


def show_dashboard(my_name: str) -> None:
    """Interactive mode only: info box, commands menu, live-log header and the terminal console."""
    print()
    box([
        f"{BOLD}Bot account :{R} @{my_name}",
        f"{BOLD}Owner       :{R} @{OWNER_USERNAME}",
        f"{BOLD}Sudo users  :{R} {len(state['sudo'])}",
        f"{BOLD}Prefix      :{R} {PREFIX}",
        f"{BOLD}Poll every  :{R} {POLL_MIN:g}-{POLL_MAX:g}s (1 request/poll; slows down when quiet or rate-limited)",
        f"{BOLD}Startup scan:{R} up to {THREADS_TO_SCAN} inbox threads, once",
        f"{BOLD}Welcome     :{R} {state['welcome_text'].replace(chr(10), ' ')[:44]}",
    ], color=46)
    print()
    print(f"   ✨ {BOLD}Welcome, {rainbow('@' + OWNER_USERNAME)}{BOLD}!{R} "
          f"{c(250)}{BOT_NAME} BOT is online as @{my_name}.{R}")
    print()
    print(f"   {c(51)}{BOLD}COMMANDS & RULES{R} {DIM}(same menu is sent in the GC by {PREFIX}help){R}\n")
    print_help()
    print_console_help()
    live_header()
    set_title(f"{BOT_NAME} BOT • @{my_name} • running")
    threading.Thread(target=console_loop, name="console", daemon=True).start()


def setup_headless() -> str:
    """Non-interactive setup for bots started by the multi-account launcher (all from env vars)."""
    global OWNER_USERNAME
    OWNER_USERNAME = (os.getenv("OWNER_USERNAME", "") or state.get("owner_username", "")).lstrip("@").lower()
    if not OWNER_USERNAME:
        sys.exit("OWNER_USERNAME is required")
    state["owner_username"] = OWNER_USERNAME
    wt = os.getenv("WELCOME_TEXT", "").replace("\\n", "\n")
    if wt:
        try:
            wt.format(mentions="@x", group="y")
            state["welcome_text"] = wt
        except Exception:
            log.warning("WELCOME_TEXT has an invalid placeholder - keeping the saved welcome text")
    save_state()
    CREDS.update(username=os.getenv("IG_USERNAME", "").strip().lstrip("@"), password=os.getenv("IG_PASSWORD", ""),
                 totp=os.getenv("IG_TOTP_SEED", "").strip(), code=os.getenv("IG_2FA_CODE", "").strip())
    return os.getenv("IG_SESSIONID", "").strip()


def main() -> None:
    global log, OWNER_PK, MY_PK
    if not HEADLESS:
        banner()
    log = setup_logging()
    if HEADLESS:
        session_id = setup_headless()
    else:
        boot_steps(["Loading modules", "Starting live logger", "Arming console"])
        session_id = setup_prompts()

    log.info("🔑 Logging in...")
    try:
        login(session_id)
    except Exception as e:
        log.error("Login failed: %s", e)
        sys.exit(f"\n   {c(196)}✖ Could not log in. {login_hint(e)}{R}")

    my_pk = str(cl.user_id)
    MY_PK = my_pk
    try:
        my_name = cl.username or cl.account_info().username
    except Exception:
        my_name = my_pk
    RUNTIME["me"] = my_name
    try:
        if state.get("owner_pk") and state.get("owner_pk_for") == OWNER_USERNAME:
            owner_pk = str(state["owner_pk"])                # remembered: no lookup request needed
        else:
            owner_pk = str(paced(cl.user_id_from_username, OWNER_USERNAME))
            state["owner_pk"], state["owner_pk_for"] = owner_pk, OWNER_USERNAME
            save_state()
        OWNER_PK = owner_pk
    except Exception as e:
        sys.exit(f"\n   {c(196)}✖ Owner username '{OWNER_USERNAME}' not found: {e}{R}")

    # Fetch group chats first so you can decide what the bot should ignore before it starts.
    startup_threads = None
    log.info("📥 Fetching recent inbox threads to find your group chats...")
    try:
        startup_threads = fetch_inbox(THREADS_TO_SCAN)
        startup_groups = index_group_chats(startup_threads)
        log.info("📚 fetched %d group chat(s) from %d recent inbox thread(s)",
                 len(startup_groups), len(startup_threads))
        if startup_groups and not HEADLESS:
            print(f"\n   {c(51)}{BOLD}GROUP CHAT SELECTION{R} {DIM}(choose which chats to ignore before monitoring starts){R}")
            console_pick("", ignoring=True)
        elif not startup_groups:
            log.warning("No group chats appeared in the fetched inbox snapshot.")
    except Exception as e:
        log.warning("Initial group fetch failed (%s); the bot will retry in its main loop.", e)
        if is_rate_limited(e):
            throttle.hit(e, "startup scan")
        startup_threads = None

    if HEADLESS:
        log.info("✅ @%s online - owner @%s - prefix '%s'", my_name, OWNER_USERNAME, PREFIX)
    else:
        show_dashboard(my_name)
        hub = get_local_hub()                      # this process also hosts bots added with !addbot
        hub.single = True
        hub.note_sid("(this bot)", getattr(cl, "sessionid", "") or session_id)
        if hub.dynamic:
            threading.Thread(target=hub.restore_saved, args=(6.0,), daemon=True).start()

    if HEADLESS:
        STOP.wait(random.uniform(0, POLL_MAX))      # bots started together shouldn't poll in lockstep
    first_run = True
    errors = 0
    polls = 0
    while not STOP.is_set():
        try:
            if startup_threads is not None:
                threads = startup_threads
                startup_threads = None
            else:
                throttle.wait_cooldown()
                threads = fetch_recent()
            groups = index_group_chats(threads)
            new_msgs = 0
            dm_new = 0
            for t in groups:
                tid = str(t.id)
                if tid in state["ignored"]:
                    watch_ignored(t, first_run, owner_pk)
                    continue
                new_msgs += process_thread(t, first_run, owner_pk, my_pk)
            for t in threads:                      # private DMs: only the owner's !addbot / !bots / !stopbot
                if not getattr(t, "is_group", False):
                    dm_new += watch_dm(t, owner_pk)
            flush_hub_replies()
            n_ign = sum(1 for t in groups if str(t.id) in state["ignored"])
            if first_run:
                log.info("✅ ready — watching %d group chat(s), ignoring %d. Send %shelp in a GC.",
                         len(groups) - n_ign, n_ign, PREFIX)
            first_run = False
            errors = 0
            polls += 1
            throttle.ok()
            throttle.note_poll(bool(new_msgs or dm_new))
            RUNTIME.update(polls=polls, groups=len(groups))
            RUNTIME["msgs"] += new_msgs
            log.debug("🔄 poll #%d · %d group(s) · %d new msg(s) · uptime %s",
                     polls, len(groups), new_msgs, human_time(time.time() - START_TIME))
        except LoginRequired:
            if not relogin(session_id):
                sys.exit(1)
        except ChallengeRequired:
            log.error("Instagram challenge required - verify the account manually, then restart.")
            sys.exit(1)
        except Exception as e:
            if is_rate_limited(e):
                throttle.hit(e, "inbox poll")     # the cool-down is waited out before the next poll
            else:
                errors += 1
                wait = min(300, 15 * errors)
                log.error("Loop error (%s). Backing off %ss", e, wait)
                STOP.wait(wait)
        STOP.wait(throttle.poll_delay())
    goodbye()


def goodbye() -> None:
    if HUB is not None:
        HUB.stop_all()                      # also stops bots that were started with !addbot
    print(f"\n\n   {rainbow('Stopped. See you soon! — ' + BOT_NAME + ' BOT')}\n")


# ============================ MULTI-ACCOUNT LAUNCHER =========================
BOT_SCRIPT = os.path.abspath(__file__)
ACCOUNTS_TEMPLATE = {
    "owner": "your_main_username",
    "stagger_seconds": 8,
    "accounts": [
        {"name": "bot1", "sessionid": "PASTE_SESSIONID_HERE"},
        {"name": "bot2", "sessionid_env": "BOT2_SESSIONID", "enabled": False,
         "owner": "optional_other_owner", "prefix": ".",
         "welcome": "👋 Hi {mentions}, welcome to {group}!"},
        {"name": "bot3", "username": "bot3_instagram_name", "password_env": "BOT3_PASSWORD",
         "totp_seed_env": "BOT3_2FA_SECRET", "proxy": "", "enabled": False},
    ],
}
LAUNCH_COLORS = [51, 213, 46, 220, 208, 171, 118, 39]
_print_lock = threading.Lock()


def find_accounts_file(explicit: str = ""):
    if explicit:
        f = Path(explicit).expanduser()
        return f if f.exists() else None
    for base in (Path.cwd(), Path(BOT_SCRIPT).parent):
        f = base / "accounts.json"
        if f.exists():
            return f
    return None


def init_accounts_template() -> None:
    target = Path.cwd() / "accounts.json"
    if target.exists():
        print(f"   {c(220)}accounts.json already exists ({target}) - edit it and run the script again.{R}")
        return
    target.write_text(json.dumps(ACCOUNTS_TEMPLATE, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(target, 0o600)          # it will hold session ids - keep it private
    except OSError:
        pass
    print(f"   {c(46)}✔ created {target}{R}")
    print(f"   {DIM}Fill in each bot's sessionid (or \"sessionid_env\": the NAME of an environment variable that\n"
          f"   holds it), set \"owner\", then run: python {os.path.basename(BOT_SCRIPT)}{R}")
    print(f"   {c(220)}⚠ Treat accounts.json like a password file and never commit it to git.{R}")


def load_accounts(path):
    """Read accounts.json -> (list of account dicts, stagger seconds)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"accounts": data}
    default_owner = str(data.get("owner", "") or "").lstrip("@").lower()
    stagger = float(data.get("stagger_seconds", 8))
    base = path.parent
    accounts, seen = [], set()
    for i, a in enumerate(data.get("accounts", []), 1):
        if not a.get("enabled", True):
            continue
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(a.get("name") or f"bot{i}"))
        if name in seen:
            raise ValueError(f"duplicate account name '{name}'")
        seen.add(name)
        sid, source = str(a.get("sessionid", "") or "").strip(), "accounts.json"
        if (not sid or "PASTE" in sid.upper()) and a.get("sessionid_env"):
            sid, source = os.getenv(str(a["sessionid_env"]), "").strip(), f"env {a['sessionid_env']}"
        if "PASTE" in sid.upper():
            sid = ""
        user = str(a.get("username", "") or "").strip().lstrip("@")
        pw = str(a.get("password", "") or "")
        if not pw and a.get("password_env"):
            pw = os.getenv(str(a["password_env"]), "")
        totp = str(a.get("totp_seed", "") or "").strip()
        if not totp and a.get("totp_seed_env"):
            totp = os.getenv(str(a["totp_seed_env"]), "").strip()
        has_login = bool(user and pw)
        profile = base / "bots" / name
        has_saved = (profile / "session.json").exists()
        owner = str(a.get("owner") or default_owner).lstrip("@").lower()
        problem = ""
        if not owner:
            problem = "no owner set"
        elif not sid and not has_login and not has_saved:
            problem = "no sessionid or username+password (and no saved session yet)"
        if not sid and has_login and not problem:
            source = "username/password"
        elif not sid and has_saved and not problem:
            source = "saved session"
        accounts.append({"name": name, "owner": owner, "sid": sid, "source": source if not problem else "-",
                         "welcome": str(a.get("welcome") or ""), "prefix": str(a.get("prefix") or ""),
                         "profile": profile, "problem": problem, "username": user, "password": pw, "totp": totp,
                         "proxy": str(a.get("proxy") or "")})
    return accounts, stagger


def _clean_name(raw) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(raw or "").strip())[:24]


def _pump(name: str, color: int, proc, width: int, acc: dict = None) -> None:
    """Copy a bot's output to the terminal with a coloured [name] prefix."""
    tag = f"{c(color)}{BOLD}[{name:<{width}}]{R} "
    for line in proc.stdout:
        if acc is not None and not acc.get("user"):
            mm = re.search(r"@(\S+) online - owner", ANSI_RE.sub("", line))
            if mm:
                acc["user"] = mm.group(1)
        with _print_lock:
            sys.stdout.write(tag + line.rstrip("\n") + "\n")
            sys.stdout.flush()
    code = proc.wait()
    with _print_lock:
        sys.stdout.write(f"{tag}{c(220 if code == 0 else 196)}⛔ stopped (exit code {code}){R}\n")
        sys.stdout.flush()


def start_bot(acc: dict, color: int, width: int, hub_dir=None):
    acc["profile"].mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    for k in ("IG_SESSIONID", "WELCOME_TEXT", "BOT_HUB", "IG_USERNAME", "IG_PASSWORD", "IG_TOTP_SEED", "IG_2FA_CODE"):
        env.pop(k, None)             # never hand one bot another bot's login details
    env.update(BOT_CHILD="1", HEADLESS="1", BOT_TAG=acc["name"], OWNER_USERNAME=acc["owner"],
               PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    if hub_dir:
        env["BOT_HUB"] = str(hub_dir)       # lets the bot ask for more bots (!addbot)
    if acc["sid"]:
        env["IG_SESSIONID"] = acc["sid"]
    if acc.get("username") and acc.get("password"):
        env["IG_USERNAME"], env["IG_PASSWORD"] = acc["username"], acc["password"]
        if acc.get("totp"):
            env["IG_TOTP_SEED"] = acc["totp"]
    if acc.get("proxy"):
        env["IG_PROXY"] = acc["proxy"]
    if acc["welcome"]:
        env["WELCOME_TEXT"] = acc["welcome"]
    if acc["prefix"]:
        env["PREFIX"] = acc["prefix"]
    proc = subprocess.Popen(
        [sys.executable, "-u", BOT_SCRIPT], cwd=str(acc["profile"]), env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    threading.Thread(target=_pump, args=(acc["name"], color, proc, width, acc), daemon=True).start()
    return proc


class Hub:
    """Starts / stops the bot processes. It lives in the launcher (accounts.json mode) or in the bot you
    started from the terminal (single mode). Bots talk to it through small request files, which is how
    '!addbot <sessionid>' typed in a chat ends up as a new running bot."""

    def __init__(self, base, width: int = 8) -> None:
        self.base = Path(base)
        self.dir = self.base / "bots" / "_hub"
        self.saved_file = self.base / "bots" / "dynamic_bots.json"
        self.width = max(8, width)
        self.procs: dict = {}        # name -> Popen
        self.accs: dict = {}         # name -> account dict
        self.sids: dict = {}         # name -> sessionid (memory only; used to refuse duplicates)
        self.logins: dict = {}       # name -> username of bots started with username/password
        self.added_at: dict = {}     # sessionid -> time it was added from chat
        self.dynamic: dict = {}      # bots added from chat (remembered between runs)
        self.single = False
        self.lock = threading.RLock()
        self.stop_flag = threading.Event()
        self._color = 0
        self._started = False

    # ---- lifecycle ----
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for sub in ("requests", "responses"):
            d = self.dir / sub
            d.mkdir(parents=True, exist_ok=True)
            for f in d.glob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        try:
            self.dynamic = {e["name"]: e for e in json.loads(self.saved_file.read_text(encoding="utf-8"))}
        except Exception:
            self.dynamic = {}
        threading.Thread(target=self._watch, name="hub", daemon=True).start()

    def _watch(self) -> None:
        req_dir, res_dir = self.dir / "requests", self.dir / "responses"
        while not self.stop_flag.is_set():
            try:
                for f in sorted(req_dir.glob("*.json")):
                    try:
                        req = json.loads(f.read_text(encoding="utf-8"))
                    except Exception:
                        req = None
                    try:
                        f.unlink()                      # a sessionid never stays on disk in a request file
                    except OSError:
                        pass
                    if not isinstance(req, dict):
                        continue
                    try:
                        res = self.handle(req)
                    except Exception as e:
                        res = {"ok": False, "msg": f"❌ hub error: {e}"}
                    rid = re.sub(r"[^0-9A-Za-z_-]", "", str(req.get("id", "")))
                    if rid:
                        tmp = res_dir / f"{rid}.tmp"
                        tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
                        tmp.replace(res_dir / f"{rid}.json")
            except Exception:
                pass
            self.stop_flag.wait(1.0)

    def _say(self, text: str) -> None:
        with _print_lock:
            print(text)

    # ---- bookkeeping ----
    def note_sid(self, name: str, sid: str) -> None:
        sid = unquote(sid or "").strip()
        if sid:
            self.sids[name] = sid

    def _save_dynamic(self) -> None:
        try:
            self.saved_file.parent.mkdir(parents=True, exist_ok=True)
            self.saved_file.write_text(json.dumps(list(self.dynamic.values()), indent=2, ensure_ascii=False),
                                       encoding="utf-8")
        except OSError as e:
            self._say(f"   {c(220)}⚠ could not save {self.saved_file}: {e}{R}")

    def _next_name(self) -> str:
        taken = set(self.accs) | set(self.dynamic)
        bots_dir = self.base / "bots"
        if bots_dir.exists():
            taken |= {q.name for q in bots_dir.iterdir()}
        i = 2
        while f"bot{i}" in taken:
            i += 1
        return f"bot{i}"

    def _alive(self, name: str) -> bool:
        return name not in self.procs or self.procs[name].poll() is None

    def spawn(self, acc: dict):
        color = LAUNCH_COLORS[self._color % len(LAUNCH_COLORS)]
        self._color += 1
        proc = start_bot(acc, color, self.width, self.dir)
        with self.lock:
            self.procs[acc["name"]] = proc
            self.accs[acc["name"]] = acc
            if acc.get("sid"):
                self.sids[acc["name"]] = unquote(acc["sid"])
            if acc.get("username"):
                self.logins[acc["name"]] = acc["username"].lower()
            acc["password"] = ""                  # the child has it now - don't keep it in this process
        return proc

    # ---- requests ----
    def handle(self, req: dict) -> dict:
        kind = req.get("kind")
        if kind == "add":
            return self.add(req)
        if kind == "list":
            return {"ok": True, "msg": self.describe()}
        if kind == "stop":
            return self.stop(str(req.get("name", "")))
        return {"ok": False, "msg": "❌ unknown request"}

    def add(self, req: dict) -> dict:
        sid = unquote(str(req.get("sid", "")).strip())
        username = str(req.get("username", "")).strip().lstrip("@")
        password = str(req.get("password", ""))
        owner = str(req.get("owner", "")).lstrip("@").lower()
        by_login = bool(username or password)
        if by_login and not (username and password):
            return {"ok": False, "msg": "❌ I need both a username and a password."}
        if not by_login and len(sid) < 20:
            return {"ok": False, "msg": "❌ That sessionid is too short."}
        if not owner:
            return {"ok": False, "msg": "❌ No owner known for the new bot."}
        key = f"login:{username.lower()}" if by_login else sid
        with self.lock:
            known = self.logins if by_login else self.sids
            probe = username.lower() if by_login else sid
            for n, sv in known.items():
                if sv == probe and self._alive(n):
                    recent = time.time() - self.added_at.get(key, 0) < 60
                    # several bots in one chat all see the same !addbot: only the first one answers
                    return {"ok": False, "quiet": recent,
                            "msg": f"ℹ️ That {'account' if by_login else 'session'} is already running as '{n}'."}
            running = [n for n, pr in self.procs.items() if pr.poll() is None]
            if len(running) >= MAX_BOTS:
                return {"ok": False, "msg": f"❌ Limit reached ({MAX_BOTS} bots). Stop one with stopbot first."}
            name = _clean_name(req.get("name")) or self._next_name()
            if name in running:
                return {"ok": False, "msg": f"❌ A bot named '{name}' is already running. Pick another name."}
            prefix = str(req.get("prefix") or "")
            acc = {"name": name, "owner": owner, "sid": "" if by_login else sid, "source": "chat command",
                   "welcome": "", "prefix": prefix, "profile": self.base / "bots" / name, "problem": "",
                   "username": username, "password": password, "totp": "", "proxy": ""}
            self.spawn(acc)
            self.added_at[key] = time.time()
            self.dynamic[name] = {"name": name, "owner": owner, "prefix": prefix}
            self._save_dynamic()
        self._say(f"   {c(46)}✔ bot '{name}' added from chat (owner @{owner}){R}")
        what = "password" if by_login else "sessionid"
        extra = "\n⚠️ If this account has 2FA I can't ask for the code from here - use a sessionid instead." if by_login else ""
        return {"ok": True, "msg": (f"🚀 Starting bot '{name}' (prefix {prefix or PREFIX}) - online in about 20s.\n"
                                    f"🔐 Please UNSEND your message that contains the {what} "
                                    f"(long-press it → Unsend).{extra}")}

    def stop(self, name: str) -> dict:
        with self.lock:
            key = next((n for n in self.procs if n.lower() == name.strip().lower()), None)
            if not key:
                have = ", ".join(self.procs) or "none"
                return {"ok": False, "msg": f"❌ No bot called '{name}'. Known bots: {have}"}
            pr = self.procs[key]
            if pr.poll() is None:
                pr.terminate()
                try:
                    pr.wait(6)
                except subprocess.TimeoutExpired:
                    pr.kill()
            if self.dynamic.pop(key, None) is not None:
                self._save_dynamic()
        return {"ok": True, "msg": f"🛑 Bot '{key}' stopped."}

    def describe(self) -> str:
        with self.lock:
            lines = []
            if self.single:
                lines.append(f"(this bot) – @{RUNTIME['me']} – prefix '{PREFIX}' – running ✅")
            for n, pr in self.procs.items():
                a = self.accs.get(n, {})
                state_txt = "running ✅" if pr.poll() is None else f"stopped (code {pr.returncode}) ⛔"
                who = f"@{a['user']}" if a.get("user") else "starting…"
                lines.append(f"{n} – {who} – prefix '{a.get('prefix') or PREFIX}' – {state_txt}")
        return "🤖 Bots:\n" + "\n".join(lines) if lines else "No extra bots started yet."

    def restore_saved(self, stagger: float = 0.0) -> None:
        """Start the bots that were added from chat in an earlier run (their saved session is reused)."""
        for e in list(self.dynamic.values()):
            name = e["name"]
            if name in self.procs and self.procs[name].poll() is None:
                continue
            profile = self.base / "bots" / name
            if not (profile / "session.json").exists():
                self._say(f"   {c(220)}⚠ chat-added bot '{name}' skipped: no saved session (use !addbot again){R}")
                continue
            if stagger and self.stop_flag.wait(stagger):
                return
            acc = {"name": name, "owner": str(e.get("owner", "")), "sid": "", "source": "saved session",
                   "welcome": "", "prefix": str(e.get("prefix") or ""), "profile": profile, "problem": ""}
            self.spawn(acc)
            self._say(f"   {c(46)}✔ restored chat-added bot '{name}'{R}")

    def stop_all(self) -> None:
        self.stop_flag.set()
        procs = list(self.procs.values())
        for pr in procs:
            if pr.poll() is None:
                pr.terminate()
        deadline = time.time() + 6
        for pr in procs:
            try:
                pr.wait(max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                pr.kill()


def launch_multi(path) -> None:
    banner()
    try:
        accounts, stagger = load_accounts(path)
    except Exception as e:
        sys.exit(f"   {c(196)}✖ Could not read {path}: {e}{R}")
    if not accounts:
        sys.exit(f"   {c(196)}✖ No enabled accounts in {path}.{R}")

    print(f"   {c(213)}{BOLD}── MULTI-BOT MODE ──{R}  {DIM}{path}{R}\n")
    width = max(len(a["name"]) for a in accounts)
    for i, a in enumerate(accounts):
        col = LAUNCH_COLORS[i % len(LAUNCH_COLORS)]
        status = f"{c(196)}✖ skipped: {a['problem']}{R}" if a["problem"] else f"{c(46)}✔ ready{R} {DIM}(session: {a['source']}){R}"
        print(f"   {c(col)}{BOLD}{a['name']:<{width}}{R}  owner @{a['owner'] or '?'}  {status}")
    ready = [a for a in accounts if not a["problem"]]
    if not ready:
        sys.exit(f"\n   {c(196)}✖ Nothing to start. Fix accounts.json (run with --init for a template).{R}")
    print(f"\n   {DIM}Starting {len(ready)} bot(s), {stagger:g}s apart. Type 'list', 'stop <name>' or 'quit' + Enter. Ctrl+C stops all.{R}")
    print(f"   {DIM}More bots: as the owner send  {PREFIX}addbot <sessionid> [name] [prefix]  to any bot in chat.{R}\n")

    hub = Hub(path.parent, width)
    hub.start()

    def console() -> None:
        while not hub.stop_flag.is_set():
            try:
                raw = input().strip()
            except (EOFError, OSError):
                return
            cmd, _, carg = raw.partition(" ")
            cmd = cmd.lower()
            if cmd in ("quit", "exit", "stop") and not carg:
                hub.stop_flag.set()
            elif cmd == "list":
                with _print_lock:
                    for line in hub.describe().split("\n"):
                        print(f"   {line}")
            elif cmd == "stop" and carg:
                with _print_lock:
                    print(f"   {hub.stop(carg.strip())['msg']}")
            elif cmd:
                print(f"   {DIM}commands: list | stop <name> | quit{R}")

    threading.Thread(target=console, daemon=True).start()
    try:
        for i, acc in enumerate(ready):
            if i and stagger:
                hub.stop_flag.wait(stagger)
            if hub.stop_flag.is_set():
                break
            hub.spawn(acc)
        if not hub.stop_flag.is_set():
            hub.restore_saved(stagger)           # bots that were added from chat in earlier runs
        while not hub.stop_flag.is_set() and any(p.poll() is None for p in list(hub.procs.values())):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop_all()
    goodbye()


def _arg_value(flag: str) -> str:
    a = sys.argv
    return a[a.index(flag) + 1] if flag in a and a.index(flag) + 1 < len(a) else ""


if __name__ == "__main__":
    try:
        if os.getenv("BOT_CHILD") != "1":
            if "--init" in sys.argv:
                init_accounts_template()
                sys.exit(0)
            if "--single" not in sys.argv:
                acc_file = find_accounts_file(_arg_value("--accounts"))
                if acc_file:
                    launch_multi(acc_file)
                    sys.exit(0)
        main()
    except (KeyboardInterrupt, EOFError):
        goodbye()
