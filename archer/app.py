"""Main window: a sidebar of apps and categories, and a page of options."""

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .apps import available_apps  # noqa: E402
from .rows import OptionRow, PreviewRow  # noqa: E402

APP_ID = "dev.lhansen.Archer"
MODIFIED = "Modified"
SEARCH_LIMIT = 60


class AppState:
    """One configurable app: its options plus the user's config file."""

    def __init__(self, app):
        self.app = app
        self.cfg = app.open_config(app.config_path())
        self.options = app.load_options()
        self.by_key = {opt.key: opt for opt in self.options}
        self.previews = app.previews()

    def user(self, key):
        return self.cfg.get(key)

    def defaults(self, key):
        opt = self.by_key.get(key)
        return opt.defaults if opt else []

    def values(self, key):
        user = self.cfg.get(key)
        return user if user is not None else self.defaults(key)

    def first(self, key):
        values = self.values(key)
        return values[0] if values else ""

    def set(self, key, values):
        self.cfg.set(key, values)

    def visible(self, opt):
        return not opt.hidden or self.cfg.get(opt.key) is not None

    def modified(self):
        return [opt for opt in self.options if self.cfg.get(opt.key) is not None]

    def save(self):
        """Validate and write the config. Returns an error message or None."""
        # Write through symlinks (e.g. a config linked from a dotfiles repo).
        path = self.cfg.path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".archer")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(self.cfg.render())
            if error := self.app.validate(Path(tmp)):
                return error
            if path.exists():
                shutil.copy2(path, path.with_name(path.name + ".bak"))
                shutil.copymode(path, tmp)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp:
                os.unlink(tmp)
        self.cfg.mark_saved()
        return None


class ArcherWindow(Adw.ApplicationWindow):
    def __init__(self, application, states):
        super().__init__(application=application, title="Archer",
                         default_width=1100, default_height=760)
        self.states = states
        self.nav = []          # per sidebar row: (state, category) or None for headers
        self.rows = []         # OptionRows on the current page
        self.preview_rows = []
        self.modified_labels = {}
        self.current = None
        self.force_close = False

        self._build_actions()
        self.set_content(self._build_layout())
        self.connect("close-request", self._on_close_request)

        first = next((i for i, item in enumerate(self.nav) if item), None)
        if first is not None:
            self.sidebar.select_row(self.sidebar.get_row_at_index(first))
        else:
            self.toasts.set_child(Adw.StatusPage(
                icon_name="dialog-information-symbolic", title="No supported apps found",
                description="Install an app Archer knows how to configure, such as Ghostty."))
        self._update_dirty()

    # -- layout -----------------------------------------------------------

    def _build_layout(self):
        self.search = Gtk.SearchEntry(placeholder_text="Search all settings",
                                      margin_start=12, margin_end=12, margin_bottom=6)
        self.search.connect("search-changed", self._on_search)
        self.search.set_key_capture_widget(self)

        self.sidebar = Gtk.ListBox()
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.connect("row-selected", self._on_nav)
        for state in self.states:
            self._add_sidebar_app(state)

        sidebar_view = Adw.ToolbarView()
        sidebar_view.add_top_bar(Adw.HeaderBar())
        sidebar_view.add_top_bar(self.search)
        sidebar_view.set_content(Gtk.ScrolledWindow(
            child=self.sidebar, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))

        self.title = Adw.WindowTitle()
        header = Adw.HeaderBar(title_widget=self.title)
        menu = Gio.Menu()
        menu.append("Open Config File", "win.open-file")
        menu.append("Revert Unsaved Changes", "win.revert")
        menu.append("About Archer", "win.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))
        save = Gtk.Button(label="Save", action_name="win.save",
                          tooltip_text="Save and apply (Ctrl+S)")
        save.add_css_class("suggested-action")
        header.pack_end(save)

        self.toasts = Adw.ToastOverlay()
        content_view = Adw.ToolbarView()
        content_view.add_top_bar(header)
        content_view.set_content(self.toasts)

        return Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="Archer", child=sidebar_view),
            content=Adw.NavigationPage(title="Settings", child=content_view),
            min_sidebar_width=220)

    def _add_sidebar_app(self, state):
        heading = Gtk.Label(label=state.app.name, xalign=0, margin_top=12, margin_start=6)
        heading.add_css_class("heading")
        heading.add_css_class("dim-label")
        self.sidebar.append(Gtk.ListBoxRow(child=heading, selectable=False, activatable=False))
        self.nav.append(None)

        for name, icon in [(MODIFIED, "document-edit-symbolic"), *state.app.categories]:
            box = Gtk.Box(spacing=12)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=name, xalign=0, hexpand=True))
            if name == MODIFIED:
                count = Gtk.Label()
                count.add_css_class("dim-label")
                box.append(count)
                self.modified_labels[state.app.id] = count
            self.sidebar.append(Gtk.ListBoxRow(child=box))
            self.nav.append((state, name))

    def _build_actions(self):
        for name, callback in [("save", self._save), ("revert", self._revert),
                               ("open-file", self._open_file), ("about", self._about),
                               ("search", lambda *_: self.search.grab_focus())]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        app = self.get_application()
        app.set_accels_for_action("win.save", ["<Control>s"])
        app.set_accels_for_action("win.search", ["<Control>f"])
        app.set_accels_for_action("window.close", ["<Control>w", "<Control>q"])

    # -- pages ------------------------------------------------------------

    def _on_nav(self, _listbox, row):
        if row is None or not (item := self.nav[row.get_index()]):
            return
        self.current = item
        if self.search.get_text():
            self.search.set_text("")  # _on_search shows the page
        else:
            self._show_category(*item)

    def _on_search(self, entry):
        query = entry.get_text().strip().lower()
        if query:
            self.sidebar.unselect_all()
            self._show_search(query)
        elif self.current:
            index = self.nav.index(self.current)
            if self.sidebar.get_selected_row() is None:
                self.sidebar.select_row(self.sidebar.get_row_at_index(index))
            else:
                self._show_category(*self.current)

    def _new_page(self):
        self.rows, self.preview_rows = [], []
        page = Adw.PreferencesPage()
        self.toasts.set_child(page)
        return page

    def _add_option(self, group, state, opt):
        row = OptionRow(opt, state, lambda key: self._on_option_changed(state, key))
        group.add(row.widget)
        self.rows.append(row)

    def _show_category(self, state, category):
        self.title.set_title(f"{state.app.name} · {category}")
        if category == MODIFIED:
            options = state.modified()
            if not options:
                self.rows, self.preview_rows = [], []
                self.toasts.set_child(Adw.StatusPage(
                    icon_name="document-edit-symbolic", title="Nothing changed yet",
                    description=f"Options you set in {state.cfg.path} show up here."))
                return
        else:
            groups = state.app.build_page(category, state,
                                          lambda: self._on_option_changed(state, None))
            if groups is not None:
                page = self._new_page()
                for group in groups:
                    page.add(group)
                return
            options = [o for o in state.options if o.category == category and state.visible(o)]

        page = self._new_page()
        previews = [p for p in state.previews if p.category == category]
        if previews:
            group = Adw.PreferencesGroup(title="Preview")
            for preview in previews:
                row = PreviewRow(preview, state)
                group.add(row.widget)
                self.preview_rows.append(row)
            page.add(group)

        group = Adw.PreferencesGroup(
            title=category if previews else "",
            description=str(state.cfg.path) if category == MODIFIED else "")
        for opt in options:
            self._add_option(group, state, opt)
        if len(options) <= 3:  # e.g. Keybindings: open the editors straight away
            for row in self.rows:
                if isinstance(row.widget, Adw.ExpanderRow):
                    row.widget.set_expanded(True)
        page.add(group)

    def _show_search(self, query):
        self.title.set_title(f"Search: {query}")
        results = []
        for state in self.states:
            matches = [o for o in state.options if state.visible(o)
                       and (query in o.key or query in o.doc.lower())]
            matches.sort(key=lambda o: (query not in o.key, not o.key.startswith(query), o.key))
            if matches:
                results.append((state, matches))
        if not results:
            self.rows, self.preview_rows = [], []
            self.toasts.set_child(Adw.StatusPage(icon_name="edit-find-symbolic",
                                                 title="No matching settings"))
            return
        page = self._new_page()
        for state, matches in results:
            shown = matches[:SEARCH_LIMIT]
            extra = f", showing {len(shown)}" if len(matches) > len(shown) else ""
            group = Adw.PreferencesGroup(title=state.app.name,
                                         description=f"{len(matches)} matches{extra}")
            for opt in shown:
                self._add_option(group, state, opt)
            page.add(group)

    # -- state ------------------------------------------------------------

    def _on_option_changed(self, state, key):
        for row in self.preview_rows:
            if row.state is state and key in row.preview.keys:
                row.refresh()
        self._update_dirty()

    def _dirty_states(self):
        return [s for s in self.states if s.cfg.dirty]

    def _update_dirty(self):
        dirty = self._dirty_states()
        self.lookup_action("save").set_enabled(bool(dirty))
        self.lookup_action("revert").set_enabled(bool(dirty))
        self.title.set_subtitle("Unsaved changes" if dirty else "")
        for state in self.states:
            count = len(state.modified())
            self.modified_labels[state.app.id].set_label(str(count) if count else "")

    def _current_state(self):
        return self.current[0] if self.current else (self.states[0] if self.states else None)

    def _refresh_page(self):
        if query := self.search.get_text().strip().lower():
            self._show_search(query)
        elif self.current:
            self._show_category(*self.current)

    def _save(self, *_):
        messages = []
        for state in self._dirty_states():
            if error := state.save():
                dialog = Adw.AlertDialog(heading=f"{state.app.name} rejected the config",
                                         body=f"Nothing was saved.\n\n{error}")
                dialog.add_response("ok", "OK")
                dialog.present(self)
                self._update_dirty()
                return False
            messages.append(state.app.apply())
        self._update_dirty()
        if messages:
            self.toasts.add_toast(Adw.Toast(title="; ".join(messages), timeout=3))
        return True

    def _revert(self, *_):
        for state in self.states:
            state.cfg.reload()
        self._refresh_page()
        self._update_dirty()
        self.toasts.add_toast(Adw.Toast(title="Reverted to the saved config", timeout=2))

    def _open_file(self, *_):
        if state := self._current_state():
            path = state.cfg.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=True)
            Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)

    def _about(self, *_):
        Adw.AboutDialog(application_name="Archer", application_icon="preferences-system",
                        version=__version__, developer_name="lhansen",
                        comments="A personal configuration tweaker for Arch applications."
                        ).present(self)

    def _on_close_request(self, _window):
        if self.force_close or not self._dirty_states():
            return False
        dialog = Adw.AlertDialog(heading="Save changes?",
                                 body="You have unsaved changes. Unsaved changes are lost "
                                      "when you close Archer.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_close_response)
        dialog.present(self)
        return True

    def _on_close_response(self, _dialog, response):
        if response == "discard" or (response == "save" and self._save()):
            self.force_close = True
            self.close()


class Archer(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        window = self.props.active_window
        if not window:
            states = []
            for app in available_apps():
                try:
                    states.append(AppState(app))
                except Exception:
                    print(f"archer: failed to load {app.name}:", file=sys.stderr)
                    traceback.print_exc()
            window = ArcherWindow(self, states)
        window.present()


def main():
    GLib.set_application_name("Archer")
    return Archer().run(sys.argv)
