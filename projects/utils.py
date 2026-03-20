import io
import os
import struct
import time
import zipfile


class ZipStreamer:
    """
    Stream a zip archive without building it entirely in memory.

    Uses zipfile to produce chunks that can be yielded from a
    Django StreamingHttpResponse.

    Usage::

        streamer = ZipStreamer()
        for chunk in streamer.stream(files):
            yield chunk

    Where *files* is an iterable of (arcname, file_path) tuples.
    """

    CHUNK_SIZE = 64 * 1024  # 64 KB read chunks

    def stream(self, files):
        """
        Yield bytes chunks that together form a valid zip archive.

        Parameters
        ----------
        files : iterable of (arcname, file_path)
            Each entry is the name inside the archive and the absolute
            path on disk to the file to include.
        """
        buffer = _StreamingBuffer()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as zf:
            for arcname, file_path in files:
                if not os.path.isfile(file_path):
                    continue
                # Write a local file header
                zinfo = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
                zinfo.compress_type = zipfile.ZIP_STORED
                zinfo.file_size = os.path.getsize(file_path)
                with zf.open(zinfo, "w") as dest:
                    with open(file_path, "rb") as src:
                        while True:
                            chunk = src.read(self.CHUNK_SIZE)
                            if not chunk:
                                break
                            dest.write(chunk)
                            # Flush whatever has accumulated in the buffer
                            data = buffer.pop()
                            if data:
                                yield data
            # After closing the ZipFile context the central directory is written
        # Final flush
        data = buffer.pop()
        if data:
            yield data


class _StreamingBuffer(io.RawIOBase):
    """
    A minimal writable buffer that accumulates bytes and lets the
    caller pop them out incrementally.
    """

    def __init__(self):
        self._buffer = bytearray()

    def writable(self):
        return True

    def write(self, b):
        self._buffer.extend(b)
        return len(b)

    def pop(self):
        """Return accumulated bytes and clear the internal buffer."""
        data = bytes(self._buffer)
        self._buffer.clear()
        return data

    def tell(self):
        return len(self._buffer)
