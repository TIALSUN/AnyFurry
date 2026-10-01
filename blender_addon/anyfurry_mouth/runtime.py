"""One refresh queue; synchronize generated openings at file lifecycle boundaries."""
import time
import bpy
from bpy.app.handlers import persistent
from mathutils.bvhtree import BVHTree

_queue = {}
_observed = {}
_busy = set()
_surfaces = {}
_paused = {}
DELAY = 0.35


def modules():
    from . import eye, mouth_opening
    return eye, mouth_opening


def signature(scene):
    eye, mouth = modules()
    return eye.state(scene), mouth.opening_state(scene)


def surface(scene, shell):
    token = shell.as_pointer()
    if token in _surfaces:
        return _surfaces[token]
    evaluated = shell.evaluated_get(bpy.context.evaluated_depsgraph_get())
    try:
        mesh = evaluated.to_mesh()
        verts = [v.co.copy() for v in mesh.vertices]
        if not verts:
            raise ValueError('头壳网格为空')
        polys = [tuple(p.vertices) for p in mesh.polygons]
        result = BVHTree.FromPolygons(verts, polys, all_triangles=False), max(v.z for v in verts) + 80
    finally:
        evaluated.to_mesh_clear()
    if scene.as_pointer() in _busy:
        _surfaces[token] = result
    return result


def schedule(scene, postpone=True):
    if not hasattr(scene, 'af_project'):
        return
    token = scene.as_pointer()
    if token in _busy:
        return
    current = signature(scene)
    changed = _observed.get(token) != current
    _observed[token] = current
    if not scene.af_project.auto_refresh:
        return
    if token not in _queue or changed:
        _queue[token] = (scene.name_full, time.monotonic() + DELAY)
    if scene.af_project.fast_preview and token not in _paused:
        from .project import shell_for
        shell = shell_for(scene)
        if shell:
            entries = [(m, m.show_viewport) for m in shell.modifiers
                       if m.type == 'BOOLEAN' and m.name.startswith('AnyFurry_实体')]
            _paused[token] = entries
            for mod, _ in entries:
                mod.show_viewport = False
    if not bpy.app.timers.is_registered(run_pending):
        bpy.app.timers.register(run_pending, first_interval=DELAY)


def flush(scene, force=False):
    token = scene.as_pointer()
    if token in _busy:
        return True
    _queue.pop(token, None)
    eye, mouth = modules()
    if eye.shell_for(scene) is None:
        return True
    _busy.add(token)
    _surfaces.clear()
    resume(scene.as_pointer())
    window = bpy.context.window
    previous_scene = window.scene if window else None
    try:
        if window:
            window.scene = scene
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            success = True
            for module, state_fn, settings in ((eye, eye.state, scene.af_eye),
                                                (mouth, mouth.opening_state, scene.af_mouth_opening)):
                state = state_fn(scene)
                if force or module._last_state.get(token) != state:
                    success = module.refresh_scene(scene) and success
                elif settings.error:
                    success = False
            bpy.context.view_layer.update()
            _observed[token] = signature(scene)
            return success
    finally:
        _surfaces.clear()
        _busy.discard(token)
        if window and previous_scene is not None and window.scene != previous_scene:
            window.scene = previous_scene


def run_pending():
    now = time.monotonic()
    for token, (name, deadline) in list(_queue.items()):
        if deadline <= now:
            _queue.pop(token, None)
            scene = bpy.data.scenes.get(name)
            if scene and scene.as_pointer() == token:
                flush(scene)
    if not _queue:
        return None
    return max(0.01, min(deadline for _, deadline in _queue.values()) - time.monotonic())


def resume(token):
    for modifier, viewport in _paused.pop(token, []):
        try:
            modifier.show_viewport = viewport
        except ReferenceError:
            pass  # A file load or undo may have removed the old data block.


def clear():
    if bpy.app.timers.is_registered(run_pending):
        bpy.app.timers.unregister(run_pending)
    _queue.clear()
    for token in list(_paused):
        resume(token)
    _observed.clear()
    _surfaces.clear()
    _busy.clear()
    for module in modules():
        module._last_state.clear()


@persistent
def save_pre(_):
    for scene in bpy.data.scenes:
        if hasattr(scene, 'af_project'):
            if not flush(scene):
                scene['anyfurry_save_warning'] = '开孔更新失败：已保存可编辑参数和上次有效开孔；请重试后再导出'
            elif 'anyfurry_save_warning' in scene:
                del scene['anyfurry_save_warning']


@persistent
def load_post(_):
    clear()
    for scene in bpy.data.scenes:
        schedule(scene)


@persistent
def undo_post(_):
    # Undo restores parameters and generated data independently; reconcile once.
    load_post(None)


def draw_status(layout, scene):
    token = scene.as_pointer()
    if scene.af_eye.error or scene.af_mouth_opening.error:
        layout.label(text='更新失败 · 保留上次结果，请重试', icon='ERROR')
    elif token in _busy:
        layout.label(text='正在计算开孔…', icon='TIME')
    elif token in _queue:
        text = '快速预览 · 停止后恢复开孔' if token in _paused else '等待拖动结束后更新…'
        layout.label(text=text, icon='TIME')
    else:
        eye, mouth = modules()
        if (eye._last_state.get(token), mouth._last_state.get(token)) != signature(scene):
            layout.label(text='参数已修改 · 点击更新', icon='INFO')
        else:
            layout.label(text='开孔已更新', icon='CHECKMARK')


def register():
    for collection, handler in ((bpy.app.handlers.save_pre, save_pre),
                                (bpy.app.handlers.load_post, load_post),
                                (bpy.app.handlers.undo_post, undo_post),
                                (bpy.app.handlers.redo_post, undo_post)):
        if handler not in collection:
            collection.append(handler)


def unregister():
    clear()
    for collection, handler in ((bpy.app.handlers.save_pre, save_pre),
                                (bpy.app.handlers.load_post, load_post),
                                (bpy.app.handlers.undo_post, undo_post),
                                (bpy.app.handlers.redo_post, undo_post)):
        if handler in collection:
            collection.remove(handler)
