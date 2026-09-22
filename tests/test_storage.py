import pytest

from src import storage
from src.config import Settings
from src.errors import FileTooLargeError


def test_create_makes_isolated_job_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "settings", Settings.from_env({"STORAGE_BASE_DIR": str(tmp_path)}))

    job_dir = storage.create("session-1", "job-1")

    assert job_dir == tmp_path / storage._JOBS_DIRNAME / "session-1" / "job-1"
    assert job_dir.is_dir()


def test_cleanup_removes_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "settings", Settings.from_env({"STORAGE_BASE_DIR": str(tmp_path)}))
    job_dir = storage.create("session-1", "job-1")

    storage.cleanup(job_dir)

    assert not job_dir.exists()


def test_cleanup_missing_directory_does_not_raise(tmp_path):
    missing = tmp_path / "does-not-exist"

    storage.cleanup(missing)  # brak wyjątku


def test_enforce_size_limit_passes_under_limit(tmp_path):
    file_path = tmp_path / "small.bin"
    file_path.write_bytes(b"0" * 1024)  # 1 KB

    storage.enforce_size_limit(file_path, max_mb=1)  # brak wyjątku


def test_enforce_size_limit_raises_over_limit(tmp_path):
    file_path = tmp_path / "big.bin"
    file_path.write_bytes(b"0" * (2 * 1024 * 1024))  # 2 MB

    with pytest.raises(FileTooLargeError):
        storage.enforce_size_limit(file_path, max_mb=1)


def test_enforce_size_limit_sums_directory_contents(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"0" * (600 * 1024))
    (tmp_path / "b.bin").write_bytes(b"0" * (600 * 1024))

    with pytest.raises(FileTooLargeError):
        storage.enforce_size_limit(tmp_path, max_mb=1)


def test_enforce_size_limit_uses_settings_default_when_max_mb_not_given(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "settings", Settings.from_env({"MAX_FILE_SIZE_MB": "1"}))
    file_path = tmp_path / "big.bin"
    file_path.write_bytes(b"0" * (2 * 1024 * 1024))  # 2 MB > limit 1 MB

    with pytest.raises(FileTooLargeError):
        storage.enforce_size_limit(file_path)


def test_directory_size_bytes_sums_files_recursively(tmp_path):
    """Publiczne API (Faza 2a: engine.py::submit_playlist używa tego do
    sprawdzania skumulowanego rozmiaru ZIP-a w trakcie pobierania) — ta sama
    logika, którą enforce_size_limit już wykorzystywał wewnętrznie."""
    (tmp_path / "a.bin").write_bytes(b"0" * 1000)
    (tmp_path / "b.bin").write_bytes(b"0" * 2000)

    assert storage.directory_size_bytes(tmp_path) == 3000


def test_directory_size_bytes_of_single_file(tmp_path):
    file_path = tmp_path / "solo.bin"
    file_path.write_bytes(b"0" * 500)

    assert storage.directory_size_bytes(file_path) == 500


def test_purge_all_removes_orphaned_job_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "settings", Settings.from_env({"STORAGE_BASE_DIR": str(tmp_path)}))
    job_dir = storage.create("session-1", "job-1")
    (job_dir / "video.mp4").write_bytes(b"x")

    storage.purge_all()

    assert not job_dir.exists()
    assert not (tmp_path / storage._JOBS_DIRNAME).exists()


def test_purge_all_does_not_touch_unrelated_siblings_in_storage_base_dir(tmp_path, monkeypatch):
    """STORAGE_BASE_DIR bywa dzielonym /tmp — purge_all() sprząta wyłącznie
    własny podkatalog, nigdy zawartości poza nim."""
    monkeypatch.setattr(storage, "settings", Settings.from_env({"STORAGE_BASE_DIR": str(tmp_path)}))
    storage.create("session-1", "job-1")
    unrelated = tmp_path / "some-other-process.tmp"
    unrelated.write_bytes(b"not ours")

    storage.purge_all()

    assert unrelated.exists()


def test_purge_all_missing_directory_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "settings", Settings.from_env({"STORAGE_BASE_DIR": str(tmp_path)}))

    storage.purge_all()  # brak wyjątku, mimo że _JOBS_DIRNAME nigdy nie istniał
