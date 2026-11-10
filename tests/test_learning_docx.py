from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from tools.build_learning_docx import OUTPUT, W, build, render_markdown


class LearningDocumentTests(unittest.TestCase):
    def test_code_examples_keep_indentation_blank_lines_and_xml_characters(self):
        source = '# Lesson\n\n```python\ndef allowed(x):\n    # keep this comment\n\n    return x < 5 and x > 0\n```\n\nExplanation.\n'
        document = ET.fromstring(f'<w:document xmlns:w="{W}">' + ''.join(render_markdown(source)) + '</w:document>')
        ns = {"w": W}
        code = [node for node in document.findall("w:p", ns)
                if node.find("w:pPr/w:pStyle", ns).get(f"{{{W}}}val") == "Code"]
        values = [node.find("w:r/w:t", ns).text or "" for node in code]
        self.assertEqual(values, ["def allowed(x):", "    # keep this comment", "", "    return x < 5 and x > 0"])
        self.assertNotIn("```", ET.tostring(document, encoding="unicode"))

    def test_unclosed_code_example_is_rejected(self):
        with self.assertRaises(ValueError):
            render_markdown("```python\nprint('unfinished')")

    def test_document_has_valid_parts_and_teaching_content(self):
        with ZipFile(OUTPUT) as archive:
            self.assertIsNone(archive.testzip())
            for name in archive.namelist():
                ET.fromstring(archive.read(name))
            document = ET.fromstring(archive.read("word/document.xml"))
            text = " ".join(node.text or "" for node in document.iter(f"{{{W}}}t"))
            for phrase in ("MVC", "Phase 1", "Phase 4", "frontend", "rollback", "Permission", "unittest",
                           "before-and-after", "ReconciliationRequired", "without an assistant", "f4a100000001"):
                self.assertIn(phrase, text)

    def test_generator_escapes_text_and_keeps_guide_current(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            source = path / "sample.md"
            source.write_text("# Lesson\n\nA < B & C > D\n")
            build(source, path / "sample.docx")
            with ZipFile(path / "sample.docx") as archive:
                doc = ET.fromstring(archive.read("word/document.xml"))
                text = " ".join(node.text or "" for node in doc.iter(f"{{{W}}}t"))
                self.assertIn("A < B & C > D", text)
            build(output=path / "current.docx")
            with ZipFile(path / "current.docx") as rebuilt, ZipFile(OUTPUT) as saved:
                self.assertEqual(rebuilt.read("word/document.xml"), saved.read("word/document.xml"))
