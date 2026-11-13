"""Serialize local load I/O while the input reader continues draining events."""
from collections import deque
from threading import Condition, Thread
from time import monotonic


class GestureOutput:
    def __init__(self, loads):
        self.loads = loads
        self.anchors = {}

    def apply(self, command):
        slot, contact_id, delta = command
        if delta is None:
            self.loads.toggle(slot)
            self.anchors.pop(slot, None)
            return
        anchor = self.anchors.get(slot)
        if anchor is None or anchor[0] != contact_id:
            start, maximum = self.loads.level(slot)
            self.anchors[slot] = (contact_id, start, maximum, None)
        _, start, maximum, last = self.anchors[slot]
        value = int(round(start + delta*maximum/1000.0))
        value = max(max(1, maximum//100), min(maximum, value))
        if value != last:
            self.loads.set_level(slot, value)
            self.anchors[slot] = (contact_id, start, maximum, value)


class LiveOutput:
    def __init__(self, loads, report=print):
        self.output = GestureOutput(loads)
        self.report = report
        self.pending = deque()
        self.cv = Condition()
        self.closed = False
        self.generation = 0
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, command):
        with self.cv:
            if self.closed:
                return
            if self.pending and command[2] is not None and self.pending[-1][2][:2] == command[:2]:
                self.pending.pop()
            if len(self.pending) >= 16:
                self.pending.clear()
                self.generation += 1
                self.report('Overloaded output: dropped backlog')
                return
            self.pending.append((self.generation, monotonic(), command))
            self.cv.notify()

    def cancel(self):
        with self.cv:
            self.generation += 1
            self.pending.clear()

    def close(self):
        with self.cv:
            self.closed = True
            self.pending.clear()
            self.cv.notify()
        self.thread.join(timeout=2)

    def _run(self):
        previous = -1
        while True:
            with self.cv:
                while not self.pending and not self.closed:
                    self.cv.wait()
                if self.closed:
                    return
                generation, queued, command = self.pending.popleft()
            if generation != previous:
                self.output.anchors.clear()
                previous = generation
            if monotonic()-queued > 0.5:
                self.report('Skipped expired slider command')
                continue
            try:
                self.output.apply(command)
            except RuntimeError as err:
                # A failed toggle is deliberately never resent. Subsequent touches remain usable.
                self.report('Local load request rejected: '+str(err))
            except (OSError, ValueError, KeyError) as err:
                self.report('Local load unavailable: '+str(err))
                self.cancel()
