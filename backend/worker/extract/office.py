"""DOCX/DOC/ODT/RTF/PPTX/PPT/ODP → PDF with headless LibreOffice, so citations get real page/slide numbers."""
import os
import shutil
import subprocess
import tempfile
import threading

from worker.extract import ExtractionError

CONVERT_TIMEOUT = 300
_profile_local = threading.local()


def _profile_dir() -> str:
    # One LibreOffice profile per worker thread: parallel conversions can't share a profile.
    if not hasattr(_profile_local, "path"):
        _profile_local.path = tempfile.mkdtemp(prefix="lo_profile_")
    return _profile_local.path


def convert_to_pdf(src_path: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo_out_") as tmp:
        # Copy first: the source mount is read-only and LibreOffice may want a lock file next to the input.
        local = os.path.join(tmp, "source" + os.path.splitext(src_path)[1].lower())
        shutil.copyfile(src_path, local)
        cmd = ["soffice", f"-env:UserInstallation=file://{_profile_dir()}", "--headless", "--norestore",
               "--nolockcheck", "--convert-to", "pdf", "--outdir", tmp, local]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CONVERT_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise ExtractionError("LibreOffice conversion timed out.") from e
        produced = os.path.join(tmp, "source.pdf")
        if proc.returncode != 0 or not os.path.exists(produced):
            raise ExtractionError(f"LibreOffice could not convert the file: {(proc.stderr or proc.stdout)[-400:]}")
        dest = os.path.join(out_dir, "converted.pdf")
        shutil.move(produced, dest)
        return dest
