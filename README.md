# Archer

A personal GTK4/libadwaita tool for tweaking application configs on Arch.

## Supported apps

- **RGB Lighting** (requires `openrgb`): detects lighting controllers, with a
  shared color for all devices, per-device colors and effects, and per-zone
  color overrides for motherboard headers, fans and strips. Settings are
  saved in `~/.config/archer/rgb.conf` and applied through OpenRGB when saving;
  they are not automatically restored at login. Device support and available
  effects depend on OpenRGB. Configure addressable header LED counts in
  OpenRGB first if needed. Restart Archer after changing connected hardware.
  OpenRGB 1.1 is recommended: it fixes concurrent hardware writes and dropped
  CLI updates that can cause wrong GPU colors in 1.0. To use an AppImage,
  set **RGB Lighting → OpenRGB → OpenRGB executable** to its full path, then
  restart Archer. Both discovery and lighting updates use that executable.
- **Ghostty**: every option, read straight from the installed `ghostty` binary
  (docs, defaults, fonts, themes), with live font and color previews. Saving
  validates with `ghostty +validate-config`, keeps a `config.bak`, and reloads
  running Ghostty windows.
- **Sway**: appearance (gaps, borders, client colors with a preview), behavior,
  displays (a drag-to-arrange layout that snaps edges together, plus mode,
  scale, rotation and wallpaper per display) and input devices detected from
  the running session, a Workspaces page (pick an open app and the workspace
  it opens on, plus where everything else goes), plus
  line-by-line editors for keybindings (including modes), window rules,
  startup commands and variables, and an Idle page for the swayidle
  timeouts (screensaver and its font size, lock, displays off). Saving validates with `sway -C`, keeps a
  `config.bak`, and runs `swaymsg reload`.
- **Waybar**: bar settings, module layout, a page per module (built from your
  config plus each module's common options), and a Style page for fonts and
  a palette of every color in style.css (change one, it's replaced
  everywhere). Comments and formatting in the JSONC config are preserved.
  Saving checks the JSON and the CSS syntax, keeps `.bak` copies, and reloads
  Waybar (SIGUSR2).

Edits only rewrite the lines that changed: comments, ordering, blocks and
column alignment in your config are preserved. Symlinked configs (e.g. from a
dotfiles repo) are written through the link.

## Run

    archer            # via ~/.local/bin/archer
    python -m archer  # from this directory

Requires `python-gobject`, `gtk4` and `libadwaita`.

## Adding an app

Subclass `ConfigApp` in `archer/apps/` and add an instance to `ALL_APPS` in
`archer/apps/__init__.py`. `ghostty.py` shows a `key = value` format with
options discovered from the app; `sway.py` shows a custom config format
(`open_config`), curated options and custom pages (`build_page`).

## License

MIT. See [LICENSE](LICENSE).
