import smtplib
import os
import threading
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)

def _build_otp_message(to_email: str, otp: str, smtp_email: str) -> MIMEMultipart:
    subject = "BatangAware - Registration OTP"
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f4f4f4; padding: 20px;">
        <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 10px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
            <div style="text-align: center; margin-bottom: 20px;">
                <img src="cid:logo" alt="BatangAware Logo" style="width: 120px; height: auto;">
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

    msg = MIMEMultipart('related')
    msg['From'] = smtp_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))

    # Inline images make Gmail SMTP noticeably slower during local testing.
    if os.getenv('EMAIL_ATTACH_LOGO', '').lower() in ('1', 'true', 'yes'):
        logo_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "logo.png")
    else:
        logo_path = ""

    if logo_path and os.path.exists(logo_path):
        with open(logo_path, 'rb') as f:
            logo_data = f.read()
        logo_image = MIMEImage(logo_data)
        logo_image.add_header('Content-ID', '<logo>')
        logo_image.add_header('Content-Disposition', 'inline', filename='logo.png')
        msg.attach(logo_image)

    return msg

def _build_password_reset_message(to_email: str, reset_link: str, smtp_email: str) -> MIMEMultipart:
    subject = "BatangAware - Password reset approved"
    text_body = "\n".join([
        "Your password reset request was approved.",
        "",
        "Open this secure link within 30 minutes to set a new password:",
        reset_link,
        "",
        "If you did not request this, please contact your administrator immediately.",
    ])
    html_body = f"""
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

    msg = MIMEMultipart('alternative')
    msg['From'] = smtp_email
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))
    return msg

def _send_smtp_message(to_email: str, msg: MIMEMultipart) -> bool:
    smtp_email = os.getenv('SMTP_EMAIL')
    smtp_password = os.getenv('SMTP_PASSWORD')
    smtp_from = os.getenv('SMTP_FROM', smtp_email)
    smtp_host = os.getenv('SMTP_HOST', 'smtp.resend.com')
    smtp_port = int(os.getenv('SMTP_PORT', '465')) # Defaulting to 465 for better cloud compatibility
    smtp_timeout = float(os.getenv('SMTP_TIMEOUT', '30')) # Increased timeout

    if not smtp_email or not smtp_password:
        print("ERROR: SMTP_EMAIL or SMTP_PASSWORD not set in environment.")
        return False

    try:
        # Port 465 uses SMTP_SSL (Implicit TLS)
        if smtp_port == 465:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                server.login(smtp_email, smtp_password)
                server.sendmail(smtp_from, to_email, msg.as_string())
        # Port 587 or 25 uses STARTTLS (Explicit TLS)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=smtp_timeout) as server:
                server.ehlo()
                if server.has_extn('STARTTLS'):
                    server.starttls()
                    server.ehlo()
                server.login(smtp_email, smtp_password)
                server.sendmail(smtp_from, to_email, msg.as_string())
        return True
    except Exception as e:
        print(
            f"Failed to send email to {to_email} via {smtp_host}:{smtp_port}. "
            f"User: {smtp_email}, From: {smtp_from}. "
            f"Error: {type(e).__name__}: {e}"
        )
        return False

def send_otp_email(to_email: str, otp: str):
    """
    Sends an OTP email using SMTP.
    """
    smtp_email = os.getenv('SMTP_EMAIL')
    smtp_password = os.getenv('SMTP_PASSWORD')
    smtp_from = os.getenv('SMTP_FROM', smtp_email)

    if not smtp_email or not smtp_password:
        print("ERROR: SMTP_EMAIL or SMTP_PASSWORD not set in environment.")
        return False

    msg = _build_otp_message(to_email, otp, smtp_from)
    if _send_smtp_message(to_email, msg):
        print(f"OTP email sent successfully to {to_email}")
        return True
    return False

def send_password_reset_email(to_email: str, reset_link: str):
    smtp_email = os.getenv('SMTP_EMAIL')
    smtp_password = os.getenv('SMTP_PASSWORD')
    smtp_from = os.getenv('SMTP_FROM', smtp_email)
    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = os.getenv('SMTP_PORT', '587')
    
    print(f"[DEBUG] Attempting password reset email to {to_email}")
    print(f"[DEBUG] Host: {smtp_host}:{smtp_port}, From: {smtp_from}, User: {smtp_email}")

    if not smtp_email or not smtp_password:
        print("ERROR: SMTP_EMAIL or SMTP_PASSWORD not set in environment.")
        return False
        
    if smtp_from == smtp_email and smtp_email == 'resend':
        print("WARNING: SMTP_FROM is not set while using Resend. This will likely fail.")

    msg = _build_password_reset_message(to_email, reset_link, smtp_from)
    if _send_smtp_message(to_email, msg):
        print(f"Password reset email sent successfully to {to_email}")
        return True
    return False

def send_password_reset_email_async(to_email: str, reset_link: str):
    """
    Fire-and-forget password reset email.
    """
    def _send():
        send_password_reset_email(to_email, reset_link)

    thread = threading.Thread(target=_send, name=f"reset-email-{to_email}", daemon=True)
    thread.start()
    return True

def send_otp_email_async(to_email: str, otp: str):
    """
    Fire-and-forget OTP sending for local/dev flows where SMTP is slow.
    The OTP is already stored before this is called, so verification can proceed
    as soon as the user receives the email.
    """
    def _send():
        send_otp_email(to_email, otp)

    thread = threading.Thread(target=_send, name=f"otp-email-{to_email}", daemon=True)
    thread.start()
    return True
