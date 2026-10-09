"""Preference rows that edit one option each and stay in sync with the config."""

import re

from gi.repository import Adw, Gdk, GLib, Gtk

NUMBER_RE = re.compile(r"-?\d+(\.\d+)?")


def summary(doc):
    para = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
    m = re.search(r"(?<=[a-z0-9)`][.!?])\s", para)
    sentence = para[:m.start()] if m else para
    return sentence if len(sentence) <= 140 else sentence[:137] + "…"


def format_number(value, digits):
    if digits == 0:
        return str(int(round(value)))
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


class OptionRow:
    """Builds the widget for one option.

    state is the owning AppState: it provides get/set of the user's values.
    on_change(key) is called after every edit.
    """

    def __init__(self, opt, state, on_change):
        self.opt, self.state, self.on_change = opt, state, on_change
        self._updating = False
        self._syncing = False
        self.kind = self._resolve_kind()
        self.widget = getattr(self, f"_build_{self.kind}")()
        self.widget.set_use_markup(False)
        self._add_suffixes()
        self.refresh()

    # -- values -----------------------------------------------------------

    def user(self):
        return self.state.user(self.opt.key)

    def first(self):
        values = self.state.values(self.opt.key)
        return values[0] if values else ""

    def commit(self, values):
        if self._updating:
            return
        self.state.set(self.opt.key, values)
        self.reset_button.set_visible(self.user() is not None)
        self.on_change(self.opt.key)

    def reset(self, *_):
        self.state.set(self.opt.key, None)
        self.refresh()
        self.on_change(self.opt.key)

    def refresh(self):
        self._updating = True
        try:
            getattr(self, f"_refresh_{self.kind}")()
        finally:
            self._updating = False
        self.reset_button.set_visible(self.user() is not None)

    def _resolve_kind(self):
        kind, user = self.opt.kind, self.user() or []
        if kind == "choice" and self.opt.multi and len(user) > 1:
            return "list"
        if kind == "bool" and user and user[0] not in ("true", "false"):
            return "text"
        if kind == "number" and user and not NUMBER_RE.fullmatch(user[0]):
            return "text"  # e.g. window-padding-x = 4,8
        return kind

    # -- shared chrome ----------------------------------------------------

    def _add_suffixes(self):
        self.reset_button = Gtk.Button(icon_name="edit-undo-symbolic", valign=Gtk.Align.CENTER,
                                       tooltip_text="Reset to default")
        self.reset_button.add_css_class("flat")
        self.reset_button.connect("clicked", self.reset)

        info = Gtk.MenuButton(icon_name="help-about-symbolic", valign=Gtk.Align.CENTER,
                              tooltip_text="Documentation")
        info.add_css_class("flat")
        info.set_create_popup_func(self._create_doc_popover)

        self.widget.add_suffix(self.reset_button)
        self.widget.add_suffix(info)

    def _create_doc_popover(self, button):
        if button.get_popover():
            return
        defaults = [d for d in self.opt.defaults if d]
        if len(defaults) > 3:
            default_text = f"{len(defaults)} built-in values"
        else:
            default_text = ", ".join(defaults) or "(empty)"
        text = f"{self.opt.doc or 'No documentation.'}\n\nDefault: {default_text}"
        label = Gtk.Label(label=text, wrap=True, xalign=0, max_width_chars=64,
                          margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        scroller = Gtk.ScrolledWindow(child=label, propagate_natural_height=True,
                                      max_content_height=440,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_size_request(460, -1)
        button.set_popover(Gtk.Popover(child=scroller))

    # -- kinds ------------------------------------------------------------

    def _build_bool(self):
        row = Adw.SwitchRow(title=self.opt.key, subtitle=summary(self.opt.doc))
        row.connect("notify::active",
                    lambda r, _: self.commit(["true" if r.get_active() else "false"]))
        return row

    def _refresh_bool(self):
        self.widget.set_active(self.first() == "true")

    def _build_choice(self):
        default = self.opt.defaults[0]
        self.default_label = f"Default ({default})" if default else "Default"
        self.model = Gtk.StringList.new([self.default_label, *self.opt.choices])
        row = Adw.ComboRow(title=self.opt.key, subtitle=summary(self.opt.doc), model=self.model)
        if len(self.opt.choices) > 12:
            row.set_enable_search(True)
            row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        row.connect("notify::selected", self._on_selected)
        return row

    def _on_selected(self, row, _):
        index = row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION:
            return
        self.commit(None if index == 0 else [self.model.get_string(index)])

    def _refresh_choice(self):
        user = self.user()
        index = 0
        if user:
            strings = [self.model.get_string(i) for i in range(self.model.get_n_items())]
            if user[0] in strings[1:]:
                index = strings.index(user[0], 1)
            else:
                self.model.append(user[0])  # a value we don't know about; keep it
                index = len(strings)
        self.widget.set_selected(index)

    def _build_number(self):
        opt = self.opt
        adjustment = Gtk.Adjustment(lower=opt.minimum, upper=opt.maximum,
                                    step_increment=opt.step, page_increment=opt.step * 10)
        row = Adw.SpinRow(title=opt.key, subtitle=summary(opt.doc), adjustment=adjustment,
                          digits=opt.digits)
        row.connect("notify::value",
                    lambda r, _: self.commit([format_number(r.get_value(), opt.digits)]))
        return row

    def _refresh_number(self):
        try:
            value = float(self.first())
        except ValueError:
            value = self.opt.minimum
        self.widget.set_value(value)

    def _build_text(self):
        row = Adw.EntryRow(title=self.opt.key, tooltip_text=summary(self.opt.doc))
        row.connect("changed", self._on_text)
        return row

    def _on_text(self, row):
        text = row.get_text()
        self.commit([text] if text else None)
        if self.kind == "color":
            self._sync_swatch(text)

    def _refresh_text(self):
        self.widget.set_text(self.first())

    def _build_color(self):
        row = self._build_text()
        self.color_button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                  valign=Gtk.Align.CENTER)
        self.color_button.connect("notify::rgba", self._on_color)
        row.add_suffix(self.color_button)
        return row

    def _on_color(self, button, _):
        if self._syncing or self._updating:
            return
        c = button.get_rgba()
        self.widget.set_text("#" + "".join(f"{round(v * 255):02x}" for v in (c.red, c.green, c.blue)))

    def _sync_swatch(self, text):
        rgba = Gdk.RGBA()
        if text and rgba.parse(text if not re.fullmatch(r"[0-9a-fA-F]{6}", text) else "#" + text):
            self._syncing = True
            self.color_button.set_rgba(rgba)
            self._syncing = False

    def _refresh_color(self):
        # Unset colors usually come from the theme, so don't show the built-in default.
        value = (self.user() or [""])[0]
        self.widget.set_text(value)
        if not value:
            self._syncing = True
            self.color_button.set_rgba(Gdk.RGBA(red=0, green=0, blue=0, alpha=0))
            self._syncing = False

    def _build_list(self):
        row = Adw.ExpanderRow(title=self.opt.key, subtitle=summary(self.opt.doc))
        self.buffer = Gtk.TextBuffer()
        self.buffer.connect("changed", self._on_list_changed)
        view = Gtk.TextView(buffer=self.buffer, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=10, bottom_margin=10, left_margin=12, right_margin=12)
        defaults = len([d for d in self.opt.defaults if d])
        hint = "One value per line."
        if defaults:
            hint += f" Leave empty to use the {defaults} built-in value{'s' * (defaults != 1)}."
        note = Gtk.Label(label=hint, xalign=0, wrap=True, margin_start=12, margin_end=12,
                         margin_top=6, margin_bottom=6)
        note.add_css_class("dim-label")
        note.add_css_class("caption")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(view)
        box.append(note)
        row.add_row(box)
        return row

    def _on_list_changed(self, buffer):
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.commit(lines or None)

    def _refresh_list(self):
        self.buffer.set_text("\n".join(self.user() or []))


class PreviewRow:
    def __init__(self, preview, state):
        self.preview, self.state = preview, state
        self.label = Gtk.Label(xalign=0, use_markup=True, selectable=False,
                               margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        self.widget = Adw.PreferencesRow(activatable=False, child=self.label)
        self.refresh()

    def refresh(self):
        try:
            self.label.set_markup(self.preview.render(self.state))
        except (GLib.Error, OSError, ValueError) as e:
            self.label.set_text(f"Preview unavailable: {e}")
