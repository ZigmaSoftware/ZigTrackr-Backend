"""HTML sanitisation of untrusted email (spec 10.1, 45).

These are security tests. Each one describes an attack that reaches an
authenticated Admin session if the sanitiser regresses.
"""

from django.test import SimpleTestCase

from apps.mail_intake.services.sanitize import sanitize_email_html


class ScriptExecutionTests(SimpleTestCase):
    def test_script_tag_and_contents_removed(self):
        html, _ = sanitize_email_html("<script>alert(1)</script><p>hi</p>")
        self.assertNotIn("script", html.lower())
        self.assertNotIn("alert", html)
        self.assertIn("hi", html)

    def test_event_handler_attributes_removed(self):
        html, _ = sanitize_email_html('<img src="x" onerror="alert(1)">')
        self.assertNotIn("onerror", html.lower())
        self.assertNotIn("alert", html)

    def test_onclick_removed_from_any_tag(self):
        html, _ = sanitize_email_html('<p onclick="steal()">text</p>')
        self.assertNotIn("onclick", html.lower())

    def test_javascript_url_removed(self):
        html, _ = sanitize_email_html('<a href="javascript:alert(1)">click</a>')
        self.assertNotIn("javascript:", html.lower())

    def test_svg_is_not_allowed(self):
        """SVG can carry script; it is absent from the allow-list on purpose."""
        html, _ = sanitize_email_html('<svg><script>alert(1)</script></svg>')
        self.assertNotIn("svg", html.lower())
        self.assertNotIn("alert", html)


class EmbeddedContentTests(SimpleTestCase):
    def test_iframe_removed(self):
        html, _ = sanitize_email_html('<iframe src="https://evil.com"></iframe>')
        self.assertNotIn("iframe", html.lower())

    def test_form_and_inputs_removed(self):
        """A credential-harvesting form must never render."""
        html, _ = sanitize_email_html(
            '<form action="https://evil.com"><input name="password"></form>'
        )
        self.assertNotIn("form", html.lower())
        self.assertNotIn("input", html.lower())

    def test_object_and_embed_removed(self):
        html, _ = sanitize_email_html('<object data="x.swf"></object><embed src="y">')
        self.assertNotIn("object", html.lower())
        self.assertNotIn("embed", html.lower())

    def test_style_attribute_removed(self):
        """Inline CSS can position an overlay over the surrounding UI."""
        html, _ = sanitize_email_html('<p style="position:fixed;top:0">overlay</p>')
        self.assertNotIn("style", html.lower())

    def test_style_element_removed(self):
        html, _ = sanitize_email_html("<style>body{display:none}</style><p>x</p>")
        self.assertNotIn("display:none", html)


class RemoteImageTests(SimpleTestCase):
    def test_remote_image_is_blocked_and_counted(self):
        """A remote img is a tracking pixel; it must not load on render."""
        html, blocked = sanitize_email_html('<img src="https://tracker.com/p.gif">')
        self.assertEqual(blocked, 1)
        # The url survives only in an inert data attribute; no live src remains,
        # so the browser issues no request. Matching on `src=` alone would also
        # match data-blocked-src, hence the boundary.
        self.assertNotRegex(html, r'(?<![-\w])src\s*=')
        self.assertIn("data-blocked-src", html)

    def test_multiple_remote_images_counted(self):
        _, blocked = sanitize_email_html(
            '<img src="http://a.com/1.gif"><img src="https://b.com/2.gif">'
        )
        self.assertEqual(blocked, 2)

    def test_inline_cid_image_is_preserved(self):
        """cid: resolves through the permission-gated attachment view, not the network."""
        html, blocked = sanitize_email_html('<img src="cid:att1" alt="shot">')
        self.assertIn('src="cid:att1"', html)
        self.assertEqual(blocked, 0)

    def test_dangerous_schemes_are_defanged_not_kept(self):
        for scheme in ("javascript:alert(1)", "data:text/html,<script>x</script>"):
            html, _ = sanitize_email_html(f'<img src="{scheme}">')
            self.assertNotRegex(html, r'\ssrc\s*=')


class BenignContentTests(SimpleTestCase):
    def test_ordinary_formatting_survives(self):
        html, _ = sanitize_email_html(
            "<p>Hello <b>world</b> and <em>others</em></p><ul><li>one</li></ul>"
        )
        for fragment in ("<p>", "<b>", "<em>", "<li>"):
            self.assertIn(fragment, html)

    def test_tables_survive(self):
        html, _ = sanitize_email_html("<table><tr><td>cell</td></tr></table>")
        self.assertIn("cell", html)

    def test_links_get_safe_rel(self):
        html, _ = sanitize_email_html('<a href="https://example.com">link</a>')
        self.assertIn("noopener", html)

    def test_mailto_links_allowed(self):
        html, _ = sanitize_email_html('<a href="mailto:a@b.com">mail</a>')
        self.assertIn("mailto:a@b.com", html)

    def test_empty_input_is_empty_output(self):
        self.assertEqual(sanitize_email_html(""), ("", 0))
        self.assertEqual(sanitize_email_html(None), ("", 0))

    def test_malformed_html_does_not_raise(self):
        html, _ = sanitize_email_html("<p>unclosed <b>bold <div>")
        self.assertIsInstance(html, str)
