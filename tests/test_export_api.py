import importlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class ExportApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.TemporaryDirectory()
        # No MLX/FFmpeg workers are needed to verify queueing and file exports.
        with patch.dict("os.environ", {"TRANSCRIBER_DATA_DIR": cls.data.name}), patch("threading.Thread.start"):
            cls.module = importlib.import_module("app")

    @classmethod
    def tearDownClass(cls):
        cls.data.cleanup()

    def setUp(self):
        self.files = tempfile.TemporaryDirectory()
        root = Path(self.files.name)
        self.uploads, self.outputs = root / "uploads", root / "outputs"
        self.uploads.mkdir()
        self.outputs.mkdir()
        with patch("threading.Thread.start"):
            self.manager = self.module.QueueManager()
        self.manager.service = Mock()
        self.manager.service.transcribe_prepared.return_value = "È una lezione <1> & 2."
        for name, value in (("manager", self.manager), ("UPLOAD_DIR", self.uploads), ("OUTPUT_DIR", self.outputs)):
            p = patch.object(self.module, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.files.cleanup)
        self.client = self.module.app.test_client()

    def create(self, *, merged=False):
        data = {"files": [(io.BytesIO(b"audio"), "Lezione.m4a")],
                "merge_requested": str(merged).lower(), "merge_name": "Lezioni unite.HTML"}
        if merged:
            data["files"].append((io.BytesIO(b"audio"), "Seconda.m4a"))
        return self.client.post("/api/jobs", data=data)

    def finish(self, job):
        self.manager._transcribe(job.id, Path(job.source_path))

    def test_queue_needs_no_output_format(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        job = next(iter(self.manager.jobs.values()))
        self.assertNotIn("output_format", response.json["jobs"][0])
        self.finish(job)
        self.assertEqual(job.status, "completed")
        self.assertTrue(job.output_file.endswith(".txt"))

    def test_all_formats_for_single_and_merged_downloads(self):
        for merged in (False, True):
            response = self.create(merged=merged)
            self.assertEqual(response.status_code, 201)
            jobs = [self.manager.jobs[item["id"]] for item in response.json["jobs"]]
            for job in jobs:
                self.finish(job)
            name = self.manager.group_outputs[jobs[0].group_id] if merged else jobs[0].output_file
            self.assertNotIn(".HTML_", name)
            saved_text = (self.outputs / name).read_text()
            calls = self.manager.service.transcribe_prepared.call_count
            for output_format, mime in (("txt", "text/plain"), ("md", "text/markdown"), ("html", "text/html")):
                with self.subTest(merged=merged, output_format=output_format):
                    download = self.client.get("/download/" + name, query_string={"format": output_format})
                    self.assertEqual(download.status_code, 200)
                    self.assertEqual(download.mimetype, mime)
                    self.assertIn("attachment;", download.headers["Content-Disposition"])
                    self.assertIn("." + output_format, download.headers["Content-Disposition"])
                    text = download.get_data(as_text=True)
                    download.close()
                    if merged:
                        self.assertEqual(text.count("# Part ") if output_format == "md" else text.count("Part "), 2)
                    if output_format == "html":
                        self.assertIn("È una lezione &lt;1&gt; &amp; 2.", text)
                    else:
                        self.assertIn("È una lezione <1> & 2.", text)
                    self.assertEqual(self.manager.service.transcribe_prepared.call_count, calls)
                    self.assertEqual((self.outputs / name).read_text(), saved_text)

    def test_invalid_download_format_is_rejected(self):
        response = self.client.get("/download/lecture.txt", query_string={"format": "../../other"})
        self.assertEqual(response.status_code, 400)

    def test_saved_names_do_not_overwrite_existing_transcripts(self):
        name = self.manager._unique_output_name("Lezione")
        (self.outputs / name).write_text("Existing", encoding="utf-8")
        self.assertEqual(self.manager._unique_output_name("Lezione"), "Lezione_2.txt")
        self.assertEqual((self.outputs / name).read_text(), "Existing")

    def test_exports_survive_queue_removal_and_a_new_manager(self):
        for merged in (False, True):
            response = self.create(merged=merged)
            jobs = [self.manager.jobs[item["id"]] for item in response.json["jobs"]]
            for job in jobs:
                self.finish(job)
            name = self.manager.group_outputs[jobs[0].group_id] if merged else jobs[0].output_file
            self.manager.cancel(jobs[0].id)
            with patch("threading.Thread.start"):
                fresh_manager = self.module.QueueManager()
            with patch.object(self.module, "manager", fresh_manager):
                for output_format in ("txt", "md", "html"):
                    download = self.client.get("/download/" + name, query_string={"format": output_format})
                    self.assertEqual(download.status_code, 200)
                    self.assertIn("È una lezione", download.get_data(as_text=True))
                    download.close()

    def test_missing_sources_and_legacy_txt_downloads(self):
        (self.outputs / "old.txt").write_text("Vecchia trascrizione", encoding="utf-8")
        for url in ("/download/old.txt", "/download/old.txt?format=txt"):
            download = self.client.get(url)
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.get_data(as_text=True), "Vecchia trascrizione")
            download.close()
        self.assertEqual(self.client.get("/download/old.txt?format=md").status_code, 404)
        self.assertEqual(self.client.get("/download/missing.txt?format=html").status_code, 404)

    def test_export_rejects_paths_outside_the_output_folder(self):
        for filename in ("../private.txt", "..%2Fprivate.txt", "folder/private.txt", "folder%5Cprivate.txt"):
            response = self.client.get("/download/" + filename, query_string={"format": "md"})
            self.assertEqual(response.status_code, 404)

    def test_merge_waits_for_success_and_exports_only_once_in_group_order(self):
        response = self.create(merged=True)
        first, second = [self.manager.jobs[item["id"]] for item in response.json["jobs"]]
        first.transcript, second.transcript = "Prima", "Seconda"
        first.status = "completed"
        self.manager._finish_group_if_ready(first.group_id)
        self.assertFalse(self.manager.group_outputs)
        second.status = "failed"
        self.manager._finish_group_if_ready(first.group_id)
        self.assertFalse(self.manager.group_outputs)
        second.status = "completed"
        self.manager.group_order[first.group_id].reverse()
        self.manager._finish_group_if_ready(first.group_id)
        self.manager._finish_group_if_ready(first.group_id)
        files = list(self.outputs.glob("*.txt"))
        self.assertEqual(len(files), 1)
        download = self.client.get("/download/" + files[0].name, query_string={"format": "md"})
        self.assertEqual(download.get_data(as_text=True), "# Part 1\n\nSeconda\n\n# Part 2\n\nPrima\n")
        download.close()


if __name__ == "__main__":
    unittest.main()
