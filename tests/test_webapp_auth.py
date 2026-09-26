"""Telegram initData validation (plan §16, AC-17)."""
import json
import time

import pytest

from app.services.webapp_auth import InitDataError, sign_init_data, validate_init_data

TOKEN = "123456:TEST-TOKEN"


def _fields(**over):
    base = {
        "auth_date": str(int(time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": 42, "first_name": "A", "language_code": "uk"}),
    }
    base.update(over)
    return base


def test_valid_init_data():
    result = validate_init_data(sign_init_data(_fields(), TOKEN), TOKEN, max_age_seconds=300)
    assert result["user"]["id"] == 42


def test_forged_signature_rejected():
    data = sign_init_data(_fields(), "999:OTHER")
    with pytest.raises(InitDataError, match="bad_signature"):
        validate_init_data(data, TOKEN, max_age_seconds=300)


def test_tampered_user_rejected():
    data = sign_init_data(_fields(), TOKEN).replace("%22id%22%3A+42", "%22id%22%3A+43")
    with pytest.raises(InitDataError):
        validate_init_data(data, TOKEN, max_age_seconds=300)


def test_expired_and_future_rejected():
    old = sign_init_data(_fields(auth_date=str(int(time.time()) - 3600)), TOKEN)
    with pytest.raises(InitDataError, match="init_data_expired"):
        validate_init_data(old, TOKEN, max_age_seconds=300)
    future = sign_init_data(_fields(auth_date=str(int(time.time()) + 3600)), TOKEN)
    with pytest.raises(InitDataError, match="auth_date_in_future"):
        validate_init_data(future, TOKEN, max_age_seconds=300)


def test_missing_user_or_hash_rejected():
    fields = _fields()
    fields.pop("user")
    with pytest.raises(InitDataError, match="user_missing"):
        validate_init_data(sign_init_data(fields, TOKEN), TOKEN, max_age_seconds=300)
    with pytest.raises(InitDataError, match="hash_missing"):
        validate_init_data("auth_date=1&user=%7B%7D", TOKEN, max_age_seconds=300)


def test_duplicate_fields_rejected():
    data = sign_init_data(_fields(), TOKEN) + "&user=%7B%22id%22%3A1%7D"
    with pytest.raises(InitDataError, match="duplicate"):
        validate_init_data(data, TOKEN, max_age_seconds=300)


def test_no_bot_token_means_no_login():
    with pytest.raises(InitDataError, match="bot_token_missing"):
        validate_init_data(sign_init_data(_fields(), TOKEN), "", max_age_seconds=300)


def test_valid_init_data_returns_verified_hash():
    data = sign_init_data(_fields(), TOKEN)
    result = validate_init_data(data, TOKEN, max_age_seconds=300)
    assert len(result["hash"]) == 64
    # Uppercase hex from a client is accepted and normalised.
    digest = data.rsplit("hash=", 1)[1]
    upper = data.replace(digest, digest.upper())
    assert validate_init_data(upper, TOKEN, max_age_seconds=300)["hash"] == result["hash"]


def test_malformed_and_missing_auth_date_rejected():
    with pytest.raises(InitDataError, match="init_data_malformed"):
        validate_init_data("no-equals-sign", TOKEN, max_age_seconds=300)
    fields = _fields()
    fields.pop("auth_date")
    with pytest.raises(InitDataError, match="auth_date_missing"):
        validate_init_data(sign_init_data(fields, TOKEN), TOKEN, max_age_seconds=300)
    with pytest.raises(InitDataError, match="init_data_missing"):
        validate_init_data("a=" + "x" * 9000, TOKEN, max_age_seconds=300)


def test_non_integer_user_id_rejected():
    fields = _fields(user=json.dumps({"id": "42"}))
    with pytest.raises(InitDataError, match="user_missing"):
        validate_init_data(sign_init_data(fields, TOKEN), TOKEN, max_age_seconds=300)
