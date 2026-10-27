import io
import unittest
import zipfile

from cogs.media import _parse_bounded_xml, _read_zip_entry_limited


class AttachmentArchiveSecurityTests(unittest.TestCase):
    def make_archive(self, name, data):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, data)
        stream.seek(0)
        return zipfile.ZipFile(stream, "r")

    def test_small_zip_member_is_read(self):
        with self.make_archive("doc.xml", b"<root>safe</root>") as archive:
            self.assertEqual(
                _read_zip_entry_limited(archive, "doc.xml", max_bytes=100),
                b"<root>safe</root>",
            )

    def test_oversized_decompressed_member_is_rejected(self):
        with self.make_archive("doc.xml", b"A" * 10000) as archive:
            self.assertIsNone(_read_zip_entry_limited(archive, "doc.xml", max_bytes=100))

    def test_xml_with_dtd_or_entities_is_rejected(self):
        payload = b'<!DOCTYPE root [<!ENTITY x "expanded">]><root>&x;</root>'
        with self.assertRaisesRegex(ValueError, "DTD/entities"):
            _parse_bounded_xml(payload)

    def test_regular_xml_parses(self):
        root = _parse_bounded_xml(b"<root><item>ok</item></root>")
        self.assertEqual(root.findtext("item"), "ok")


if __name__ == "__main__":
    unittest.main()
