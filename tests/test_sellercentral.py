"""The saved Seller Central session: written after sign-in, loaded where the profile has no sign-in."""
from __future__ import annotations

import json
import os

from app import config
from app.sellercentral import restore_session, save_session, session_file

COOKIE = {"name": "at-acbuk", "value": "token", "domain": ".amazon.co.uk", "path": "/", "expires": -1,
          "httpOnly": True, "secure": True, "sameSite": "Lax"}


class FakeContext:
    """The parts of a Playwright browser context the session code uses."""

    def __init__(self, cookies=()):
        self.jar = list(cookies)

    def storage_state(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"cookies": self.jar, "origins": []}, handle)

    def cookies(self, urls=None):
        return list(self.jar)

    def add_cookies(self, cookies):
        self.jar += cookies


def test_the_session_is_saved_and_signs_in_a_new_profile():
    settings = config.load_settings()
    path = save_session(FakeContext([COOKIE]), settings)
    assert path == session_file(settings) and json.loads(path.read_text(encoding="utf-8"))["cookies"] == [COOKIE]
    os.utime(path, (path.stat().st_mtime + 10,) * 2)  # as if copied to the server afterwards
    fresh = FakeContext()
    assert restore_session(fresh, settings) and fresh.jar == [COOKIE]


def test_the_profiles_own_sign_in_is_not_replaced_by_its_own_older_save():
    settings = config.load_settings()
    save_session(FakeContext([COOKIE]), settings)
    newer = FakeContext([{**COOKIE, "value": "rotated"}])
    assert not restore_session(newer, settings) and newer.jar[0]["value"] == "rotated"


def test_without_a_session_file_nothing_changes():
    context = FakeContext()
    assert not restore_session(context, config.load_settings()) and context.jar == []
