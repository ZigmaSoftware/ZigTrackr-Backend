from html.parser import HTMLParser

from django.test import SimpleTestCase

from apps.tickets.services.mail_templates import SUPPORT_CONTACTS, otp_email


class ContactRowParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.row = 0
        self.contact_rows = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row += 1
        if tag == "td" and attrs.get("class") == "enquiry-contact":
            self.contact_rows.append(self.row)
        if tag == "a" and attrs.get("href", "").startswith("tel:"):
            self.links.append(attrs["href"])


class OtpMailContactTests(SimpleTestCase):
    def test_contacts_share_one_row_and_keep_phone_links(self):
        text, html = otp_email(code="123456", minutes=10)
        parser = ContactRowParser()
        parser.feed(html)
        self.assertEqual(len(parser.contact_rows), 3)
        self.assertEqual(len(set(parser.contact_rows)), 1)
        self.assertEqual(parser.links, [f"tel:{phone}" for _, _, phone in SUPPORT_CONTACTS])
        self.assertIn("L1 Dimple +918567746474 | L2 Mouly +919564744476 | L3 Dinesh +919264744579", text)
        self.assertIn("max-width:480px", html)
        self.assertIn("This code is valid for 10 minutes", text)
