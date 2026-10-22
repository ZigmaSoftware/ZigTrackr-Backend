"""HTML bodies for requester email, in the Professional Blue direction.

Built as table-based inline-styled HTML rather than Django templates because
mail clients ignore <style> blocks and float/flex; the MSO conditional comments
are what keep the 600px shell from collapsing in Outlook. The structure follows
/email-designs/01-professional-blue, which was approved as the house direction.

Every builder returns (text, html). The plain-text half is not a fallback
afterthought -- it is what a text-only client and most spam filters read, so it
carries the same information.
"""

from html import escape

BRAND = "#0a66c2"
INK = "#18354e"
BODY_INK = "#34495e"
MUTED = "#66717e"
LINE = "#e5eaf0"
PANEL_BG = "#f5f8fb"
PANEL_LINE = "#e6ecf2"
PAGE_BG = "#f3f6f9"

FONT = "Arial,Helvetica,sans-serif"
MONO = "Consolas,'Courier New',monospace"


def _shell(*, preheader, eyebrow, heading, content, footer_note=""):
    """The common chrome: masthead, preheader, content slot, footer."""
    note = footer_note or "Sent in response to your support request."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">
<title>{escape(heading)} - Zigma Support</title>
<style>
body, table, td, p, a {{ -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }}
@media screen and (max-width:620px) {{
  .email-shell {{ padding:12px 8px !important; }}
  .email-container {{ width:100% !important; }}
  .pad {{ padding-left:22px !important; padding-right:22px !important; }}
  .otp-code {{ font-size:30px !important; letter-spacing:8px !important; }}
}}
</style>
</head>
<body style="margin:0;padding:0;background-color:{PAGE_BG};font-family:{FONT};color:#303942;">
<div style="display:none;font-size:1px;line-height:1px;color:{PAGE_BG};max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;background-color:{PAGE_BG};font-family:{FONT};">
<tr><td class="email-shell" style="padding:32px 12px;">
<!--[if mso]><table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table class="email-container" role="presentation" width="600" align="center" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:{FONT};width:100%;max-width:600px;">
<tr><td>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:{FONT};background-color:#ffffff;border:1px solid #e0e6ed;border-radius:6px;">
<tr><td class="pad" style="padding:28px 32px;border-bottom:1px solid {LINE};">
<span style="font-size:23px;font-weight:700;color:#182636;">Zigma<span style="font-weight:400;"> Support</span></span>
</td></tr>
<tr><td class="pad" style="padding:32px 32px 6px;">
<p style="margin:0 0 12px;font-size:12px;font-weight:700;line-height:20px;color:{BRAND};letter-spacing:.06em;">{escape(eyebrow)}</p>
<h1 style="margin:0 0 20px;font-size:28px;line-height:36px;font-weight:700;color:{INK};">{escape(heading)}</h1>
</td></tr>
{content}
<tr><td class="pad" style="padding:22px 32px;border-top:1px solid {LINE};background-color:#fafbfd;">
<p style="margin:0;font-size:11px;line-height:18px;text-align:center;color:#707986;">Zigma Support &middot; Ticket notifications<br>{escape(note)}</p>
</td></tr>
</table>
</td></tr>
</table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr>
</table>
</body>
</html>"""


def _paragraph(text, *, color=None, size=15, top=0, bottom=18):
    return (
        f'<p style="margin:{top}px 0 {bottom}px;font-size:{size}px;'
        f'line-height:24px;color:{color or BODY_INK};">{text}</p>'
    )


def _meta_panel(rows):
    """Label/value panel. Monospace on the first row -- it is the reference."""
    cells = []
    for index, (label, value) in enumerate(rows):
        last = index == len(rows) - 1
        pad = "0" if last else "0 0 12px"
        style = (
            f"font-family:{MONO};font-size:14px;font-weight:700;color:{INK};word-break:break-word;"
            if index == 0 else f"font-size:14px;line-height:22px;color:{INK};"
        )
        cells.append(
            f'<tr><td width="90" valign="top" style="padding:0 12px {"0" if last else "12px"} 0;'
            f'font-size:12px;line-height:20px;color:#6c7580;">{escape(label)}</td>'
            f'<td style="padding:{pad};line-height:20px;{style}">{escape(str(value))}</td></tr>'
        )
    return (
        f'<tr><td class="pad" style="padding:22px 24px;background-color:{PANEL_BG};'
        f'border-top:1px solid {PANEL_LINE};border-bottom:1px solid {PANEL_LINE};">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;font-family:{FONT};">{"".join(cells)}</table>'
        f"</td></tr>"
    )


def _button(label, url):
    return (
        f'<tr><td class="pad" style="padding:0 32px 28px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;"><tr>'
        f'<td style="background-color:{BRAND};border-radius:4px;">'
        f'<a href="{escape(url)}" style="display:inline-block;padding:12px 26px;'
        f'font-family:{FONT};font-size:14px;font-weight:700;color:#ffffff;'
        f'text-decoration:none;">{escape(label)}</a></td></tr></table></td></tr>'
    )


def _signoff(extra=""):
    return (
        f'<tr><td class="pad" style="padding:0 32px 32px;">{extra}'
        f'<p style="margin:24px 0 0;font-size:13px;line-height:21px;color:#657080;">'
        f"Kind regards,<br><strong>Zigma Support Team</strong></p></td></tr>"
    )



# Support escalation contacts, shown on the access-code mail. A real
# human route in the message is also a mild trust signal to spam filters,
# which score bare code-only mail harshly.
SUPPORT_CONTACTS = (
    ("L1", "Dimple", "+918567746474"),
    ("L2", "Mouly", "+919564744476"),
    ("L3", "Dinesh", "+919264744579"),
)


def _contacts_block():
    rows = "".join(
        f'<tr>'
        f'<td width="34" valign="top" style="padding:4px 8px 4px 0;font-size:13px;'
        f'line-height:22px;font-weight:700;color:{BRAND};">{escape(tier)}</td>'
        f'<td valign="top" style="padding:4px 8px 4px 0;font-size:13px;'
        f'line-height:22px;color:#414b55;">{escape(name)}</td>'
        f'<td align="right" valign="top" style="padding:4px 0;font-size:13px;'
        f'line-height:22px;"><a href="tel:{escape(phone)}" style="color:{BRAND};'
        f'text-decoration:underline;white-space:nowrap;">{escape(phone)}</a></td>'
        f'</tr>'
        for tier, name, phone in SUPPORT_CONTACTS
    )
    return (
        f'<tr><td class="pad" style="padding:22px 32px;border-top:1px solid {LINE};">'
        f'<p style="margin:0 0 12px;font-size:13px;line-height:20px;font-weight:700;'
        f'color:{BRAND};">For enquiries</p>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'border="0" style="border-collapse:collapse;font-family:{FONT};">{rows}</table>'
        f"</td></tr>"
    )

# ---- OTP -------------------------------------------------------------------
# Modelled on the verification mail people already recognise: the code is the
# only thing that matters, so it is large, monospace and letter-spaced, with
# the validity window and a do-not-share warning immediately beneath it.

OTP_TEXT = """Verify your identity

Here is your Zigma Support access code:

    {code}

This code is valid for {minutes} minutes and can only be used once.

Please don't share this code with anyone. We will never ask you for it by
phone or email.

If you didn't request this code, you can ignore this message -- nothing has
been accessed.

For enquiries:
  L1  Dimple   +918567746474
  L2  Mouly    +919564744476
  L3  Dinesh   +919264744579

Zigma Support
"""


def otp_email(*, code, minutes):
    """The ticket number is deliberately absent: sign-in is by address
    now, the code opens every ticket that address owns, and naming one
    ticket would be both wrong and an unnecessary disclosure to anyone
    who should not have received the mail."""
    digits = "".join(
        f'<span style="display:inline-block;padding:0 6px;">{escape(d)}</span>'
        for d in code
    )
    content = (
        f'<tr><td class="pad" style="padding:0 32px 8px;">'
        + _paragraph(
            "Use the code below to sign in to Zigma Support. It confirms this "
            "email address belongs to you."
        )
        + "</td></tr>"
        f'<tr><td class="pad" style="padding:0 32px 26px;">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;background-color:{PANEL_BG};'
        f'border:1px solid {PANEL_LINE};border-radius:6px;"><tr>'
        f'<td align="center" style="padding:26px 16px;">'
        f'<p style="margin:0 0 10px;font-size:12px;line-height:18px;color:#6c7580;'
        f'letter-spacing:.06em;">ACCESS CODE</p>'
        f'<div class="otp-code" style="font-family:{MONO};font-size:38px;line-height:46px;'
        f'font-weight:700;color:{INK};letter-spacing:10px;">{digits}</div>'
        f'<p style="margin:14px 0 0;font-size:13px;line-height:20px;color:{BODY_INK};">'
        f"Valid for <strong>{minutes} minutes</strong> and usable once.</p>"
        f"</td></tr></table></td></tr>"
        f'<tr><td class="pad" style="padding:0 32px 4px;">'
        f'<div style="border-left:3px solid {BRAND};padding:0 0 0 16px;">'
        + _paragraph(
            "<strong>Please don't share this code with anyone.</strong> "
            "Zigma Support will never ask you for it by phone or email.",
            size=14, bottom=0,
        )
        + "</div></td></tr>"
        + _signoff(
            _paragraph(
                "If you didn't request this code you can ignore this message. "
                "Nothing has been accessed.",
                color=MUTED, size=13, bottom=0,
            )
        )
        + _contacts_block()
    )
    html = _shell(
        preheader=f"Your Zigma Support sign-in code expires in {minutes} minutes.",
        eyebrow="VERIFICATION",
        heading="Verify your identity",
        content=content,
        footer_note="Sent because someone asked to sign in with this address.",
    )
    return OTP_TEXT.format(code=code, minutes=minutes), html


# ---- Existing notification types ------------------------------------------


def ack_email(*, ticket_no, title, status):
    content = (
        f'<tr><td class="pad" style="padding:0 32px 8px;">'
        + _paragraph("Hello,")
        + _paragraph(
            "Thank you for contacting Zigma Support. Your request has been "
            "logged, and our team will review it before assignment."
        )
        + _paragraph(
            "Our team will work to resolve your issue as soon as possible "
            "during business days. We will keep you updated on the progress."
        )
        + "</td></tr>"
        + _meta_panel([("Reference", ticket_no), ("Subject", title), ("Status", status)])
        + _signoff(
            _paragraph(
                f"Keep {escape(ticket_no)} in the subject line when replying so your "
                "message stays with this request. Please send unrelated requests "
                "separately.",
                color=MUTED, size=13, bottom=0,
            )
        )
    )
    return _shell(
        preheader=f"Your request is logged as {ticket_no}. Our support team will review it.",
        eyebrow="RECEIVED",
        heading="We've received your request.",
        content=content,
    )


def assigned_email(*, ticket_no, title, track_url):
    content = (
        f'<tr><td class="pad" style="padding:0 32px 8px;">'
        + _paragraph(
            "Your request has been accepted, and our team is actively working "
            "on it."
        )
        + _paragraph(
            "We will make every effort to resolve your issue as soon as possible "
            "during business days. You can track its progress and contact our "
            "support team at any time."
        )
        + "</td></tr>"
        + _meta_panel([("Ticket", ticket_no), ("Subject", title)])
        + f'<tr><td class="pad" style="padding:26px 32px 20px;">'
        + _paragraph(
            "Open your request with this ticket number and this email address.",
            size=14, bottom=0,
        )
        + "</td></tr>"
        + _button("Track your request", track_url)
        + _signoff()
    )
    return _shell(
        preheader=f"{ticket_no} has been accepted and is now being worked on.",
        eyebrow="ACCEPTED",
        heading="Your request is being worked on.",
        content=content,
    )


def closed_email(*, ticket_no, title, remarks, track_url):
    extra = ""
    if remarks:
        extra = (
            f'<tr><td class="pad" style="padding:0 32px 8px;">'
            f'<div style="border-left:3px solid {BRAND};padding:0 0 0 16px;">'
            f'<p style="margin:0 0 6px;font-size:13px;font-weight:700;color:{BODY_INK};">'
            f"Closing note</p>"
            + _paragraph(escape(remarks), size=14, bottom=0)
            + "</div></td></tr>"
        )
    content = (
        f'<tr><td class="pad" style="padding:0 32px 8px;">'
        + _paragraph("Your request has been completed and closed.")
        + "</td></tr>"
        + _meta_panel([("Ticket", ticket_no), ("Subject", title)])
        + extra
        + f'<tr><td class="pad" style="padding:26px 32px 20px;">'
        + _paragraph(
            "If this isn't resolved, you can reopen it within 48 hours. After "
            "that, please submit a new request.",
            size=14, bottom=0,
        )
        + "</td></tr>"
        + _button("View your request", track_url)
        + _signoff()
    )
    return _shell(
        preheader=f"{ticket_no} has been completed and closed.",
        eyebrow="CLOSED",
        heading="Your request is complete.",
        content=content,
    )


def deleted_email(*, ticket_no, title, reason):
    extra = ""
    if reason:
        extra = (
            f'<tr><td class="pad" style="padding:0 32px 8px;">'
            f'<div style="border-left:3px solid {BRAND};padding:0 0 0 16px;">'
            f'<p style="margin:0 0 6px;font-size:13px;font-weight:700;color:{BODY_INK};">'
            f"Reason</p>"
            + _paragraph(escape(reason), size=14, bottom=0)
            + "</div></td></tr>"
        )
    content = (
        f'<tr><td class="pad" style="padding:0 32px 8px;">'
        + _paragraph("This request has been removed and will not be worked on.")
        + "</td></tr>"
        + _meta_panel([("Reference", ticket_no), ("Subject", title)])
        + extra
        + _signoff(
            _paragraph(
                "If you still need assistance, please submit a new request.",
                color=MUTED, size=13, bottom=0,
            )
        )
    )
    return _shell(
        preheader=f"{ticket_no} has been removed.",
        eyebrow="REMOVED",
        heading="This request has been removed.",
        content=content,
    )
