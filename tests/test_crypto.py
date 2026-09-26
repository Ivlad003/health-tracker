def test_secret_hash_verification(mock_settings):
    from app.crypto import hash_secret, verify_secret

    stored = hash_secret("tok")
    assert stored.startswith("sha256:") and "tok" not in stored[7:]
    assert verify_secret("tok", stored)
    assert not verify_secret("other", stored)
    assert verify_secret("legacy", "legacy")  # pre-migration plaintext
    assert not verify_secret(None, stored)
    assert not verify_secret("tok", None)
