from cryo.security.google_id_token import verify_google_id_token
from cryo.security.passwords import hash_password, verify_password


def test_password_round_trip() -> None:
    stored = hash_password("correct-horse")
    assert verify_password("correct-horse", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("correct-horse", None)


def test_google_token_rejects_wrong_audience() -> None:
    claims = verify_google_id_token(
        "token",
        client_id="expected.apps.googleusercontent.com",
        hosted_domain="company.com",
        fetcher=lambda _token: {
            "aud": "other.apps.googleusercontent.com",
            "iss": "https://accounts.google.com",
            "email": "a@company.com",
            "email_verified": "true",
        },
    )
    assert claims is None


def test_google_token_accepts_matching_domain() -> None:
    claims = verify_google_id_token(
        "token",
        client_id="expected.apps.googleusercontent.com",
        hosted_domain="company.com",
        fetcher=lambda _token: {
            "aud": "expected.apps.googleusercontent.com",
            "iss": "https://accounts.google.com",
            "email": "A@Company.com",
            "email_verified": "true",
            "sub": "123",
        },
    )
    assert claims == {"email": "a@company.com", "subject": "123"}
