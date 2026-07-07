from __future__ import annotations

from atlas.safety.matchers import is_credential_access, is_financial, is_mass_deletion


def test_credential_matcher() -> None:
    dirs = ["~/.ssh", "/etc/ssl"]
    hit, _ = is_credential_access(["~/.ssh/id_rsa"], dirs)
    assert hit

    hit, _ = is_credential_access(["/tmp/safe.txt"], dirs)
    assert not hit

    hit, _ = is_credential_access(["./.env"], dirs)
    assert hit

    hit, _ = is_credential_access(["/etc/ssl/certs/ca.pem"], dirs)
    assert hit


def test_mass_deletion() -> None:
    hit, _ = is_mass_deletion(6, None, 5)
    assert hit

    hit, _ = is_mass_deletion(3, None, 5)
    assert not hit

    hit, _ = is_mass_deletion(0, "/*", 5)
    assert hit  # glob override


def test_financial() -> None:
    domains = ["stripe.com"]
    hit, _ = is_financial("https://api.stripe.com/v1", None, domains)
    assert hit

    hit, _ = is_financial(None, "stripe list", domains)
    assert hit

    hit, _ = is_financial("https://github.com", "git push", domains)
    assert not hit
