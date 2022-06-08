"""
Sends an email with a summary report (WIP)

For now, it just sends stdin.
In the future, it will generate a pretty email and send that.

Requires that an SMTP server is configured through environment variables.

Example usage:

  $ env OUTPUT_HTML=true python3 examples/working_hours.py | python3 examples/email_report.py
"""

import smtplib
import os
import sys
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


@dataclass
class Recipient:
    name: str
    email: str


def create_msg(
    sender: Recipient,
    receiver: Recipient,
    subject: str,
    text: str,
    html=None,
) -> MIMEMultipart:
    """Based on https://stackoverflow.com/a/882770/965332"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender.email
    msg["To"] = receiver.email

    # Record the MIME types of both parts - text/plain and text/html.
    # Also attach parts into message container.
    # According to RFC 2046, the last part of a multipart message, in this case
    # the HTML message, is best and preferred.
    msg.attach(MIMEText(text, "plain"))
    if html:
        msg.attach(MIMEText(html, "html"))

    return msg


def main(read_stdin=True) -> None:
    smtp_server = os.environ["SMTP_SERVER"].strip()
    smtp_username = os.environ["SMTP_USERNAME"].strip()
    smtp_password = os.environ["SMTP_PASSWORD"].strip()

    assert smtp_server, "Environment variable SMTP_SERVER not set"
    assert smtp_username, "Environment variable SMTP_USERNAME not set"
    assert smtp_password, "Environment variable SMTP_PASSWORD not set"

    sender = Recipient("ActivityWatch (automated script)", "noreply@activitywatch.net")
    receiver = Recipient("Erik Bjäreholt", "erik.bjareholt@gmail.com")

    if read_stdin:
        # Accepts input from stdin
        text = sys.stdin.read()
    else:
        text = "Just a test. ActivityWatch stats will go here."

    text = text.replace("\n\n", "<hr>")
    # text = text.replace("\n\n", "<br>")
    # Create the body of the message (a plain-text and an HTML version).
    html = f"""\
    <html>
      <head></head>
      <body>
        <p>{text}</p>
      </body>
    </html>
    """

    subject = "Example report from aw-client"
    msg = create_msg(sender, receiver, subject, text, html)

    try:
        with smtplib.SMTP_SSL(smtp_server, 465) as smtp:
            smtp.login(smtp_username, smtp_password)
            smtp.send_message(msg)
            smtp.quit()
            print("Successfully sent email")
    except smtplib.SMTPException as e:
        print("Error: unable to send email")
        print(e)


if __name__ == "__main__":
    main()
