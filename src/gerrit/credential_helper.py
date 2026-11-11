"""SecretSpec child helper: send selected values only through inherited FD."""
import json
import os
import stat


def main():
    names = json.loads(os.environ["GERRIT_SECRET_NAMES"])
    fd = int(os.environ["GERRIT_SECRET_FD"])
    values = {name: os.environ[name] for name in names if name in os.environ}
    files = json.loads(os.environ.get("GERRIT_SECRET_FILES", "{}"))
    for name, path in files.items():
        fd_in = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            st = os.fstat(fd_in)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
                raise SystemExit(2)
            with os.fdopen(fd_in, "rb", closefd=False) as source:
                value = source.read(256 * 1024 + 1)
            if not value or len(value) > 256 * 1024:
                raise SystemExit(2)
            values[name] = value.decode("utf-8").strip() if name == os.environ["GERRIT_TOKEN_NAME"] else value.decode("utf-8")
        finally:
            os.close(fd_in)
    payload = json.dumps(values, separators=(",", ":")).encode("utf-8")
    if len(payload) > 256 * 1024:
        raise SystemExit(2)
    view = memoryview(payload)
    while view:
        view = view[os.write(fd, view):]


if __name__ == "__main__":
    main()
