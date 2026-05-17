import os
import threading
import requests
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
            <h2 style="color: #00b4d8; text-align: center;">Password reset approved</h2>
            <p style="font-size: 16px; color: #333;">Your password reset request was approved.</p>
            <p style="text-align: center; margin: 30px 0;">
                <a href="{reset_link}" style="display: inline-block; padding: 12px 18px; background-color: #00b4d8; color: #ffffff; text-decoration: none; border-radius: 8px;">Reset password</a>
            </p>
            <p style="font-size: 14px; color: #777; text-align: center;">This secure link expires in 30 minutes.</p>
            <p style="font-size: 14px; color: #777; text-align: center;">If you did not request this, contact your administrator immediately.</p>
        </div>
    </body>
    </html>
    """

def _send_resend_api_message(to_email: str, subject: str, html_content: str) -> bool:
    # Use RESEND_API_KEY if exists, otherwise fallback to SMTP_PASSWORD (which contains the re_ key)
    api_key = os.getenv('RESEND_API_KEY') or os.getenv('SMTP_PASSWORD')
    from_email = os.getenv('SMTP_FROM', 'batangaware@yhubkeysystem.site')
    
    if not api_key:
        print("ERROR: Resend API Key not found (RESEND_API_KEY or SMTP_PASSWORD).")
        return False

    url = "https://api.resend.com/emails"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "from": from_email,
        "to": to_email,
        "subject": subject,
        "html": html_content
    }

    try:
        print(f"[DEBUG] Sending email via Resend API to {to_email}...")
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        
        if response.status_code in (200, 201, 202):
            print(f"✅ Email sent successfully via API to {to_email}")
            return True
        else:
            print(f"❌ Resend API Error ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"❌ Failed to connect to Resend API: {e}")
        return False

def send_otp_email(to_email: str, otp: str):
    html = _build_otp_html(otp)
    return _send_resend_api_message(to_email, "BatangAware - Registration OTP", html)

def send_password_reset_email(to_email: str, reset_link: str):
    html = _build_password_reset_html(reset_link)
    return _send_resend_api_message(to_email, "BatangAware - Password reset approved", html)

def send_password_reset_email_async(to_email: str, reset_link: str):
    def _send():
        send_password_reset_email(to_email, reset_link)
    threading.Thread(target=_send, name=f"reset-email-{to_email}", daemon=True).start()
    return True

def send_otp_email_async(to_email: str, otp: str):
    def _send():
        send_otp_email(to_email, otp)
    threading.Thread(target=_send, name=f"otp-email-{to_email}", daemon=True).start()
    return True
