# systemd deployment

Copy the unit files to `/etc/systemd/system/` on `koast@192.168.3.60`.

```bash
sudo cp deploy/systemd/biz-monitor-slack.service /etc/systemd/system/
sudo cp deploy/systemd/biz-monitor-scheduled.service /etc/systemd/system/
sudo cp deploy/systemd/biz-monitor-scheduled.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now biz-monitor-slack.service
sudo systemctl enable --now biz-monitor-scheduled.timer
sudo systemctl status biz-monitor-slack.service
sudo systemctl status biz-monitor-scheduled.timer
```

Useful checks:

```bash
sudo journalctl -u biz-monitor-slack.service -f
sudo journalctl -u biz-monitor-scheduled.service -n 200 --no-pager
systemctl list-timers --all | grep biz-monitor
```

## User-level OpenClaw host diagnostics

The files under `deploy/openclaw/` are installed into
`/home/koast/openclaw/scripts/` and `~/.config/systemd/user/` on
`koast@192.168.3.60`.

They are intended for the headless server role:

- `capture-last-boot-crash.sh`
  - Captures the previous boot journal and Docker state right after the next boot.
- `capture-host-health.sh`
  - Stores a rolling health snapshot every 5 minutes.
- `openclaw-host-last-boot-snapshot.service`
  - Runs once per boot in the user manager.
- `openclaw-host-health.service`
- `openclaw-host-health.timer`

Suggested install:

```bash
install -m 755 deploy/openclaw/capture-last-boot-crash.sh /home/koast/openclaw/scripts/
install -m 755 deploy/openclaw/capture-host-health.sh /home/koast/openclaw/scripts/
install -m 644 deploy/openclaw/openclaw-host-last-boot-snapshot.service /home/koast/.config/systemd/user/
install -m 644 deploy/openclaw/openclaw-host-health.service /home/koast/.config/systemd/user/
install -m 644 deploy/openclaw/openclaw-host-health.timer /home/koast/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now openclaw-host-last-boot-snapshot.service
systemctl --user enable --now openclaw-host-health.timer
```

Headless follow-up that still requires root on the server:

```bash
sudo systemctl set-default multi-user.target
sudo systemctl disable --now gdm.service
sudo apt-get install -y kdump-tools linux-crashdump
sudo tee /etc/sysctl.d/99-koast-crash-watchdog.conf >/dev/null <<'EOF'
kernel.panic=30
kernel.panic_on_oops=1
kernel.softlockup_panic=1
kernel.hardlockup_panic=1
EOF
sudo sysctl --system
```

The root-level changes above were not applied automatically from Codex when the
server required an interactive sudo password.

## 2026-06-23 reboot incident

The host reboot issue on `koast@192.168.3.91` was traced to a custom shutdown
hook at `/usr/lib/systemd/system-shutdown/99-force-poweroff`.

The hook executed:

```sh
sync
echo o > /proc/sysrq-trigger
```

That forced a `SysRq-o` power-off at the end of the normal reboot path, which
matched the observed `sysrq: Power Off` console message and caused the machine
to power off instead of restarting cleanly.

Mitigation applied on 2026-06-23:

- Moved the hook out of the shutdown path to
  `/root/codex-disabled-hooks/99-force-poweroff.disabled-20260623`
- Restored `GRUB_CMDLINE_LINUX_DEFAULT=""`
- Verified 3 consecutive reboots with normal SSH recovery and no `sysrq`,
  `Power Off`, or `force_poweroff` markers in the previous-boot journal

Do not recreate this hook unless there is a deliberate requirement to force
power-off instead of reboot during shutdown.

Origin trace status:

- The hook was not owned by a Debian/Ubuntu package (`dpkg -S` had no match).
- No remaining script, cron entry, or shell history entry on the host referenced
  `99-force-poweroff` or `/proc/sysrq-trigger`.
- The remaining filesystem metadata showed the hook file was created and last
  modified on `2025-09-24` around `15:22` to `15:25` KST, so the best current
  conclusion is that it was added manually outside the tracked repo.
