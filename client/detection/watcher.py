from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler

from client.detection.scanner import should_ignore
from client.detection.tracker import ChangeTracker


class SyncEventHandler(FileSystemEventHandler):
    def __init__(self, root: Path, tracker: ChangeTracker) -> None:
        super().__init__()
        self.root = root.resolve()
        self.tracker = tracker

    def _relative_path(self, path: str) -> str | None:
        candidate = Path(path).absolute()

        try:
            relative = candidate.relative_to(self.root)
        except ValueError:
            return None

        if should_ignore(relative):
            return None

        return relative.as_posix()

    def _track(self, path: str) -> None:
        relative = self._relative_path(path)
        if relative is not None:
            self.tracker.mark_changed(relative)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            self.tracker.request_full_rescan()
        else:
            self._track(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._track(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            self.tracker.request_full_rescan()
        else:
            self._track(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            self.tracker.request_full_rescan()
            return

        source = self._relative_path(event.src_path)
        destination = self._relative_path(event.dest_path)

        if source is not None and destination is not None:
            self.tracker.mark_moved(source, destination)
        else:
            if source is not None:
                self.tracker.mark_changed(source)
            if destination is not None:
                self.tracker.mark_changed(destination)


class FileWatcher:
    def __init__(self, root: Path, tracker: ChangeTracker) -> None:
        from watchdog.observers import Observer

        self._observer = Observer()
        self._observer.schedule(
            SyncEventHandler(root, tracker),
            str(root.resolve()),
            recursive=True,
        )

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        if self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
