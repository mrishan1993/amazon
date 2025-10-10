# emailer.py
import yaml
import smtplib
from email.message import EmailMessage
from typing import List

def load_email_config(path: str = "config/email.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)

def send_email(subject: str, body: str, attachments: List[str], config_path: str = "config/email.yaml"):
    cfg = load_email_config(config_path)["email"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg.set_content(body)
    for p in attachments:
        with open(p, "rb") as f:
            data = f.read()
        maintype = "application"
        subtype = "octet-stream"
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=p.split("/")[-1])
    # send
    server = cfg["smtp_server"]
    port = cfg["smtp_port"]
    passwd = cfg["password"]
    with smtplib.SMTP(server, port) as s:
        s.starttls()
        s.login(cfg["from"], passwd)
        s.send_message(msg)
    print("[emailer] Sent email with attachments:", attachments)
