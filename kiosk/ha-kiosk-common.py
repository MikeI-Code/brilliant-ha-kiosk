"""Shared by the kiosk's Python services (installed flat next to them in /etc/ha-kiosk/).

Two small things every reader and publisher needs to agree on:

* ``parse_env``: how kiosk.env / overrides.env are read. The rules of parse_env in ha-kiosk-setup,
  which writes kiosk.env as ``KEY="value"`` (the setup tool also checks key names and a few value
  formats; this does not).
* ``DiscoveryTopics``: the record of the retained Home Assistant MQTT discovery configs this panel
  published (``/var/lib/ha-kiosk/mqtt-discovery-topics``, one topic per line), so ``ha-kiosk-setup``
  can withdraw every one of them on disable, uninstall and purge.

Python 3.10 compatible (the panel's Python). Load it by path, like the other kiosk modules:
``importlib.util.spec_from_file_location("ha_kiosk_common", <dir of this script>/ha-kiosk-common.py)``.
"""
import os
import re
import tempfile
import threading

try:
    import fcntl
except ImportError:                       # not on the panel; only a dev machine without flock
    fcntl = None

# The value rule is the one of BAD_VALUE in ha-kiosk-setup (which also rejects a single quote).
BAD_VALUE = re.compile(r"[\"'`$\\\x00-\x1f\x7f]")
MAX_VALUE_LEN = 1024


def parse_env(text):
    """kiosk.env / overrides.env text -> ({KEY: value}, [keys of rejected lines]).

    A line is ``KEY=value`` (no spaces around the ``=``), one pair of matching quotes around the
    value is removed, and ``#`` is an ordinary character inside a value (it only starts a comment
    at the beginning of a line, as in the shell that sources these files). A value the setup tool
    would reject (quotes, backtick, ``$``, backslash, control characters, over 1024 characters)
    rejects its line; the last good assignment of a key wins. Lines that are not ``KEY=value``
    (including ``export KEY=value``) are skipped. Unlike the tool this does not check key names.
    """
    out, rejected = {}, []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*)", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if BAD_VALUE.search(val) or len(val) > MAX_VALUE_LEN:
            rejected.append(key)
            continue
        out[key] = val
    return out, rejected


def read_env(path):
    """The settings in one file ({} when it is missing or unreadable)."""
    try:
        with open(path, encoding="utf-8") as f:
            return parse_env(f.read())[0]
    except (OSError, UnicodeDecodeError):
        return {}


KIOSK_ENV = "/etc/ha-kiosk/kiosk.env"
OVERRIDES_ENV = "/var/lib/ha-kiosk/overrides.env"


def read_settings(paths=None):
    """kiosk.env, then overrides.env on top (the last assignment wins)."""
    out = {}
    for path in paths or (KIOSK_ENV, OVERRIDES_ENV):
        out.update(read_env(path))
    return out


# ---------- retained discovery configs ----------
DISCOVERY_TOPICS = "/var/lib/ha-kiosk/mqtt-discovery-topics"
_TOPIC = re.compile(r"[^\s#+\x00-\x1f\x7f]+/config")


def valid_topic(topic):
    """A discovery config topic: <prefix>/<component>/<node>/<key>/config, no wildcards or blanks."""
    return bool(_TOPIC.fullmatch(topic)) and topic.count("/") >= 4 and "//" not in topic


def read_topics(path=None):
    """The recorded topics (a set; invalid lines are ignored)."""
    try:
        with open(path or DISCOVERY_TOPICS, encoding="utf-8") as f:
            return {t for t in (ln.strip() for ln in f) if valid_topic(t)}
    except (OSError, UnicodeDecodeError):
        return set()


class DiscoveryTopics:
    """Records the discovery config topics one process publishes into the shared state file.

    Several services (bridge/mqtt, voice, intercom, capture) record into the same file, so every
    change is a read-merge-write under a lock, and the file is replaced atomically. ``add`` is
    cheap after the first time a topic is seen (it only touches the file for a new topic).
    """

    def __init__(self, path=None):
        self.path = path or DISCOVERY_TOPICS
        self.seen = set()
        self._lock = threading.Lock()

    def _update(self, add=(), drop=()):
        d = os.path.dirname(self.path)
        os.makedirs(d, exist_ok=True)
        lock_fd = os.open(self.path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if fcntl:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            topics = (read_topics(self.path) | set(add)) - set(drop)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".mqtt-discovery-topics.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write("".join(t + "\n" for t in sorted(topics)))
                    f.flush()
                    os.fsync(f.fileno())
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        finally:
            os.close(lock_fd)             # also releases the flock

    def add(self, topic):
        """Record ``topic`` (call it before publishing, so a crash cannot lose it). Never raises."""
        if topic in self.seen or not valid_topic(topic):
            return
        with self._lock:
            try:
                self._update(add=[topic])
                self.seen.add(topic)
            except OSError as e:
                print(f"discovery topics: cannot record {topic}: {e}", flush=True)

    def discard(self, topic):
        """Forget ``topic`` (it is withdrawn here, or owned by another integration now)."""
        with self._lock:
            self.seen.discard(topic)
            if topic in read_topics(self.path):
                try:
                    self._update(drop=[topic])
                except OSError as e:
                    print(f"discovery topics: cannot forget {topic}: {e}", flush=True)
