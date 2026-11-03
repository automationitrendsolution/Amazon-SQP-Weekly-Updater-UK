"""Seller Central UK (sellercentral.amazon.co.uk) in Chrome without a window (headless), with a saved
sign-in (data/browser/sellercentral-profile/, this project's own: the US project's sign-in is for .com).

Seller Central requires 2-step verification. ``login`` runs in the terminal: it fills the email and
password from .env (SELLERCENTRAL_EMAIL / SELLERCENTRAL_PASSWORD), asks the person for the OTP, ticks
"Don't require OTP on this browser", then opens a Search Query Details page (CHECK_URL) to prove the
session works. Only if SELLERCENTRAL_TOTP_SECRET is set (an authenticator-app key) is the code generated
here. Passwords and codes are never logged.

After signing in, the session is also saved as data/browser/sellercentral-session.json (cookies). Chrome's
profile only works on the machine it was made on; the session file can be copied to another one (the
server), which then starts signed in. It gives access to the account: keep it private, never commit it.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import struct
import time
from pathlib import Path

from .config import Settings
from .exceptions import ConfigurationError, PipelineError
from .logging_config import get_logger

log = get_logger("sellercentral")

HOME_URL = "https://sellercentral.amazon.co.uk/home"
# Seller Central UK's addresses (older accounts are sent to the pan-European one).
HOSTS = ("sellercentral.amazon.co.uk", "sellercentral-europe.amazon.com")
MARKETPLACE_NAME = "United Kingdom"
# Opened after signing in, to prove the session reaches Brand Analytics (any of our ASINs and a past week).
CHECK_URL = ("https://sellercentral.amazon.co.uk/brand-analytics/dashboard/query-detail?view-id=query-detail-asin-view"
             "&asin=B0D5YK4YDH&search-term-freeform=halloween%20decorations&reporting-range=weekly"
             "&weekly-week=2026-10-31&country-id=gb")


class SellerCentralError(PipelineError):
    """Seller Central sign-in needed, or its page is not as expected."""

    exit_code = 4


def _profile_dir(settings: Settings) -> Path:
    return settings.data_dir / "browser" / "sellercentral-profile"


def _open(settings: Settings):
    """Chrome with this project's own profile (not the person's everyday Chrome profile); without a window
    unless SC_HEADLESS=false."""
    from playwright.sync_api import sync_playwright

    profile = _profile_dir(settings)
    profile.mkdir(parents=True, exist_ok=True)
    channel = None if settings.sc_browser_channel == "chromium" else settings.sc_browser_channel
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            str(profile), channel=channel, headless=settings.sc_headless, accept_downloads=True,
            user_agent=_user_agent(playwright, channel) if settings.sc_headless else None,
            viewport={"width": 1440, "height": 900}, args=["--disable-blink-features=AutomationControlled"])
    except Exception:
        playwright.stop()
        raise
    try:
        restore_session(context, settings)
    except (OSError, ValueError, KeyError) as exc:
        log.warning("Seller Central session file not used (%s)", exc)
    return playwright, context


def _user_agent(playwright, channel: str | None) -> str:
    """This Chrome's user agent as a window would send it: headless Chrome calls itself "HeadlessChrome",
    which gives a robot away."""
    browser = playwright.chromium.launch(channel=channel, headless=True)
    try:
        return browser.new_page().evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
    finally:
        browser.close()


def session_file(settings: Settings) -> Path:
    return settings.data_dir / "browser" / "sellercentral-session.json"


def _restored_marker(settings: Settings) -> Path:
    return settings.data_dir / "browser" / ".session-used"


def save_session(context, settings: Settings) -> Path:
    """Save the signed-in session's cookies to session_file (portable, unlike the Chrome profile)."""
    path = session_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    _restored_marker(settings).touch()  # our own save is never newer than the profile it came from
    log.info("Seller Central session saved: %s", path)
    return path


def restore_session(context, settings: Settings) -> bool:
    """Load session_file's cookies when this profile has no sign-in yet, or the file was replaced since it was
    last used (a fresh copy from another machine). Otherwise the profile's own, newer sign-in is kept."""
    path, marker = session_file(settings), _restored_marker(settings)
    if not path.is_file():
        return False
    copied_in = not marker.is_file() or path.stat().st_mtime > marker.stat().st_mtime
    if context.cookies(HOME_URL) and not copied_in:
        return False
    context.add_cookies(json.loads(path.read_text(encoding="utf-8"))["cookies"])
    marker.touch()
    log.info("Seller Central session loaded from %s", path)
    return True


def _close(context, playwright) -> None:
    """Close Chrome; if the person already closed the window, there is nothing left to close."""
    try:
        context.close()
    except Exception:
        pass
    playwright.stop()


def _totp(secret: str, at: float | None = None) -> str:
    """RFC 6238 code (30 s, 6 digits, SHA-1), as an authenticator app shows it."""
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    counter = int((at or time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def _signed_in(page) -> bool:
    """On a Seller Central page that is not Amazon's sign-in / OTP form (judged by the path: after
    signing in, the home page's address can still carry "signin" in its query string)."""
    from urllib.parse import urlsplit

    for candidate in page.context.pages:
        parts = urlsplit(candidate.url)
        if (parts.hostname or "").endswith(HOSTS) and not parts.path.startswith("/ap/") \
                and "signin" not in parts.path.lower():
            return True
    return False


def _fill_if_present(page, selector: str, value: str, timeout_ms: int = 4000) -> bool:
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
    except Exception:
        return False
    page.fill(selector, value)
    return True


def _click_if_present(page, selector: str, timeout_ms: int = 3000) -> bool:
    try:
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
    except Exception:
        return False
    page.click(selector)
    return True


def _screenshot(settings: Settings, page, name: str) -> Path | None:
    """What the hidden browser shows, for the error message."""
    shot = settings.data_dir / "screenshots" / f"sellercentral_{name}_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        shot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shot), full_page=True)
    except Exception:
        return None
    return shot


def login(settings: Settings) -> tuple[str, int]:
    """One-time sign-in in the terminal (OTP typed by the person); then CHECK_URL must show its table.
    The session is saved. Returns the check page's address and its number of competitor rows."""
    playwright, context = _open(settings)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(HOME_URL, wait_until="domcontentloaded")
        _settle(page)
        if not _signed_in(page):
            _terminal_sign_in(settings, page)
        log.info("Seller Central signed in (%s)", page.url.split("?")[0])
        save_session(context, settings)
        rows = check_page(settings, page)
        save_session(context, settings)  # with what opening Brand Analytics added
        return page.url, rows
    finally:
        _close(context, playwright)


def check_page(settings: Settings, page, url: str = CHECK_URL) -> int:
    """Open a Search Query Details page and read it exactly as the scraping does; the number of competitor
    rows. What the page showed is kept in data/screenshots/ (screenshot and captured text) either way."""
    from urllib.parse import parse_qs, urlsplit

    from .competitors import _CAPTURE_JS, has_table, parse_capture

    query = parse_qs(urlsplit(url).query)
    for _ in range(2):  # right after signing in, Seller Central may first land on Home
        page.goto(url, wait_until="domcontentloaded")
        _settle(page)
        if page.url.split("?")[0] == url.split("?")[0]:
            break
    try:
        page.wait_for_function("""() => /Access Required|Product Title|No data|no results/i
                                      .test(document.body.innerText)""", timeout=45000)
    except Exception:
        pass
    _settle(page, 15000)
    data = page.evaluate(_CAPTURE_JS)
    data.update(asin=query["asin"][0], search_query=query["search-term-freeform"][0])
    shot = _screenshot(settings, page, "check")
    captured = shot.with_suffix(".json") if shot else None
    if captured:
        captured.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("Seller Central check page: %s, %s", shot, captured)
    if "access required" in " ".join(data["pageText"]).lower():
        raise SellerCentralError(f"signed in, but Brand Analytics shows 'Access Required' for this user; "
                                 f"screenshot: {shot}")
    if not has_table(data["rows"]):
        raise SellerCentralError(f"signed in, but the Search Query Details table did not show (on "
                                 f"{page.url.split('?')[0]}); screenshot: {shot}, page text: {captured}")
    try:
        return len(parse_capture(data)["competitors"])
    except (ValueError, StopIteration) as exc:
        raise SellerCentralError(f"signed in and the table shows, but it could not be read ({str(exc) or 'no table rows'})"
                                 f"; the session is saved. Page text for fixing the reader: {captured}") from None


def _settle(page, timeout_ms: int = 30000) -> None:
    """Wait until Seller Central stops loading (redirects, account selection, the page's scripts)."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass
    time.sleep(2)


def _terminal_sign_in(settings: Settings, page) -> None:
    """Email and password from .env; the OTP from the person at the terminal; the account picker by name."""
    if not (settings.sc_email and settings.sc_password):
        raise ConfigurationError("set SELLERCENTRAL_EMAIL and SELLERCENTRAL_PASSWORD (or AMAZON_*) in .env")
    if _fill_if_present(page, "#ap_email", settings.sc_email, 15000):
        _click_if_present(page, "#continue")
    if _fill_if_present(page, "#ap_password", settings.sc_password, 15000):
        _click_if_present(page, "input[name=rememberMe]", 1000)
        _click_if_present(page, "#signInSubmit")
    for _ in range(3):
        if not _fill_if_present(page, "#auth-mfa-otpcode", "", 15000):
            break
        code = (_totp(settings.sc_totp_secret) if settings.sc_totp_secret
                else input("Seller Central OTP (sent to your phone / authenticator): ").strip())
        page.fill("#auth-mfa-otpcode", code)
        _click_if_present(page, "#auth-mfa-remember-device", 1000)
        _click_if_present(page, "#auth-signin-button")
        _settle(page)
        if "/ap/mfa" not in page.url:
            break
        print("That code was not accepted; try again.")
    _settle(page)
    if "account-switcher" in page.url or page.locator("text=Select an account").count():
        try:  # Econour / United Kingdom, as in the browser
            page.get_by_text("Econour", exact=False).first.click()
            page.get_by_text(MARKETPLACE_NAME, exact=False).first.click()
            _click_if_present(page, "button:has-text('Select account')", 5000)
        except Exception:
            print("Could not choose the account by itself; the screenshot shows the page.")
    if not _signed_in(page):
        raise SellerCentralError(f"sign-in did not reach Seller Central (on {page.url.split('?')[0]}); screenshot: "
                                 f"{_screenshot(settings, page, 'signin')}")
