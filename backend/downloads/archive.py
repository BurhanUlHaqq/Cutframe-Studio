"""Stream playlist files as ZIP64 without keeping a second copy on disk."""

from contextlib import ExitStack
from zipfile import ZIP_STORED, ZipFile


class ZipBuffer:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)
        return len(data)

    def flush(self):
        pass

    def drain(self):
        chunks, self.chunks = self.chunks, []
        yield from filter(None, chunks)


def archive_chunks(items):
    sink = ZipBuffer()
    with ExitStack() as resources:
        sources = [(name, resources.enter_context(path.open("rb"))) for name, path in items]
        with ZipFile(sink, "w", compression=ZIP_STORED, allowZip64=True) as archive:
            for name, source in sources:
                with archive.open(name, "w", force_zip64=True) as member:
                    while block := source.read(256 * 1024):
                        member.write(block)
                        yield from sink.drain()
                yield from sink.drain()
        yield from sink.drain()
