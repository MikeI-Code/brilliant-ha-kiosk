#!/bin/sh
# ha-ssh-guard: keep SSH reachable on a Brilliant panel across firmware (OSTree) updates.
#
# Brilliant's updates replace /usr and can reset the "Root SSH Login" state, which would lock
# you out of a panel you administer over SSH. This guard re-asserts the SSH enable state on every
# run, restoring whatever an update removed. Its persistent assets live in /var/lib/ha-ssh-guard,
# which updates never touch; /etc and /root are re-seeded from there each boot.
#
# Design rule: idempotent and FAIL-SAFE. Every step is best-effort and the script always exits 0,
# so it can never crash-loop at boot (which on this platform could trip the recovery partition).

GUARD_DIR=/var/lib/ha-ssh-guard
LOG() { logger -t ha-ssh-guard "$*" 2>/dev/null; }

{
  # 1) Key-based access: make sure our key(s) are present in root's authorized_keys. This keeps
  #    you in even if the per-device password is unknown and survives /root being reset by an
  #    update. We only ADD missing lines and never rewrite the file, so keys another tool placed
  #    (or a second admin's) are preserved.
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
  #    Never fight an existing active directive. /etc survives updates (merged, not replaced).
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

  # 3) Re-enable socket-activated OpenSSH. The enable symlink lives in /etc; an update can drop
  #    it, so re-create it when missing, then (re)start the socket.
  WANTS=/etc/systemd/system/sockets.target.wants
  if [ -f /usr/lib/systemd/system/sshd.socket ]; then
    mkdir -p "$WANTS" 2>/dev/null
    if [ ! -e "$WANTS/sshd.socket" ]; then
      ln -sf /usr/lib/systemd/system/sshd.socket "$WANTS/sshd.socket" 2>/dev/null \
        && LOG "re-enabled sshd.socket"
    fi
    systemctl start sshd.socket 2>/dev/null
  fi

  # 4) Last-resort fallback: if a future update removes OpenSSH's server entirely, start the
  #    bundled static dropbear from /var on the fallback port. Only runs when /usr/sbin/sshd is
  #    gone AND a dropbear binary was installed beside this guard (optional).
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
