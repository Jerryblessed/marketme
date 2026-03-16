import imaplib
import smtplib
import logging
import email as email_lib
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM,
                    IMAP_HOST, IMAP_PORT)

log = logging.getLogger("marketme.email")


def smtp_send(to, subject, body_plain, body_html="", reply_to=None):
    """Always uses system SMTP from .env"""
    if not SMTP_HOST or not SMTP_USER:
        log.warning("SMTP not configured in .env")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = SMTP_FROM or SMTP_USER
        msg["To"]      = to
        msg["Subject"] = subject
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.attach(MIMEText(body_plain, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_USER, to, msg.as_string())
        log.info(f"Email sent → {to} | {subject}")
        return True
    except Exception as e:
        log.error(f"SMTP error: {e}")
        return False


def smtp_send_many(to_list, subject, body_plain, body_html=""):
    """Batch send — returns count of successfully sent emails"""
    sent = 0
    for to in to_list:
        if smtp_send(to, subject, body_plain, body_html):
            sent += 1
    return sent


def fetch_unseen_emails():
    """Fetch unseen emails from the system IMAP account (.env credentials)"""
    if not SMTP_USER:
        return []
    msgs = []
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(SMTP_USER, SMTP_PASS)
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        nums = data[0].split()
        log.info(f"IMAP: {len(nums)} unseen emails")
        for num in nums[-30:]:
            _, raw = mail.fetch(num, "(RFC822)")
            if not raw or not raw[0]:
                continue
            msg = email_lib.message_from_bytes(raw[0][1])
            sp = decode_header(msg.get("Subject", ""))[0]
            subject = (
                sp[0].decode(sp[1] or "utf-8")
                if isinstance(sp[0], bytes)
                else str(sp[0])
            )
            from_hdr = msg.get("From", "")
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode(
                            "utf-8", errors="replace"
                        )
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            msgs.append({
                "message_id":  msg.get("Message-ID", ""),
                "in_reply_to": msg.get("In-Reply-To", ""),
                "subject":     subject,
                "from":        from_hdr,
                "body":        body[:800],
            })
        mail.logout()
    except Exception as e:
        log.error(f"IMAP: {e}")
    return msgs
