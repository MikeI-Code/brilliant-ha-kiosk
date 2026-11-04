#!/usr/bin/env python3
"""Inject synthetic touches into the panel's touchscreen, to test gestures without touching it.
Run on the panel:  python3 inject_touch.py swipe2down | swipe3down | swipe3up | tap2 | tap3 | tap1 X Y
Writing to /dev/input/event0 delivers the events to every reader, exactly like real fingers.
"""
import struct, sys, time
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SLOT, TRK, MX, MY, BTN_TOUCH, ABS_X, ABS_Y = 0x2f, 0x39, 0x35, 0x36, 330, 0, 1
f = open("/dev/input/event0", "wb", buffering=0)
def ev(t, c, v): f.write(struct.pack("llHHi", 0, 0, t, c, v))
def syn(): ev(EV_SYN, 0, 0)
def gesture(paths, steps=12, dt=0.025, hold=0.0):
    for i, (x0, y0, x1, y1) in enumerate(paths):
        ev(EV_ABS, SLOT, i); ev(EV_ABS, TRK, 100 + i); ev(EV_ABS, MX, x0); ev(EV_ABS, MY, y0)
    ev(EV_KEY, BTN_TOUCH, 1); ev(EV_ABS, ABS_X, paths[0][0]); ev(EV_ABS, ABS_Y, paths[0][1]); syn(); time.sleep(0.04)
    for s in range(1, steps + 1):
        for i, (x0, y0, x1, y1) in enumerate(paths):
            ev(EV_ABS, SLOT, i); ev(EV_ABS, MX, x0 + (x1 - x0) * s // steps); ev(EV_ABS, MY, y0 + (y1 - y0) * s // steps)
        syn(); time.sleep(dt)
    time.sleep(hold)
    for i in range(len(paths)):
        ev(EV_ABS, SLOT, i); ev(EV_ABS, TRK, -1)
    ev(EV_KEY, BTN_TOUCH, 0); syn()
kind = sys.argv[1]
if kind == "swipe2down":
    gesture([(300, 300, 300, 700), (420, 300, 420, 700)], steps=20)
elif kind == "tap2":
    gesture([(330, 56, 330, 56), (490, 56, 490, 56)], steps=2, dt=0.03)
elif kind == "tap3":
    gesture([(250, 600, 250, 600), (360, 600, 360, 600), (470, 600, 470, 600)], steps=2, dt=0.03)
elif kind == "swipe3down":
    gesture([(250, 400, 250, 540), (360, 400, 360, 540), (470, 400, 470, 540)], steps=14)
elif kind == "swipe3up":
    gesture([(250, 540, 250, 400), (360, 540, 360, 400), (470, 540, 470, 400)], steps=14)
elif kind == "swipe2hold":     # two fingers move up and stay down for 4 s (for screenshots)
    gesture([(300, 700, 300, 520), (420, 700, 420, 520)], steps=14, hold=4)
elif kind == "tap1":
    x, y = int(sys.argv[2]), int(sys.argv[3])
    gesture([(x, y, x, y)], steps=2, dt=0.03)
