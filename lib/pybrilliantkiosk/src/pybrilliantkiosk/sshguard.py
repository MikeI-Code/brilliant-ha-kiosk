"""The optional SSH guard: keep a panel reachable over SSH across firmware (OSTree) updates.

Brilliant's updates replace ``/usr`` and can reset the "Root SSH Login" state, which would lock
an administrator out of a panel they manage over SSH. The guard re-asserts the SSH enable state
on every boot from assets kept in ``/var/lib/ha-ssh-guard`` (which updates do not touch).

These constants mirror the standalone payload in the repository's ``ssh-guard/`` directory; keep
the two in sync. :meth:`pybrilliantkiosk.Panel.install_ssh_guard` writes them to a panel.
"""

from __future__ import annotations

from typing import Final

GUARD_DIR: Final = "/var/lib/ha-ssh-guard"

#: The guard script. Idempotent and fail-safe: every step is best-effort and it always exits 0,
#: so it can never crash-loop at boot (which on this platform could trip the recovery partition).
GUARD_SH: Final = r"""#!/bin/sh
# ha-ssh-guard: keep SSH reachable on a Brilliant panel across firmware (OSTree) updates.
# Idempotent and FAIL-SAFE: every step is best-effort and the script always exits 0.
GUARD_DIR=/var/lib/ha-ssh-guard
LOG() { logger -t ha-ssh-guard "$*" 2>/dev/null; }

{
  # 1) Key-based access: add our key(s) to root's authorized_keys if missing. Append-only, so
  #    keys another tool or a second admin placed are preserved; never rewrites the file.
  if [ -s "$GUARD_DIR/authorized_keys" ]; then
    # Root's home is /home/root on this platform, not /root; resolve it from passwd.
    ROOT_HOME=$(awk -F: '$1=="root"{print $6; exit}' /etc/passwd 2>/dev/null)
    [ -n "$ROOT_HOME" ] || ROOT_HOME=/root
    mkdir -p "$ROOT_HOME/.ssh" 2>/dev/null
    chmod 700 "$ROOT_HOME/.ssh" 2>/dev/null
    AK="$ROOT_HOME/.ssh/authorized_keys"
    [ -f "$AK" ] || : > "$AK" 2>/dev/null
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      case "$line" in \#*) continue ;; esac
      if ! grep -qxF "$line" "$AK" 2>/dev/null; then
        printf '%s\n' "$line" >> "$AK" 2>/dev/null && LOG "added SSH key to root authorized_keys"
      fi
    done < "$GUARD_DIR/authorized_keys"
    chmod 600 "$AK" 2>/dev/null
  fi

  # 2) Allow root login, but only if an update reset sshd_config to its (commented) default.
  CONF=/etc/ssh/sshd_config
  MARK="# ha-ssh-guard"
  if [ -f "$CONF" ] && ! grep -qE '^[[:space:]]*PermitRootLogin[[:space:]]' "$CONF" 2>/dev/null
  then
    {
      echo ""
      echo "$MARK (keeps SSH reachable across updates; remove via uninstall-ssh-guard)"
      echo "PermitRootLogin prohibit-password"
    } >> "$CONF" 2>/dev/null && LOG "set PermitRootLogin prohibit-password"
  fi

  # 3) Re-enable socket-activated OpenSSH; re-create the /etc enable symlink if an update drops it.
  WANTS=/etc/systemd/system/sockets.target.wants
  if [ -f /usr/lib/systemd/system/sshd.socket ]; then
    mkdir -p "$WANTS" 2>/dev/null
    if [ ! -e "$WANTS/sshd.socket" ]; then
      ln -sf /usr/lib/systemd/system/sshd.socket "$WANTS/sshd.socket" 2>/dev/null \
        && LOG "re-enabled sshd.socket"
    fi
    systemctl start sshd.socket 2>/dev/null
  fi

  # 4) Last resort: if an update removes OpenSSH's server, start the optional bundled dropbear.
  DB="$GUARD_DIR/dropbear"
  DB_PORT="${HA_SSH_GUARD_PORT:-2222}"
  if [ ! -x /usr/sbin/sshd ] && [ -x "$DB" ]; then
    if ! pgrep -f "$GUARD_DIR/dropbear" >/dev/null 2>&1; then
      if "$DB" -p "$DB_PORT" -r "$GUARD_DIR/dropbear_host_key" -R >/dev/null 2>&1; then
        LOG "OpenSSH server absent; started fallback dropbear on port $DB_PORT"
      fi
    fi
  fi

  true
} 2>/dev/null

exit 0
"""

#: The systemd service that runs the guard at boot. Runs late, never gates the boot, no Restart=.
SERVICE_UNIT: Final = """[Unit]
Description=Keep SSH reachable across Brilliant firmware updates (HA Kiosk add-on)
After=multi-user.target sshd.socket

[Service]
Type=oneshot
ExecStart=/var/lib/ha-ssh-guard/ha-ssh-guard.sh

[Install]
WantedBy=multi-user.target
"""

#: The timer that re-checks after boot and periodically.
TIMER_UNIT: Final = """[Unit]
Description=Periodic SSH guard re-check (HA Kiosk add-on)

[Timer]
OnBootSec=2min
OnUnitActiveSec=30min
Unit=ha-ssh-guard.service

[Install]
WantedBy=timers.target
"""
