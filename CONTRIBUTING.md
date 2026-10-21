# Contributing

Thanks for your interest. Da capo is small on purpose: one playbook, five
roles, a demo site. Keep it that way.

## Ground rules

- **The core stays neutral.** No branding, texts, URLs, fonts, logos or
  secrets of a particular site in the core. If something must differ per
  fleet, it becomes a variable in `vars/defaults.yml` with a sensible,
  neutral default and a comment.
- **Chroot safety.** The first run happens in the Debian installer without
  systemd. Services and timers are only enabled there; never add an
  unconditional `state: started`.
- **Idempotence.** Two consecutive runs must report `changed=0` on the
  second. `tests/docker-test.sh` checks this.
- **English** in code, comments, docs and commit messages.

## Before you open a pull request

```bash
ansible-playbook --syntax-check -i localhost, local.yml
ansible-lint local.yml roles/ vars/ tests/render.yml
for p in browser app display; do ansible-playbook -i localhost, tests/render.yml -e profile=$p; done
tests/docker-test.sh browser        # and app, display — full run in debian:trixie
shellcheck -S warning preseed/*.sh raspberry/*.sh tests/*.sh tools/*.sh
```

The same checks run in GitHub Actions. If you change a template, run
`tools/make-screenshots.sh` and commit the updated `docs/images/` when the
screens look different.

## Adding a variable

1. Add it to `vars/defaults.yml` in the right section with a comment.
2. Use it in the role template or task.
3. Mention it in `docs/site-layout.md` or `docs/profiles.md`.
4. Set a demo value in `examples/site-demo/` only when it is profile- or
   site-specific.

## Adding a role or a profile type

Open an issue first and describe the kiosk type. The three Firefox modes
cover most cases; a new mode is a bigger change than it looks (profile
template, sway config, tests).

## Reporting bugs

Please include the profile, the rendered file (`tests/render.yml`) and the
relevant journal lines (`journalctl -u ansible-pull.service`,
`journalctl -t kiosk-firefox -b`). Never paste `secrets.yml` or a pull URL
with a token.
