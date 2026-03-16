import hmac
import secrets


def set_security_headers(response):
    """Apply baseline browser hardening headers.

    CSP is intentionally strict but still allows current frontend dependencies
    (jsdelivr for Chart.js and Google Fonts).
    """
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response


def generate_csrf_token(session_obj) -> str:
    """Create and store a per-session CSRF token used by write endpoints."""
    token = secrets.token_urlsafe(32)
    session_obj["csrf_token"] = token
    return token


def validate_csrf_token(session_obj, header_token: str) -> bool:
    """Compare header token with stored session token using constant-time checks."""
    session_token = session_obj.get("csrf_token", "")
    return bool(header_token and session_token and hmac.compare_digest(header_token, session_token))
