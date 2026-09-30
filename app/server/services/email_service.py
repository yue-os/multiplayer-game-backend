import os
import threading
import requests
import smtplib
import ssl
import re
import html as html_lib
from email.message import EmailMessage
from pathlib import Path
from dotenv import load_dotenv

# Only load .env if it exists (standard for local dev)
env_path = Path(__file__).resolve().parents[3] / ".env"
if env_path.exists():
    load_dotenv(env_path, override=True)
else:
    # In production (e.g. Railway), variables should be set in the dashboard
    load_dotenv()

def _build_otp_html(otp: str) -> str:
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
            <div style="text-align: center; margin-bottom: 20px;">
                <h1 style="color: #00b4d8;">BatangAware</h1>
            </div>
            <h2 style="color: #00b4d8; text-align: center;">Welcome to BatangAware!</h2>
            <p style="font-size: 16px; color: #333;">Thank you for registering. Please use the following One-Time Password (OTP) to verify your account:</p>
            <div style="text-align: center; margin: 30px 0;">
                <span style="font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #aeea00; background-color: #05172a; padding: 10px 20px; border-radius: 5px;">{otp}</span>
            </div>
            <p style="font-size: 14px; color: #777; text-align: center;">This code will expire shortly. If you did not request this, please ignore this email.</p>
            <hr style="border: 0; border-top: 1px solid #eee; margin: 20px 0;">
            <p style="font-size: 12px; color: #999; text-align: center;">&copy; 2026 BatangAware Team</p>
        </div>
    </body>
    </html>
    """

def _build_password_reset_html(reset_link: str) -> str:
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
            <h2 style="color: #00b4d8; text-align: center;">Password reset requested</h2>
            <p style="font-size: 16px; color: #333;">Use the secure link below to choose a new password.</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{reset_link}" style="display: inline-block; padding: 12px 18px; background-color: #00b4d8; color: #ffffff; text-decoration: none; border-radius: 8px;">Reset password</a>
            </p>
            <p style="font-size: 14px; color: #777; text-align: center;">This link expires in 30 minutes and can only be used once.</p>
            <p style="font-size: 14px; color: #777; text-align: center;">If you did not request this, you can ignore this email.</p>
        </div>
    </body>
    </html>
    """

def _send_smtp_message(to_email: str, subject: str, html_content: str, text_content: str | None = None) -> bool:
    smtp_user = os.getenv('SMTP_EMAIL') or os.getenv('GMAIL_SMTP_USER') or os.getenv('SMTP_GMAIL_USER')
    smtp_password = os.getenv('SMTP_PASSWORD') or os.getenv('GMAIL_SMTP_APP_PASSWORD') or os.getenv('SMTP_GMAIL_APP_PASSWORD')
    if not smtp_user or not smtp_password:
        print("ERROR: SMTP email credentials are not configured.")
        return False

    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_timeout = float(os.getenv('SMTP_TIMEOUT', '15'))
    message = EmailMessage()
    message['From'] = os.getenv('SMTP_FROM') or smtp_user
    message['To'] = to_email
    message['Subject'] = subject
    plain_text = text_content or html_lib.unescape(re.sub(r'<[^>]*>', ' ', html_content))
    message.set_content(re.sub(r'\s+', ' ', plain_text).strip())
    message.add_alternative(html_content, subtype='html')

    try:
        if smtp_port == 465:
            with smtplib.SMTP_SSL(
                smtp_host,
                smtp_port,
                timeout=smtp_timeout,
                context=ssl.create_default_context(),
            ) as server:
                server.login(smtp_user, smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(smtp_user, smtp_password)
                server.send_message(message)
        print(f"Email sent successfully via SMTP to {to_email}")
        return True
    except Exception as exc:
        print(f"SMTP email error: {exc}")
        return False


def _send_email_message(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str | None = None,
) -> bool:
    smtp_password = os.getenv('SMTP_PASSWORD') or ''
    api_key = os.getenv('RESEND_API_KEY')
    if not api_key and smtp_password.startswith('re_'):
        api_key = smtp_password

    if not api_key:
        return _send_smtp_message(to_email, subject, html_content, text_content)

    from_email = os.getenv('SMTP_FROM', 'batangaware@yhubkeysystem.site')
    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": from_email,
        "to": to_email,
        "subject": subject,
        "html": html_content,
    }
    if text_content:
        payload["text"] = text_content

    try:
        print(f"[DEBUG] Sending email via Resend API to {to_email}...")
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code in (200, 201, 202):
            print(f"Email sent successfully via Resend API to {to_email}")
            return True
        print(f"Resend API Error ({response.status_code}): {response.text}")
        return False
    except Exception as exc:
        print(f"Failed to connect to Resend API: {exc}")
        return False

def send_otp_email(to_email: str, otp: str):
    html = _build_otp_html(otp)
    return _send_email_message(to_email, "BatangAware - Registration OTP", html, f"Your BatangAware registration code is {otp}.")

def send_password_reset_email(to_email: str, reset_link: str):
    html = _build_password_reset_html(reset_link)
    return _send_email_message(to_email, "BatangAware - Password reset", html, f"Use this secure link within 30 minutes to set a new password: {reset_link}")

def send_password_reset_email_async(to_email: str, reset_link: str, on_complete=None):
    smtp_password = os.getenv('SMTP_PASSWORD') or os.getenv('GMAIL_SMTP_APP_PASSWORD') or os.getenv('SMTP_GMAIL_APP_PASSWORD')
    smtp_user = os.getenv('SMTP_EMAIL') or os.getenv('GMAIL_SMTP_USER') or os.getenv('SMTP_GMAIL_USER')
    has_resend = bool(os.getenv('RESEND_API_KEY') or (smtp_password and smtp_password.startswith('re_')))
    if not has_resend and not (smtp_user and smtp_password):
        print("ERROR: Cannot queue password reset email because no mail credentials are configured.")
        return False

    def _send():
        try:
            sent = send_password_reset_email(to_email, reset_link)
        except Exception as exc:
            print(f"Password reset email send failed: {exc}")
            sent = False
        if on_complete:
            try:
                on_complete(bool(sent))
            except Exception as exc:
                print(f"Failed to record password reset email delivery: {exc}")

    threading.Thread(target=_send, name=f"reset-email-{to_email}", daemon=True).start()
    return True

def send_otp_email_async(to_email: str, otp: str):
    def _send():
        send_otp_email(to_email, otp)
    threading.Thread(target=_send, name=f"otp-email-{to_email}", daemon=True).start()
    return True
