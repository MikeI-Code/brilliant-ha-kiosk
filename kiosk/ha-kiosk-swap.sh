#!/bin/sh
# Swap file for the panel (the Brilliant kernel has no zram). Low swappiness, so it is only a
# safety net against the OOM killer and the eMMC sees little wear. SWAP_MB=0 in kiosk.env disables.
[ -f /etc/ha-kiosk/kiosk.env ] && . /etc/ha-kiosk/kiosk.env
SWAP_MB=${SWAP_MB:-256}
SWAPPINESS=${SWAPPINESS:-10}
F=/var/lib/ha-kiosk-swap/swapfile

if [ "$SWAP_MB" -le 0 ]; then
    swapoff "$F" 2>/dev/null; echo "swap: disabled"; exit 0
fi
mkdir -p "${F%/*}"
size=$(( $(stat -c %s "$F" 2>/dev/null || echo 0) / 1048576 ))
if [ "$size" -ne "$SWAP_MB" ]; then
    swapoff "$F" 2>/dev/null
    echo "swap: creating ${SWAP_MB} MB swap file"
    dd if=/dev/zero of="$F" bs=1M count="$SWAP_MB" 2>/dev/null || exit 1
    chmod 600 "$F"
    mkswap "$F" >/dev/null || exit 1
fi
grep -q "^$F " /proc/swaps || swapon "$F" || exit 1
echo "$SWAPPINESS" > /proc/sys/vm/swappiness
echo "swap: $(awk -v f="$F" '$1==f {print $3/1024 " MB"}' /proc/swaps) active, swappiness $SWAPPINESS"
