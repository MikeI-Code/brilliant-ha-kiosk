import threading
import time
from pathlib import Path
from types import SimpleNamespace
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "kiosk"))
from slider_output import LiveOutput
from slider_worker import Worker, MockLoads
from wired_loads import WiredLoads

ROOT = Path(__file__).resolve().parent


class RecoveryTests(unittest.TestCase):
    def frame(self, worker, now, *pairs):
        for code,value in pairs:
            worker.feed(now, 3, code, value)
        worker.feed(now, 0, 0, 0)

    def test_recorded_and_dropped_stream(self):
        loads = MockLoads([300]*3)
        w = Worker(loads, snapshot=lambda:(1,{1:40,2:41}))
        self.frame(w,0,(47,3),(57,39),(54,400))
        w.feed(.1,0,3,0)
        self.frame(w,.2,(57,-1))  # discarded through SYN_REPORT
        self.frame(w,.3,(47,1),(57,40),(54,700))
        self.frame(w,.4,(57,-1))
        self.frame(w,.5,(47,7),(57,998),(54,900))  # unused hardware slot
        self.frame(w,.6,(47,2),(57,42),(54,335))  # replaces inherited id 41
        self.frame(w,.7,(57,-1))
        self.assertEqual(loads.history,[(2,'toggle',0)])

    def test_slide_then_tap_each_strip(self):
        loads = MockLoads([500]*3)
        worker = Worker(loads)
        for slot in (1, 2, 3):
            self.frame(worker, 1, (47,slot), (57,10), (54,300))
            self.frame(worker, 1.1, (54,500))
            self.frame(worker, 1.2, (57,-1))
            self.frame(worker, 2, (57,11), (54,400))
            self.frame(worker, 2.1, (57,-1))
            self.assertEqual(loads.history[-2:], [(slot,'level',800),(slot,'toggle',0)])

    def test_stalled_output_reader_and_drop(self):
        entered=threading.Event()
        unblock=threading.Event()
        class Slow(MockLoads):
            def set_level(self, slot, value):
                entered.set()
                unblock.wait(2)
                super().set_level(slot,value)
        loads=Slow([500]*3)
        sink=LiveOutput(loads)
        try:
            sink.submit((1,1,70))
            self.assertTrue(entered.wait(1))
            for delta in range(71,180): sink.submit((1,1,delta))
            self.assertEqual(len(sink.pending),1)
            sink.cancel()
            sink.submit((3,2,None))
            unblock.set()
            deadline=time.monotonic()+1
            while len(loads.history)<2 and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(loads.history,[(1,'level',570),(3,'toggle',0)])
        finally:
            unblock.set()
            sink.close()

    def test_stale_level_rereads_toggle_never_retried(self):
        class Bus:
            device_id='test'
            def __init__(self): self.reads=0; self.calls=[]
            def peripheral(self,name):
                self.reads+=1
                values={'display_name':'First','dimmable':'1','on':'1','intensity':'200','max_intensity_value':'1000'}
                return SimpleNamespace(variables={k:SimpleNamespace(value=v,timestamp=self.reads,externally_settable=True) for k,v in values.items()})
            def call(self,method,**kwargs):
                self.calls.append(kwargs['last_set_timestamps'])
                if len(self.calls)==1: raise RuntimeError("Check-and-set timestamp stale for 'intensity' (code 2)")
        bus=Bus(); WiredLoads(bus, ['First','Second','Third']).set_level(1,450)
        self.assertEqual(bus.calls,[{'intensity':1},{'intensity':2}])
        bus=Bus()
        with self.assertRaises(RuntimeError):WiredLoads(bus, ['First','Second','Third']).toggle(1)
        self.assertEqual(len(bus.calls),1)


if __name__=='__main__':
    unittest.main()
