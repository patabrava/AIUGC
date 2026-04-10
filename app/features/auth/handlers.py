"""
Lippe Lift Studio Auth Handlers
Login, OTP verification, and logout routes.
"""

from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.logging import get_logger
from app.features.auth.queries import is_email_allowed, send_otp, verify_otp, sign_out
from app.features.auth.middleware import (
    encode_session_cookie,
    decode_session_cookie,
    should_bypass_auth,
)

logger = get_logger(__name__)
templates = Jinja2Templates(directory="templates")

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_session_response(email: str, settings, redirect_url: str = "/batches") -> RedirectResponse:
    cookie_data = {
        "access_token": f"review-access-token:{email}",
        "refresh_token": f"review-refresh-token:{email}",
    }
    cookie_value = encode_session_cookie(cookie_data, settings.token_encryption_key)
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    return response

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Render the login page."""
    settings = get_settings()
    if should_bypass_auth(request):
        return RedirectResponse(url="/batches", status_code=302)
    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "step": "email",
        "email": "",
        "error": None,
        "otp_code_length": settings.auth_otp_code_length,
        "bypass_auth_in_development": settings.is_auth_bypassed,
        "reviewer_login_enabled": bool(settings.reviewer_login_email.strip() and settings.reviewer_login_token.strip()),
    })


@router.get("/review", response_class=HTMLResponse)
async def reviewer_login(request: Request, token: str = Query(default="")):
    """Passwordless reviewer login for TikTok review access."""
    settings = get_settings()
    reviewer_email = settings.reviewer_login_email.strip().lower()
    reviewer_token = settings.reviewer_login_token.strip()

    if not reviewer_email or not reviewer_token:
        logger.warning("reviewer_login_not_configured")
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "step": "email",
            "email": "",
            "error": "Reviewer login is not configured yet.",
            "otp_code_length": settings.auth_otp_code_length,
            "bypass_auth_in_development": settings.is_auth_bypassed,
            "reviewer_login_enabled": False,
        }, status_code=503)

    if token != reviewer_token:
        logger.warning("reviewer_login_denied", token_present=bool(token))
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "step": "email",
            "email": "",
            "error": "Invalid reviewer access link.",
            "otp_code_length": settings.auth_otp_code_length,
            "bypass_auth_in_development": settings.is_auth_bypassed,
            "reviewer_login_enabled": True,
        }, status_code=403)

    logger.info("reviewer_login_success", email=reviewer_email)
    return _build_session_response(reviewer_email, settings)


@router.post("/send-otp", response_class=HTMLResponse)
async def handle_send_otp(request: Request, email: str = Form(...)):
    """Validate email and send OTP code."""
    normalized_email = email.strip().lower()
    settings = get_settings()

    if should_bypass_auth(request):
        response = _build_session_response(normalized_email, settings)
        logger.info("auth_login_bypassed_local", email=normalized_email)
        return response

    if not is_email_allowed(normalized_email):
        logger.warning("auth_email_rejected", email=normalized_email)
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "step": "email",
            "email": normalized_email,
            "error": "This email is not authorized to access Lippe Lift Studio.",
            "otp_code_length": settings.auth_otp_code_length,
            "bypass_auth_in_development": settings.is_auth_bypassed,
            "reviewer_login_enabled": bool(settings.reviewer_login_email.strip() and settings.reviewer_login_token.strip()),
        })

    try:
        await send_otp(normalized_email)
    except Exception as e:
        logger.error("auth_send_otp_error", email=normalized_email, error=str(e))
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "step": "email",
            "email": normalized_email,
            "error": str(e),
            "otp_code_length": settings.auth_otp_code_length,
            "bypass_auth_in_development": settings.is_auth_bypassed,
            "reviewer_login_enabled": bool(settings.reviewer_login_email.strip() and settings.reviewer_login_token.strip()),
        })

    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "step": "otp",
        "email": normalized_email,
        "error": None,
        "otp_code_length": settings.auth_otp_code_length,
        "bypass_auth_in_development": settings.is_auth_bypassed,
        "reviewer_login_enabled": bool(settings.reviewer_login_email.strip() and settings.reviewer_login_token.strip()),
    })


@router.post("/verify-otp")
async def handle_verify_otp(request: Request, email: str = Form(...), token: str = Form(...)):
    """Verify OTP code and create session."""
    normalized_email = email.strip().lower()
    clean_token = token.strip()
    settings = get_settings()

    if should_bypass_auth(request):
        response = _build_session_response(normalized_email, settings)
        logger.info("auth_otp_bypassed_local_verified", email=normalized_email)
        return response

    session = await verify_otp(normalized_email, clean_token)
    if not session:
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "step": "otp",
            "email": normalized_email,
            "error": "Invalid or expired code. Please try again.",
            "otp_code_length": settings.auth_otp_code_length,
            "bypass_auth_in_development": settings.is_auth_bypassed,
            "reviewer_login_enabled": bool(settings.reviewer_login_email.strip() and settings.reviewer_login_token.strip()),
        })

    settings = get_settings()
    cookie_data = {
        "access_token": session["access_token"],
        "refresh_token": session["refresh_token"],
    }
    cookie_value = encode_session_cookie(cookie_data, settings.token_encryption_key)

    response = RedirectResponse(url="/batches", status_code=302)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    logger.info("auth_login_success", email=normalized_email)
    return response


@router.get("/callback", response_class=HTMLResponse)
async def auth_callback(request: Request):
    """
    Handle Supabase magic link redirect.
    Supabase puts tokens in the URL hash (#access_token=...&refresh_token=...).
    This page captures them client-side and posts to /auth/session to set the cookie.
    """
    return templates.TemplateResponse("auth/callback.html", {"request": request})


@router.post("/session")
async def handle_session(request: Request):
    """Accept tokens from the magic link callback and set session cookie."""
    body = await request.json()
    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")

    if not access_token or not refresh_token:
        return RedirectResponse(url="/auth/login", status_code=302)

    settings = get_settings()
    cookie_data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
    cookie_value = encode_session_cookie(cookie_data, settings.token_encryption_key)

    from fastapi.responses import JSONResponse
    response = JSONResponse(content={"ok": True, "redirect": "/batches"})
    response.set_cookie(
        key=settings.session_cookie_name,
        value=cookie_value,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
    )
    logger.info("auth_magic_link_login")
    return response


@router.post("/logout")
async def handle_logout(request: Request):
    """Sign out and clear session cookie."""
    settings = get_settings()
    cookie_value = request.cookies.get(settings.session_cookie_name)
    if cookie_value:
        session = decode_session_cookie(cookie_value, settings.token_encryption_key)
        if session and "access_token" in session:
            await sign_out(session["access_token"])

    response = RedirectResponse(url="/auth/login", status_code=302)
    response.delete_cookie(key=settings.session_cookie_name)
    logger.info("auth_logout")
    return response
