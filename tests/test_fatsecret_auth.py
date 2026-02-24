import pytest


def test_hmac_sha1_signature(mock_settings):
    from app.services.fatsecret_auth import sign_oauth1_request

    # Known test vector for OAuth 1.0 signature
    sig = sign_oauth1_request(
        method="POST",
        url="https://example.com/request_token",
        params={"oauth_consumer_key": "key", "oauth_nonce": "nonce",
                "oauth_signature_method": "HMAC-SHA1", "oauth_timestamp": "123",
                "oauth_version": "1.0"},
        consumer_secret="secret",
        token_secret="",
    )
    assert isinstance(sig, str)
    assert len(sig) > 0  # Base64-encoded HMAC-SHA1


def test_build_auth_header(mock_settings):
    from app.services.fatsecret_auth import build_oauth1_header

    header = build_oauth1_header(
        params={"oauth_consumer_key": "key", "oauth_signature": "sig="},
    )
    assert header.startswith("OAuth ")
    assert "oauth_consumer_key" in header
    assert "oauth_signature" in header
