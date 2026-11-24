# Get root / SSH on a Brilliant panel via UART

A practical guide for owners of Brilliant panels after the company shut down, when
the app's **"Root SSH Login"** toggle is greyed out. Enabling SSH is a local change
on the panel — you just need a way in. A serial (UART) cable gives you one.

> **Only do this on a panel you own, or with the owner's permission.**

---

## The idea in one line

Get a root shell over the serial console → turn SSH on → set a password →
SSH in over your network. If the serial route is blocked, there's a USB fallback
(Appendix A).

---

## What you need

**Hardware**
- A **3.3 V USB-to-TTL serial adapter** (FT232, CP2102, or CH340 chip). Must be
  **3.3 V** — a 5 V adapter can damage the panel. ~$5–10.
- A few jumper wires.

**Software — a serial terminal (any one):**

| Tool | Command |
|---|---|
| **tio** (recommended) | `tio -b 115200 /dev/ttyUSB0` |
| picocom | `picocom -b 115200 /dev/ttyUSB0` |
| minicom | `minicom -D /dev/ttyUSB0 -b 115200` |
| screen | `screen /dev/ttyUSB0 115200` |
| PuTTY (Windows) | Serial, `COMx`, 115200, 8N1 |

Serial settings are **115200 8N1, no flow control**. On Linux, add yourself to the
`dialout` group to avoid `sudo`.

---

## Step 1 — Connect the cable

Open the panel and find the debug UART (a 3–4 pin header or test points labeled
**TX / RX / GND**; check community photos for your model if they're unlabeled).
Wire it up:

| Adapter | Panel |
|---|---|
| GND | GND |
| RX  | TX |
| TX  | RX |
| VCC | **leave disconnected** |

RX and TX cross over. Connect GND first.

## Step 2 — Open the terminal and power on

1. Start your terminal (e.g. `tio -b 115200 /dev/ttyUSB0`).
2. Power on the panel. You should see boot text scroll by.
   - **Garbled text?** Wrong baud (use 115200) or RX/TX are swapped.

## Step 3 — Interrupt the bootloader

As soon as **U-Boot** starts printing, press keys (try **Space**, then **Ctrl-C**)
to stop it at the `=>` prompt.

- Got `=>`? Continue to Step 4.
- Can't stop it, or it asks for a password? Use **Appendix A** instead.

## Step 4 — Boot into a root shell (no password needed)

At the `=>` prompt, boot the system but hand control to a shell instead of the
normal startup. Copy the kernel/initrd load lines from the boot messages you just
saw, and add `init=/bin/sh` to the kernel arguments. In most cases this works:

```
=> setenv bootargs "console=ttymxc3,115200 root=/dev/mmcblk0p4 rw \
     ostree=/ostree/boot.1/brilliant-linux/<hash>/0 init=/bin/sh"
=> boot
```

Replace `<hash>` with the value shown in the boot log. You'll land at a **root
shell with no password prompt**. (If it misbehaves, try `rd.break` instead of
`init=/bin/sh`, or use Appendix A.)

## Step 5 — Turn on SSH and set a password

```sh
mount -o remount,rw /

# enable SSH
ln -sf /usr/lib/systemd/system/sshd.socket \
       /etc/systemd/system/sockets.target.wants/sshd.socket

# allow root login
grep -q '^PermitRootLogin yes' /etc/ssh/sshd_config || \
  echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config

# set a password you choose
passwd root

sync
```

Power-cycle the panel. It boots normally, and now you can connect over your
network:

```sh
ssh root@<panel-ip>
```

## Step 6 — Lock it down (do this)

You've just put a password-login root box on your network. Switch to key-based
login and turn passwords off:

```sh
# on your computer: copy your public key over
mkdir -p /root/.ssh && chmod 700 /root/.ssh
echo 'ssh-ed25519 AAAA... you@host' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys

# then disable password login
sed -i 's/^PermitRootLogin yes/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
printf '\nPasswordAuthentication no\n' >> /etc/ssh/sshd_config
systemctl restart sshd.socket
```

Also consider firewalling port 22 to just your own machine at your router.

Done. Your changes live in `/etc` and stick around. (Only a full factory reset
wipes them.)

---

## Appendix A — If U-Boot is locked (USB fallback)

If you can't stop U-Boot, use the chip's built-in USB download mode, which sits
*below* the bootloader. Open-source tools:

- **uuu** (NXP mfgtools) — <https://github.com/nxp-imx/mfgtools>
- **imx_usb_loader** — <https://github.com/boundarydevices/imx_usb_loader>

Steps:
1. Put the board in **USB download mode** (boot-mode straps or a recovery
   jumper/button on the board) and connect its USB port to your computer.
2. Use `uuu` / `imx_usb_loader` to load a U-Boot into **RAM** (nothing is flashed).
3. From that U-Boot, run `ums 0 mmc 0` to expose the panel's storage to your
   computer as a USB drive.
4. Mount the `p4` partition and make the Step 5 changes directly in the files
   (add your key to `root/.ssh/authorized_keys`, create the `sshd.socket` link,
   add `PermitRootLogin`), or clear the root password in `etc/shadow`.
5. Unmount, boot normally, SSH in, and do Step 6.

No password needed, and it doesn't rely on U-Boot cooperating.

---

## FAQ: why not just spoof the dead cloud instead?

Common question: the company's servers are gone, so why not point the panel at a
fake `api.brilliant.tech` and have it enable SSH over the network — no cable, no
opening the case?

**It doesn't work for a single panel.** Redirecting DNS is easy, but the panel only
trusts Brilliant's **own private certificate** for its servers — not the normal
public ones. So a fake server can't prove it's Brilliant without Brilliant's secret
key, which nobody outside the company has. (Buying the old domain and getting a
normal SSL cert doesn't help.) The enable-SSH step was also email-verified and the
panel has no local network port to talk to directly.

**When it could work:** only at *fleet* scale, and only if Brilliant's certificate
key ever leaks (old code, an ex-employee, the bankruptcy sale). Then someone could
run a replacement cloud and re-home many panels at once with no cable. Short of
that, the UART method above is the way in.
