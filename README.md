# Archer

A personal GTK4/libadwaita tool for tweaking application configs on Arch.

## Supported apps

- **Ghostty**: every option, read straight from the installed `ghostty` binary
  (docs, defaults, fonts, themes), with live font and color previews. Saving
  validates with `ghostty +validate-config`, keeps a `config.bak`, and reloads
  running Ghostty windows.

## Run

    archer            # via ~/.local/bin/archer
    python -m archer  # from this directory

Requires `python-gobject`, `gtk4` and `libadwaita`.

## Adding an app

Subclass `ConfigApp` in `archer/apps/` (see `ghostty.py`), describe its options
as `Option`s, and add an instance to `ALL_APPS` in `archer/apps/__init__.py`.
