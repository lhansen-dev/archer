"""A drag-to-arrange view of displays, like the one in GNOME Settings.

Displays are drawn to scale in layout (logical) pixels. While dragging, edges
and centers snap to the other displays; on release the display is moved to
the nearest spot where it shares an edge with another one and overlaps none,
so the pointer can always cross between them. The layout is then shifted so
it starts at 0,0.
"""

import math
from dataclasses import dataclass

from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango

SNAP = 16      # screen pixels within which edges snap together
PADDING = 20   # screen pixels around the layout
RADIUS = 8


@dataclass
class Display:
    name: str
    label: str     # make and model, e.g. "ASUSTek VG27A"
    detail: str    # e.g. "2560×1440 @ 144 Hz"
    x: int
    y: int
    width: int     # logical size: mode / scale, rotated
    height: int

    def overlaps(self, other):
        return (self.x < other.x + other.width and other.x < self.x + self.width
                and self.y < other.y + other.height and other.y < self.y + self.height)

    def touches(self, other):
        shared_y = min(self.y + self.height, other.y + other.height) - max(self.y, other.y)
        shared_x = min(self.x + self.width, other.x + other.width) - max(self.x, other.x)
        side = (self.x == other.x + other.width or other.x == self.x + self.width) and shared_y > 0
        stacked = (self.y == other.y + other.height or other.y == self.y + self.height) \
            and shared_x > 0
        return side or stacked


class Arrangement(Gtk.Widget):
    """on_moved(displays) is called after a drag changes the layout."""

    def __init__(self, displays, on_moved):
        super().__init__(hexpand=True, height_request=280)
        self.displays = displays
        self.on_moved = on_moved
        self.dragging = None
        self.start = None
        self.view = None  # frozen during a drag, so the layout doesn't rescale under the pointer

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_begin)
        drag.connect("drag-update", self._on_update)
        drag.connect("drag-end", self._on_end)
        self.add_controller(drag)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

        style = Adw.StyleManager.get_default()
        for prop in ("notify::accent-color", "notify::dark"):
            style.connect(prop, lambda *_: self.queue_draw())

    def set_displays(self, displays):
        if self.dragging is None:
            self.displays = displays
            self.queue_draw()

    # -- geometry ---------------------------------------------------------

    def _fit(self):
        """(scale, offset x, offset y) mapping layout pixels to widget pixels."""
        if not self.displays:
            return 1, 0, 0
        left = min(d.x for d in self.displays)
        top = min(d.y for d in self.displays)
        width = max(d.x + d.width for d in self.displays) - left
        height = max(d.y + d.height for d in self.displays) - top
        w, h = self.get_width() - 2 * PADDING, self.get_height() - 2 * PADDING
        scale = min(w / width, h / height) if width and height else 1
        return (scale, PADDING + (w - width * scale) / 2 - left * scale,
                PADDING + (h - height * scale) / 2 - top * scale)

    def _rect(self, d, view):
        scale, ox, oy = view
        return ox + d.x * scale, oy + d.y * scale, d.width * scale, d.height * scale

    def _at(self, x, y):
        view = self._fit()
        for d in reversed(self.displays):
            rx, ry, rw, rh = self._rect(d, view)
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return d
        return None

    def _snap(self, d, x, y, tolerance):
        others = [o for o in self.displays if o is not d]

        def nearest(value, candidates):
            best = min(candidates, key=lambda c: abs(c - value), default=value)
            return best if abs(best - value) <= tolerance else value

        x = nearest(x, [c for o in others for c in (
            o.x + o.width, o.x - d.width, o.x, o.x + o.width - d.width,
            o.x + (o.width - d.width) / 2)])
        y = nearest(y, [c for o in others for c in (
            o.y + o.height, o.y - d.height, o.y, o.y + o.height - d.height,
            o.y + (o.height - d.height) / 2)])
        return round(x), round(y)

    def _place(self, d):
        """Move d to the nearest position flush against another display."""
        others = [o for o in self.displays if o is not d]
        if not others or (any(d.touches(o) for o in others)
                          and not any(d.overlaps(o) for o in others)):
            return
        candidates = []
        for o in others:
            # Keep at least a quarter of the shorter edge shared.
            min_y = min(d.height, o.height) // 4
            min_x = min(d.width, o.width) // 4
            y = max(o.y - d.height + min_y, min(d.y, o.y + o.height - min_y))
            x = max(o.x - d.width + min_x, min(d.x, o.x + o.width - min_x))
            candidates += [(o.x + o.width, y), (o.x - d.width, y),
                           (x, o.y + o.height), (x, o.y - d.height)]
        original = d.x, d.y
        best = None
        for cx, cy in sorted(candidates, key=lambda c: math.dist(c, original)):
            d.x, d.y = cx, cy
            if not any(d.overlaps(o) for o in others):
                best = cx, cy
                break
        d.x, d.y = best or original

    def _normalize(self):
        left = min(d.x for d in self.displays)
        top = min(d.y for d in self.displays)
        for d in self.displays:
            d.x -= left
            d.y -= top

    # -- input ------------------------------------------------------------

    def _on_motion(self, _controller, x, y):
        if self.dragging is None:
            self.set_cursor_from_name("grab" if self._at(x, y) else None)

    def _on_begin(self, gesture, x, y):
        d = self._at(x, y)
        if d is None or len(self.displays) < 2:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.view = self._fit()
        self.dragging, self.start = d, (d.x, d.y)
        # Draw the dragged display on top.
        self.displays.remove(d)
        self.displays.append(d)
        self.set_cursor_from_name("grabbing")

    def _on_update(self, _gesture, dx, dy):
        if self.dragging is None:
            return
        scale = self.view[0]
        d = self.dragging
        d.x, d.y = self._snap(d, self.start[0] + dx / scale, self.start[1] + dy / scale,
                              SNAP / scale)
        self.queue_draw()

    def _on_end(self, _gesture, _dx, _dy):
        d = self.dragging
        if d is None:
            return
        before = {o.name: (o.x, o.y) for o in self.displays}
        before[d.name] = self.start
        self._place(d)
        self._normalize()
        self.dragging = self.view = None
        self.set_cursor_from_name("grab")
        self.queue_draw()
        if any((o.x, o.y) != before[o.name] for o in self.displays):
            self.on_moved(self.displays)

    # -- drawing ----------------------------------------------------------

    def do_snapshot(self, snapshot):
        view = self.view or self._fit()
        fg = self.get_color()
        accent = Adw.StyleManager.get_default().get_accent_color_rgba()
        white = Gdk.RGBA(red=1, green=1, blue=1, alpha=1)
        for d in self.displays:
            x, y, w, h = self._rect(d, view)
            x, y, w, h = x + 2, y + 2, max(w - 4, 1), max(h - 4, 1)  # a gap between neighbors
            rect = Graphene.Rect().init(x, y, w, h)
            outline = Gsk.RoundedRect()
            outline.init_from_rect(rect, min(RADIUS, w / 2, h / 2))
            active = d is self.dragging
            fill = accent if active else _alpha(fg, 0.12)
            border = accent if active else _alpha(fg, 0.35)
            snapshot.push_rounded_clip(outline)
            snapshot.append_color(fill, rect)
            snapshot.pop()
            snapshot.append_border(outline, [1.5] * 4, [border] * 4)

            name, label, detail = (GLib.markup_escape_text(t) for t in (d.name, d.label, d.detail))
            layout = self.create_pango_layout(None)
            layout.set_markup(f"<b>{name}</b>\n<small>{label}\n{detail}</small>", -1)
            layout.set_alignment(Pango.Alignment.CENTER)
            layout.set_width(int(max(w - 12, 1) * Pango.SCALE))
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            _, extent = layout.get_pixel_extents()
            if extent.height > h - 8:
                layout.set_markup(f"<b>{name}</b>", -1)
                _, extent = layout.get_pixel_extents()
            snapshot.save()
            snapshot.translate(Graphene.Point().init(x + 6, y + (h - extent.height) / 2))
            snapshot.append_layout(layout, white if active else _alpha(fg, 0.9))
            snapshot.restore()


def _alpha(color, alpha):
    return Gdk.RGBA(red=color.red, green=color.green, blue=color.blue, alpha=alpha)
