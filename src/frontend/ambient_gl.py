"""
src/frontend/ambient_gl.py

THE AMBIENT FIELD, RENDERED ON THE GPU (v10.3).

WHY THIS EXISTS, MEASURED
    The raster field (widgets.AmbientGlow) is the bottom widget in the
    shell, and every surface above it is translucent by design. So a
    repaint there is never "repaint the wash": Qt must re-rasterise the
    whole translucent stack on the GUI thread. Measured on the reference
    machine at 1300x860, in a REAL event loop with real idle between
    frames (a tight update/processEvents loop measures vsync busy-wait,
    not work — every arm of the first four probes came back at wall
    5.556ms, i.e. exactly 180.0fps, which is the swap interval and not a
    cost):

        raster @ 10Hz (what shipped) ...  4.3% of one core
        raster @ 60Hz ................. 40.2% of one core
        GPU    @ 60Hz ................. 10.9% of one core

    60fps on the raster path costs 40% of a core for a background
    decoration. On the GPU it costs a quarter of that, for six times the
    frame rate of the shipped field.

THE STRUCTURAL REASON IT WORKS
    A QOpenGLWidget renders into its own texture, which the compositor
    blends with the backing store holding the widgets above it. Animating
    it therefore does NOT re-rasterise them. Verified rather than assumed,
    because Qt's own docs warn about widgets overlapping a QOpenGLWidget:
    with the sidebar, content well and fourteen cards stacked on top,
    driving this widget at 60fps produced **0.000 card repaints per
    frame**. That single number is what the whole approach rests on.

WHAT IS SHARED WITH THE RASTER PATH, AND WHY
    Only the DRAWING moves to the GPU. The simulation — the seeded
    scatter, the per-tier drift, the wrap, the sway, the twinkle phase,
    the pointer bias — stays exactly where it was, in the code the raster
    field already uses (widgets._AmbientSimulation). Two reasons, both
    practical:

      1. Visual parity is by construction rather than by careful
         re-derivation. A star is in the same place in both renderers
         because the same arithmetic put it there.
      2. It is free. Integrating 126 particles is microseconds; the cost
         was never the maths, it was the full-window repaint the maths
         triggered.

    Per frame this uploads 126 x vec4 (~2KB) and issues three draws. The
    per-star parameters ride in a vertex buffer rather than a uniform
    array precisely so the count can grow without hitting a uniform limit.

FALLBACK IS AUTOMATIC AND SILENT
    See capability(). A machine without GL, or with SOFTWARE-EMULATED GL
    (llvmpipe / WARP / GDI generic — common under RDP and in VMs), gets
    the raster field with no setting to find and no message to dismiss.
    Software GL is explicitly treated as "no GL": it is slower than the
    raster path, so taking it would make the field worse on exactly the
    machines least able to afford it.
"""
from __future__ import annotations

import ctypes
import math
import os
import struct

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QOpenGLContext, QSurfaceFormat, QVector2D

from frontend import theme as TH
# widgets does NOT import this module, so this cannot cycle. The base is
# imported at module scope rather than lazily because GLAmbientField has to
# actually INHERIT it — sharing only its __init__ would leave the two
# renderers free to drift, which is the one thing it exists to prevent.
from frontend.widgets import AmbientGlow, _AmbientSimulation

# QtOpenGL / QtOpenGLWidgets are separate wheels' worth of symbols and can
# be absent or broken independently of the rest of Qt. Import failure here
# is a supported outcome, not an error: it simply means the raster field.
try:
    from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLFramebufferObject,
                                  QOpenGLShader, QOpenGLShaderProgram,
                                  QOpenGLVertexArrayObject)
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    _GL_IMPORTED = True
except ImportError:                                  # pragma: no cover
    QOpenGLWidget = object                           # type: ignore[assignment]
    _GL_IMPORTED = False


# ============================================================
#  RAW GL ENUMS
# ============================================================
# Spelled out rather than imported from PyOpenGL, which is not a
# dependency and would be a large one to add for eleven integers.
_GL_TRIANGLES = 0x0004
_GL_POINTS = 0x0000
_GL_BLEND = 0x0BE2
_GL_SRC_ALPHA = 0x0302
_GL_ONE_MINUS_SRC_ALPHA = 0x0303
_GL_ZERO = 0x0000
_GL_SRC_COLOR = 0x0300
_GL_TEXTURE_2D = 0x0DE1
_GL_TEXTURE_MIN_FILTER = 0x2801
_GL_TEXTURE_MAG_FILTER = 0x2800
_GL_LINEAR = 0x2601
_GL_PROGRAM_POINT_SIZE = 0x8642
_GL_RENDERER = 0x1F01
_GL_VERSION = 0x1F02

#: Renderer substrings that mean "this GL is running on the CPU". Taking
#: the GPU path on these is strictly worse than the raster field — it is
#: the same software rasterisation plus a texture upload and a composite.
_SOFTWARE_RENDERERS = (
    "llvmpipe", "softpipe", "swrast", "software rasterizer",
    "gdi generic", "microsoft basic render", "warp", "swiftshader",
    "mesa offscreen", "d3d12 (microsoft basic render driver)",
)

#: Override for tests and for a user who needs to force a path:
#:   PULSE_AMBIENT=raster  — never use the GPU
#:   PULSE_AMBIENT=gl      — use it even if it looks software-emulated
#:   PULSE_AMBIENT=auto    — the default; capability() decides
_ENV_OVERRIDE = "PULSE_AMBIENT"


# ============================================================
#  SHADERS
# ============================================================
# One full-screen triangle generated from gl_VertexID — no vertex buffer,
# no attribute marshalling across the PySide6 boundary for the two
# full-screen passes.
_FS_VERT = """#version 330 core
out vec2 uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

# ORB PASS. Five drifting, breathing radial blobs over the canvas
# gradient, rendered into a SMALL offscreen buffer (see _ORB_DIV) on the
# raster path's own 100ms cadence (_ORB_MS). Orbs are smooth gradients by
# construction, so resolution costs them nothing visually — the same
# argument that lets the raster path cache a 512px orb texture and scale
# it, and that let the frosted modal backdrop render its host at blur
# resolution for 7ms instead of 29.
_ORB_FRAG = """#version 330 core
uniform vec3  canvas_top;
uniform vec3  canvas_bottom;
uniform vec3  orb_color[5];
uniform vec3  orb_pos[5];      // xy = centre in [0,1], z = radius
uniform float orb_peak[5];
in  vec2 uv;
out vec4 frag;

void main() {
    // The shell's own gradient, so this widget is opaque and Qt never has
    // to paint anything beneath it.
    vec3 c = mix(canvas_top, canvas_bottom,
                 clamp((uv.x * 0.3 + (1.0 - uv.y)) / 1.3, 0.0, 1.0));
    for (int i = 0; i < 5; ++i) {
        float d = distance(uv, orb_pos[i].xy) / max(orb_pos[i].z, 0.0001);
        // Matches the raster orb texture's falloff: 1.0 at the core,
        // 0.35 at 0.45 of the radius, 0 at the rim.
        float a = orb_peak[i] * exp(-d * d * 2.2);
        c += orb_color[i] * a;
    }
    frag = vec4(c, 1.0);
}
"""

_BLIT_FRAG = """#version 330 core
uniform sampler2D tex;
in  vec2 uv;
out vec4 frag;
void main() { frag = texture(tex, uv); }
"""

# STAR PASS. One GL_POINT per star; the sprite's radial falloff comes from
# gl_PointCoord, so there is no texture, no instancing and no per-vertex
# geometry — 126 vertices of vec4 a frame.
#
# GL_POINTS rather than instanced quads deliberately: it needs one buffer
# and one draw, where instancing needs a divisor'd VAO layout for the same
# result. Max point size is implementation-defined but the floor the spec
# guarantees is far above this field's largest sprite (20px).
_STAR_VERT = """#version 330 core
layout(location = 0) in vec4 star;   // x_px, y_px, span_px, alpha
uniform vec2 res;
out float v_alpha;
void main() {
    v_alpha = star.w;
    gl_PointSize = star.z;
    gl_Position = vec4((star.xy / res) * 2.0 - 1.0, 0.0, 1.0);
}
"""

# The falloff reproduces widgets.AmbientGlow._star_pixmap's gradient
# (alpha 1.0 at the core, 0.42 at 0.30 of the radius, 0 at the rim) so the
# two renderers draw the same star.
_STAR_FRAG = """#version 330 core
uniform vec3 star_color;
in  float v_alpha;
out vec4 frag;
void main() {
    vec2 local = gl_PointCoord * 2.0 - 1.0;
    float d = length(local);
    if (d > 1.0) discard;
    float a;
    if (d <= 0.30) a = 1.0 + (0.42 - 1.0) * (d / 0.30);
    else           a = 0.42 * (1.0 - (d - 0.30) / 0.70);
    a *= v_alpha;
    frag = vec4(star_color * a, a);
}
"""


# ============================================================
#  CAPABILITY PROBE
# ============================================================
def _renderer_is_software(renderer: str) -> bool:
    low = (renderer or "").lower()
    return any(token in low for token in _SOFTWARE_RENDERERS)


def capability() -> tuple[bool, str]:
    """`(use_gpu, reason)` — may this process render the field on the GPU?

    Answered ONCE, before any window exists, by creating a throwaway
    offscreen context. Doing it here rather than inside initializeGL is
    what keeps the fallback silent: the shell picks a renderer up front
    and never has to swap one out after the user has seen it.

    Software GL counts as NO GL. llvmpipe/WARP/GDI-generic are the CPU
    doing the same rasterisation the raster path does, plus a texture
    upload and a composite — so on an RDP session or a VM, taking the
    "GPU" path would make the field slower on exactly the machines with
    the least headroom. This is why the check reads the renderer string
    and not merely "did a context appear".
    """
    override = os.environ.get(_ENV_OVERRIDE, "auto").strip().lower()
    if override == "raster":
        return False, "PULSE_AMBIENT=raster"
    if not _GL_IMPORTED:
        return False, "PySide6 QtOpenGL is unavailable"

    from PySide6.QtGui import QOffscreenSurface

    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setVersion(3, 3)

    ctx = QOpenGLContext()
    ctx.setFormat(fmt)
    if not ctx.create():
        return False, "no OpenGL context could be created"

    surface = QOffscreenSurface()
    surface.setFormat(ctx.format())
    surface.create()
    if not surface.isValid() or not ctx.makeCurrent(surface):
        return False, "no usable offscreen surface"
    try:
        fns = ctx.functions()
        renderer = _gl_string(fns, _GL_RENDERER)
        version = ctx.format().majorVersion(), ctx.format().minorVersion()
        if version < (3, 3):
            return False, f"OpenGL {version[0]}.{version[1]} is below 3.3"
        if override == "gl":
            return True, f"PULSE_AMBIENT=gl ({renderer})"
        if _renderer_is_software(renderer):
            return False, f"software-emulated GL ({renderer})"
        return True, renderer or "OpenGL"
    finally:
        # ORDER MATTERS AND IS NOT THE DEFAULT. Both objects are Python
        # locals, so without this they are released by the garbage
        # collector at an unspecified later moment — potentially after
        # QApplication teardown has begun, which frees a context against a
        # surface that may already be gone. Unbinding and destroying them
        # here, in this order, keeps the probe's lifetime inside this
        # function where it can be reasoned about.
        ctx.doneCurrent()
        surface.destroy()


def _gl_string(fns, name: int) -> str:
    """glGetString through ctypes — PySide6 does not wrap it, and the
    renderer name is the whole basis of the software-GL check."""
    try:
        fn = fns.glGetString
    except AttributeError:                            # pragma: no cover
        return ""
    try:
        raw = fn(name)
    except Exception:                                 # pragma: no cover
        return ""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("ascii", "replace")
    if isinstance(raw, str):
        return raw
    try:                                              # pragma: no cover
        return ctypes.cast(int(raw), ctypes.c_char_p).value.decode(
            "ascii", "replace")
    except Exception:
        return ""


# ============================================================
#  THE GPU FIELD
# ============================================================
class GLAmbientField(_AmbientSimulation, QOpenGLWidget):
    """The ambient field, drawn on the GPU.

    Mirrors widgets.AmbientGlow's public surface exactly — apply_theme,
    set_radius, suspend, resume, defer, set_occluders — so PulseApp holds
    one or the other without knowing which. The simulation is inherited
    from _AmbientSimulation, shared verbatim with the raster field.
    """

    #: The orb buffer is 1/N of the widget in each axis. At 6 the field is
    #: ~216x143 on a 1300x860 window: 33x fewer pixels for a set of soft
    #: gradients whose largest per-frame change is ~3px on a ~500px
    #: falloff.
    _ORB_DIV = 6
    #: ...and is rebuilt at the raster path's cadence, not per frame. The
    #: orbs genuinely do not change faster than this; see _LAYER_MS.
    _ORB_MS = 100

    def __init__(self, parent=None):
        QOpenGLWidget.__init__(self, parent)
        _AmbientSimulation.__init__(self, gl=True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._ready = False
        self._fbo = None
        self._orb_prog = self._blit_prog = self._star_prog = None
        self._vao = None
        self._vbo = None
        self._last_orb_t = -1e9
        self._gl_frames = 0

        # THE WHOLE POINT: this renderer runs at the display's refresh
        # rate, where the raster one runs at 10Hz. Shadowing the class
        # constant per-instance is what makes the governor, _arm() and
        # resume() all pick the new base up without a second knob.
        #
        # Clamped to [30, 60]. The ceiling is deliberate — a 144Hz panel
        # gets 60, because past that the field is spending a laptop's
        # battery on motion nobody asked to be smoother. The floor keeps a
        # 24Hz projector from making the drift visibly step.
        self._INTERVAL_MS = self._refresh_interval_ms()
        self._interval = float(self._INTERVAL_MS)
        self._timer.setInterval(self._INTERVAL_MS)

    @staticmethod
    def _refresh_interval_ms() -> int:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        hz = screen.refreshRate() if screen is not None else 60.0
        if not hz or hz <= 0:
            hz = 60.0
        return int(round(1000.0 / max(30.0, min(60.0, hz))))

    # -- lifecycle: animate only while visible AND not minimized --------
    # Deliberately restated rather than hoisted into _AmbientSimulation:
    # the mixin is not a QObject and has no Qt base to forward to, so a
    # super() call from there would depend on which concrete widget class
    # it was mixed into. Four lines of duplication beats an MRO that only
    # works by luck.
    def showEvent(self, e):
        QOpenGLWidget.showEvent(self, e)
        if not self._suspended:
            self._arm()

    def hideEvent(self, e):
        QOpenGLWidget.hideEvent(self, e)
        self._timer.stop()

    def _update_exposed(self, area=None):
        """A GL surface is redrawn whole or not at all.

        The raster field clips its repaint to the exposed region because
        its cost scales with dirty area. This one's does not — the frame
        is a handful of draw calls over the whole viewport either way — and
        QOpenGLWidget has no partial-update path to hand a region to. The
        occluders still matter here, just at a different layer: star_buffer
        drops covered stars before they are ever uploaded.
        """
        self.update()

    # -- GL lifecycle -----------------------------------------
    def initializeGL(self):
        ctx = QOpenGLContext.currentContext()
        self._fns = ctx.functions()
        # EVERY GL RESOURCE BELOW MUST DIE WITH A CURRENT CONTEXT.
        #
        # Programs, buffers, VAOs and FBOs are parented to this widget, so
        # Qt destroys them when the widget goes — but by then there is no
        # context current, and freeing a GL object without one is undefined
        # behaviour. It does not raise: the process aborts during teardown,
        # which surfaces as a non-zero exit code from a test run whose
        # every assertion passed (pytest reported 24 passed and exit 9),
        # and as a crash-on-close for the user.
        #
        # aboutToBeDestroyed is the documented hook and it fires while the
        # context is still alive. Connected here rather than in __init__
        # because the context does not exist until now, and re-connected on
        # every initializeGL because Qt may recreate the context (a moved
        # window, a reset driver) without rebuilding the widget.
        ctx.aboutToBeDestroyed.connect(self._release_gl,
                                       Qt.ConnectionType.UniqueConnection)
        self._orb_prog = self._link(_FS_VERT, _ORB_FRAG, "orb")
        self._blit_prog = self._link(_FS_VERT, _BLIT_FRAG, "blit")
        self._star_prog = self._link(_STAR_VERT, _STAR_FRAG, "star")
        self._vao = QOpenGLVertexArrayObject(self)
        self._vao.create()
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
        self._ready = all((self._orb_prog, self._blit_prog, self._star_prog))

    def _link(self, vert: str, frag: str, label: str):
        prog = QOpenGLShaderProgram(self)
        prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vert)
        prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, frag)
        if not prog.link():
            # A link failure is survivable: paintGL degrades to clearing to
            # the canvas colour, which is what the shell painted before this
            # widget existed. A black window would not be survivable.
            return None
        return prog

    def _release_gl(self):
        """Free every GL object while its context is still current.

        Idempotent and defensive: it runs during teardown, where an
        exception has nowhere useful to go and would replace a clean exit
        with the abort this method exists to prevent.
        """
        self._ready = False
        try:
            self.makeCurrent()
        except Exception:                             # pragma: no cover
            return
        try:
            for prog in (self._orb_prog, self._blit_prog, self._star_prog):
                if prog is not None:
                    prog.removeAllShaders()
            if self._vbo is not None and self._vbo.isCreated():
                self._vbo.destroy()
            if self._vao is not None and self._vao.isCreated():
                self._vao.destroy()
            self._fbo = None
        except Exception:                             # pragma: no cover
            pass
        finally:
            self._orb_prog = self._blit_prog = self._star_prog = None
            self._vbo = self._vao = None
            try:
                self.doneCurrent()
            except Exception:                         # pragma: no cover
                pass

    def resizeGL(self, w: int, h: int):
        self._fbo = None            # rebuilt at the new size on next paint
        self._visible_region = None

    # -- theme ------------------------------------------------
    def apply_theme(self, t: dict):
        self._absorb_theme(t)       # shared with the raster field
        self._canvas_top = TH.to_qcolor(t["bg_grad_top"])
        self._canvas_bottom = TH.to_qcolor(t["bg_grad_bottom"])
        self.update()

    def set_radius(self, radius: int):
        # DWM rounds the window; this widget is a square opaque canvas. Kept
        # for API parity with the raster field.
        self._radius = radius

    # -- painting ---------------------------------------------
    def paintGL(self):
        self._gl_frames += 1
        w, h = max(1, self.width()), max(1, self.height())
        if not self._ready:
            c = self._canvas_bottom
            self._fns.glClearColor(c.redF(), c.greenF(), c.blueF(), 1.0)
            self._fns.glClear(0x00004000)             # GL_COLOR_BUFFER_BIT
            return

        self._vao.bind()
        self._draw_orbs(w, h)
        self._draw_stars(w, h)
        self._vao.release()

    def _ensure_fbo(self, w: int, h: int):
        want = QSize(max(1, w // self._ORB_DIV), max(1, h // self._ORB_DIV))
        if self._fbo is None or self._fbo.size() != want:
            self._fbo = QOpenGLFramebufferObject(want)
            self._last_orb_t = -1e9
        return self._fbo

    def _draw_orbs(self, w: int, h: int):
        fbo = self._ensure_fbo(w, h)
        # Rebuild the low-res orb buffer only on its own cadence. While
        # frozen (an OS move/resize loop) it is never rebuilt at all — the
        # existing texture is simply stretched, exactly as the raster path
        # stretches its cached layer.
        due = (self._t - self._last_orb_t) * 1000.0 >= self._ORB_MS
        if due and not self._frozen:
            self._last_orb_t = self._t
            fbo.bind()
            self._fns.glViewport(0, 0, fbo.width(), fbo.height())
            prog = self._orb_prog
            prog.bind()
            self._set_orb_uniforms(prog, w, h)
            self._fns.glDrawArrays(_GL_TRIANGLES, 0, 3)
            prog.release()
            fbo.release()
            self._fns.glViewport(0, 0, w, h)

        self._fns.glBindTexture(_GL_TEXTURE_2D, fbo.texture())
        self._fns.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_MIN_FILTER,
                                  _GL_LINEAR)
        self._fns.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_MAG_FILTER,
                                  _GL_LINEAR)
        self._blit_prog.bind()
        self._blit_prog.setUniformValue1i(
            self._blit_prog.uniformLocation("tex"), 0)
        self._fns.glDrawArrays(_GL_TRIANGLES, 0, 3)
        self._blit_prog.release()

    def _set_orb_uniforms(self, prog, w: int, h: int):
        from PySide6.QtGui import QVector3D
        prog.setUniformValue(prog.uniformLocation("canvas_top"),
                             QVector3D(self._canvas_top.redF(),
                                       self._canvas_top.greenF(),
                                       self._canvas_top.blueF()))
        prog.setUniformValue(prog.uniformLocation("canvas_bottom"),
                             QVector3D(self._canvas_bottom.redF(),
                                       self._canvas_bottom.greenF(),
                                       self._canvas_bottom.blueF()))
        peaks = self.orb_peaks()
        colors = self.orb_colors()
        for i, (bx, by, dspd, dph, bspd, bph, par, scale) in enumerate(
                self._orb_motion):
            dx = math.sin(self._t * dspd * math.tau + dph) * 0.06
            dy = math.cos(self._t * dspd * math.tau * 0.8 + dph) * 0.06
            dx += self._bias_x * self._POINTER_GAIN * par
            dy += self._bias_y * self._POINTER_GAIN * par
            breathe = 1.0 + 0.16 * math.sin(self._t * bspd * math.tau + bph)
            col = colors[i]
            # In LIGHT mode the raster path multiplies the orb layer onto
            # the canvas so a saturated orb DARKENS porcelain (lightening
            # near-white does nothing). Here the canvas and orbs are in one
            # pass, so the same effect is had by subtracting: the orb's
            # complement is removed rather than its colour added.
            sign = -1.0 if self._light else 1.0
            prog.setUniformValue(
                prog.uniformLocation(f"orb_color[{i}]"),
                QVector3D(sign * col.redF(), sign * col.greenF(),
                          sign * col.blueF()))
            prog.setUniformValue(
                prog.uniformLocation(f"orb_pos[{i}]"),
                QVector3D(bx + dx, by + dy, 0.625 * scale))
            prog.setUniformValue1f(
                prog.uniformLocation(f"orb_peak[{i}]"),
                peaks[i] * max(0.0, min(1.0, breathe)))

    def _draw_stars(self, w: int, h: int):
        data = self.star_buffer(w, h)
        if not data:
            return
        prog = self._star_prog
        self._fns.glEnable(_GL_BLEND)
        # Light mode's motes are DARK ink on paper, so they must subtract
        # rather than add — the same inversion the orbs make above.
        if self._light:
            self._fns.glBlendFunc(_GL_ZERO, _GL_ONE_MINUS_SRC_ALPHA)
        else:
            self._fns.glBlendFunc(_GL_SRC_ALPHA, _GL_ONE_MINUS_SRC_ALPHA)
        self._fns.glEnable(_GL_PROGRAM_POINT_SIZE)

        self._vbo.bind()
        self._vbo.allocate(data, len(data))
        prog.bind()
        prog.setUniformValue(prog.uniformLocation("res"),
                             QVector2D(float(w), float(h)))
        color = self.star_color()
        from PySide6.QtGui import QVector3D
        prog.setUniformValue(prog.uniformLocation("star_color"),
                             QVector3D(color.redF(), color.greenF(),
                                       color.blueF()))
        prog.enableAttributeArray(0)
        prog.setAttributeBuffer(0, 0x1406, 0, 4, 0)   # GL_FLOAT, vec4
        self._fns.glDrawArrays(_GL_POINTS, 0, len(data) // 16)
        prog.disableAttributeArray(0)
        prog.release()
        self._vbo.release()

        self._fns.glDisable(_GL_PROGRAM_POINT_SIZE)
        self._fns.glDisable(_GL_BLEND)

    def star_buffer(self, w: int, h: int) -> bytes:
        """The star field as packed (x_px, y_px, span_px, alpha) float4s.

        Built from the SAME _particles the raster field draws, so the two
        renderers cannot drift apart. Occluded stars are dropped here
        rather than in the shader: a star inside an opaque card's core is
        invisible either way, and not uploading it is cheaper than
        discarding it 400 fragments at a time.
        """
        span_key = "px_light" if self._light else "px_dark"
        pmax = self._STAR_PMAX["light" if self._light else "dark"]
        occluders = self._occluders
        out = bytearray()
        for pt in self._particles:
            x, y = pt["x"] * w, pt["y"] * h
            span = pt[span_key]
            if occluders:
                # The sprite's VISIBLE extent, not its full footprint. A
                # star straddling the widget edge has half its sprite off
                # the canvas, where nothing can show it — testing the raw
                # footprint left those three edge stars permanently
                # un-cullable, since no occluder can contain a rect that
                # reaches outside the widget.
                half = span / 2.0
                left = max(0.0, x - half)
                top = max(0.0, y - half)
                right = min(float(w), x + half)
                bottom = min(float(h), y + half)
                if left >= right or top >= bottom:
                    # Entirely off the canvas. The field wraps through a
                    # margin (x in [-0.03, 1.03]), so a few stars sit fully
                    # outside at any moment; they are as invisible as an
                    # occluded one and equally not worth uploading.
                    continue
                # QRect.right()/bottom() are INCLUSIVE (x + width - 1), so
                # comparing an exclusive edge against them rejects a star
                # that is covered by exactly one pixel's worth of rounding.
                # This is why the naive form left four stars permanently
                # un-cullable under a full-window occluder.
                if any(rect.x() <= left
                       and right <= rect.x() + rect.width()
                       and rect.y() <= top
                       and bottom <= rect.y() + rect.height()
                       for rect in occluders):
                    continue
            tw = 0.5 + 0.5 * math.sin(self._t * pt["tws"] * math.tau + pt["tw"])
            alpha = pmax * pt["dim"] * (0.22 + 0.78 * tw)
            out += struct.pack("<4f", x, y, float(span), alpha)
        return bytes(out)


def make_ambient_field(parent, force: str | None = None):
    """The shell's one call: hand back whichever field this machine can
    actually run, already themed by its caller.

    Returns `(widget, reason)`. The reason is logged, never shown — the
    fallback is meant to be invisible (a technician on an RDP session
    should get a smooth app, not a dialog about OpenGL).
    """
    from frontend.widgets import AmbientGlow

    if force == "raster":
        return AmbientGlow(parent), "forced raster"
    use_gpu, reason = capability() if force != "gl" else (True, "forced gl")
    if not use_gpu:
        return AmbientGlow(parent), reason
    try:
        return GLAmbientField(parent), reason
    except Exception as exc:                          # pragma: no cover
        # Constructing the widget can still fail on a driver that lied to
        # the probe. There is exactly one acceptable outcome for the app.
        return AmbientGlow(parent), f"GL widget construction failed: {exc}"
