"""AnyFurry symmetric physical eye-opening controls."""
import json
import math
import time
import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, PointerProperty, StringProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .recovery import OpeningTransaction, show_error, set_error
from . import runtime, project

SPACING_MM = 10.0
PRESET_SPACING_OFFSET_MM = 6.0
WIDTH_PCT = 15.0
HEIGHT_PCT = 15.0
POSITION_MM = 12.0
ANGLE_DEG = 15.0
PRESET_Y_OFFSET_RATIO = -5.0 / 6.0
CUTTER_OUTSIDE_MM = 120.0
CUTTER_INSIDE_MM = 45.0
CUTTER_COLLECTION = 'AnyFurry_实体眼孔切刀'
BOOLEAN_PREFIX = 'AnyFurry_实体眼孔_'
DEBOUNCE_SECONDS = 0.20
_refreshing = set()
_last_state = {}
_pending_refresh = {}


def shell_for(scene):
    return project.shell_for(scene)


def eye_objects(scene):
    return [o for o in scene.objects if o.type == 'CURVE' and o.get('anyfurry_eye_preset')]


def cutter_objects(scene):
    return [o for o in scene.objects if o.type == 'MESH' and o.get('anyfurry_eye_cutter')]


def eye_boolean_modifiers(shell):
    return [m for m in shell.modifiers if m.type == 'BOOLEAN' and m.name.startswith(BOOLEAN_PREFIX)]


def shape_value(shell, name):
    keys = shell.data.shape_keys
    return keys.key_blocks[name].value if keys and name in keys.key_blocks else 0.0


def state(scene):
    shell = shell_for(scene)
    if not shell or not hasattr(scene, 'af_eye'):
        return None
    p = scene.af_eye
    return (p.preset, bool(p.openings_enabled), round(p.spacing, 6),
            round(p.width, 6), round(p.height, 6), round(p.position, 6),
            round(p.angle, 6), round(shape_value(shell, '鼻嘴上下位置'), 6), round(shape_value(shell, '整体宽度'), 6),
            round(shape_value(shell, '整体高度'), 6),
            tuple((key.name, round(key.value, 6)) for key in shell.data.shape_keys.key_blocks if key.name != 'Basis'),
            tuple(round(v, 6) for row in shell.matrix_world for v in row))


def cutter_for(scene, side):
    existing = next((o for o in cutter_objects(scene)
                     if o.get('anyfurry_eye_cutter') == side), None)
    if existing:
        return existing
    collection = next((c for c in scene.collection.children if c.name.startswith(CUTTER_COLLECTION)), None)
    if collection is None:
        collection = bpy.data.collections.new(CUTTER_COLLECTION)
        scene.collection.children.link(collection)
    mesh = bpy.data.meshes.new(f'实体眼孔切刀_{side}_网格')
    obj = bpy.data.objects.new(f'实体眼孔切刀_{side}', mesh)
    collection.objects.link(obj)
    obj['anyfurry_eye_cutter'] = side
    obj['cutter_outside_mm'] = CUTTER_OUTSIDE_MM
    obj['cutter_inside_mm'] = CUTTER_INSIDE_MM
    obj.display_type = 'WIRE'
    obj.hide_render = True
    obj.hide_set(True)
    return obj


def update_cutter(obj, coords, matrix_world):
    count = len(coords)
    if count < 3:
        return
    bottom = [(x, y, z - CUTTER_INSIDE_MM) for x, y, z in coords]
    top = [(x, y, z + CUTTER_OUTSIDE_MM) for x, y, z in coords]
    verts = bottom + top
    faces = [tuple(reversed(range(count))), tuple(range(count, count * 2))]
    for index in range(count):
        nxt = (index + 1) % count
        faces.append((index, nxt, count + nxt, count + index))
    mesh = obj.data
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    cutter_bm = bmesh.new()
    cutter_bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(cutter_bm, faces=list(cutter_bm.faces))
    cutter_bm.to_mesh(mesh)
    cutter_bm.free()
    mesh.update(calc_edges=True)
    obj.matrix_world = matrix_world
    obj['point_count'] = count


def ensure_boolean(shell, cutter, side):
    name = BOOLEAN_PREFIX + side
    modifier = shell.modifiers.get(name)
    if modifier is None:
        modifier = shell.modifiers.new(name=name, type='BOOLEAN')
    modifier.operation = 'DIFFERENCE'
    modifier.solver = 'EXACT'
    modifier.object = cutter
    modifier.show_render = True
    return modifier


def _refresh_scene(scene):
    token = scene.as_pointer()
    if token in _refreshing:
        return
    _pending_refresh.pop(token, None)
    shell = shell_for(scene)
    objects = eye_objects(scene)
    if not shell or not objects or not hasattr(scene, 'af_eye'):
        return
    _refreshing.add(token)
    evaluated = None
    modifiers = eye_boolean_modifiers(shell)
    unrelated = [(m, m.show_viewport, m.show_render) for m in shell.modifiers
                 if m.type == "BOOLEAN" and m.name.startswith("AnyFurry_实体") and m not in modifiers]
    previous_visibility = [(m, m.show_viewport, m.show_render) for m in modifiers]
    try:
        p = scene.af_eye
        # The physical hole is the preview. Construction outlines stay hidden.
        for obj in objects:
            obj.hide_viewport = True
            obj.hide_render = True
            obj.hide_set(True)

        # Evaluate the uncut deforming shell so projection never samples an old hole.
        for modifier, _, _ in previous_visibility + unrelated:
            modifier.show_viewport = False
            modifier.show_render = False
        if bpy.context.view_layer:
            bpy.context.view_layer.update()
        bvh, z_start = runtime.surface(scene, shell)

        overall_width = 1.0 + .10 * shape_value(shell, '整体宽度')
        overall_height = 1.0 + .10 * shape_value(shell, '整体高度')
        center_y_global = float(shell.get('overall_scale_center_y_mm', 0.0))
        eye_width = 1.0 + WIDTH_PCT / 100.0 * p.width
        eye_height = 1.0 + HEIGHT_PCT / 100.0 * p.height
        angle = math.radians(-ANGLE_DEG * p.angle)
        cosine, sine = math.cos(angle), math.sin(angle)

        by_preset = {}
        for obj in objects:
            by_preset.setdefault(obj.get('anyfurry_eye_preset'), {})[obj.get('anyfurry_eye_side')] = obj
        active_coords = {}
        for preset, pair in by_preset.items():
            if preset != p.preset:
                continue
            left_obj = pair.get('L')
            right_obj = pair.get('R')
            if not left_obj or not right_obj:
                continue
            base = json.loads(left_obj['anyfurry_eye_base_xy'])
            xs = [co[0] for co in base]
            ys = [co[1] for co in base]
            base_cx = (min(xs) + max(xs)) * .5
            base_cy = (min(ys) + max(ys)) * .5
            center_abs = abs(base_cx) * overall_width + PRESET_SPACING_OFFSET_MM + p.spacing * SPACING_MM
            transformed_cy = center_y_global + (base_cy - center_y_global) * overall_height
            preset_y_offset = PRESET_Y_OFFSET_RATIO * (max(ys) - min(ys)) * overall_height
            left_xy = []
            for x, y in base:
                dx = (x - base_cx) * overall_width * eye_width
                dy = (y - base_cy) * overall_height * eye_height
                left_xy.append((
                    -center_abs + dx * cosine - dy * sine,
                    transformed_cy + preset_y_offset + p.position * POSITION_MM + dx * sine + dy * cosine,
                ))
            right_xy = [(-x, y) for x, y in left_xy]
            left_points = left_obj.data.splines[0].points
            right_points = right_obj.data.splines[0].points
            misses = 0
            left_cut = []
            right_cut = []
            for lp, rp, (lx, ly), (rx, ry) in zip(left_points, right_points, left_xy, right_xy):
                lh = bvh.ray_cast(Vector((lx, ly, z_start)), Vector((0, 0, -1)))[0]
                rh = bvh.ray_cast(Vector((rx, ry, z_start)), Vector((0, 0, -1)))[0]
                if lh is None or rh is None:
                    misses += 1
                    continue
                surface_z = max(lh.z, rh.z)
                lp.co = (lx, ly, surface_z + 3.0, 1.0)
                rp.co = (rx, ry, surface_z + 3.0, 1.0)
                left_cut.append((lx, ly, surface_z))
                right_cut.append((rx, ry, surface_z))
            left_obj['anyfurry_eye_projection_misses'] = misses
            right_obj['anyfurry_eye_projection_misses'] = misses
            if preset == p.preset and misses == 0:
                active_coords = {'L': left_cut, 'R': right_cut}

        if p.openings_enabled and len(active_coords) != 2:
            raise ValueError('当前眼型超出头壳表面，调整位置或尺寸后重试')

        for side in ('L', 'R'):
            cutter = cutter_for(scene, side)
            if side in active_coords:
                update_cutter(cutter, active_coords[side], shell.matrix_world)
            modifier = ensure_boolean(shell, cutter, side)
            modifier.show_viewport = bool(p.openings_enabled and side in active_coords)
            modifier.show_render = bool(p.openings_enabled and side in active_coords)
        shell['eye_opening_mode'] = 'live_boolean_difference'
        shell['eye_opening_cutter_depth_mm'] = CUTTER_INSIDE_MM
        shell['eye_opening_outline_visible'] = False
        _last_state[token] = state(scene)
    finally:
        for modifier, viewport, render in unrelated:
            modifier.show_viewport = viewport
            modifier.show_render = render
        if evaluated is not None:
            evaluated.to_mesh_clear()
        # New modifiers use the requested state. Restore only unrelated pre-existing entries on failure paths.
        if not hasattr(scene, 'af_eye'):
            for modifier, viewport, render in previous_visibility:
                modifier.show_viewport = viewport
                modifier.show_render = render
        _refreshing.discard(token)


def refresh_scene(scene):
    shell = shell_for(scene)
    if shell is None or not hasattr(scene, 'af_eye') or not eye_objects(scene):
        return False
    if scene.as_pointer() in _refreshing:
        return True
    settings = scene.af_eye
    try:
        with OpeningTransaction(scene, shell):
            _refresh_scene(scene)
    except Exception as error:
        set_error(settings, error)
        # Do not repeatedly retry an unchanged failing request in the handler.
        _last_state[scene.as_pointer()] = state(scene)
        return False
    settings.error = ''
    return True


def schedule_refresh(scene, postpone=True):
    runtime.schedule(scene, postpone=postpone)


def force_refresh(scene):
    return runtime.flush(scene, force=True)


def refresh(self, context):
    schedule_refresh(self.id_data, postpone=True)


class AFEyeSettings(bpy.types.PropertyGroup):
    error: StringProperty(options={'SKIP_SAVE'})
    preset: EnumProperty(
        name='眼型预设',
        items=(('A', '1号', '设计稿1号眼型'), ('B', '2号', '设计稿2号眼型'), ('C', '3号', '设计稿3号眼型')),
        default='A', update=refresh)
    openings_enabled: BoolProperty(
        name='实体眼孔', default=True, update=refresh,
        description='直接在头壳上实时生成左右实体眼孔')
    spacing: FloatProperty(name='眼睛左右位置（眼距）', default=0, min=-1, max=1, update=refresh,
                           description='左右眼镜像地向内或向外移动，范围每侧 ±10 mm')
    width: FloatProperty(name='眼孔宽度', default=0, min=-1, max=1, update=refresh,
                         description='保持当前预设轮廓特征，宽度范围 ±15%')
    height: FloatProperty(name='眼孔高度', default=0, min=-1, max=1, update=refresh,
                          description='保持当前预设轮廓特征，高度范围 ±15%')
    position: FloatProperty(name='眼睛上下位置', default=0, min=-1, max=1, update=refresh,
                            description='左右眼同步上下移动，范围 ±12 mm')
    angle: FloatProperty(name='眼睛角度', default=0, min=-1, max=1, update=refresh,
                         description='负值外眼角下垂，正值外眼角上扬，范围 ±15°')


def controls(layout, scene):
    box = layout.box()
    show_error(box, scene.af_eye)
    box.label(text='实体眼孔')
    box.prop(scene.af_eye, 'openings_enabled', text='显示实体眼孔', toggle=True, icon='MOD_BOOLEAN')
    row = box.row(align=True)
    row.prop(scene.af_eye, 'preset', expand=True)
    box.separator()
    box.label(text='眼孔微调')
    box.prop(scene.af_eye, 'spacing', text='眼睛左右位置（眼距）', slider=True)
    box.prop(scene.af_eye, 'width', text='眼孔宽度', slider=True)
    box.prop(scene.af_eye, 'height', text='眼孔高度', slider=True)
    box.prop(scene.af_eye, 'position', text='眼睛上下位置', slider=True)
    box.prop(scene.af_eye, 'angle', text='眼睛角度', slider=True)
    p = scene.af_eye
    box.label(text='每侧眼距偏移 %+.1f mm · 高度偏移 %+.1f mm' % (p.spacing * SPACING_MM, p.position * POSITION_MM))
    box.label(text='眼孔宽/高 %+.0f%% / %+.0f%% · 角度 %+.1f°' % (p.width * WIDTH_PCT, p.height * HEIGHT_PCT, p.angle * ANGLE_DEG))
    row = box.row(align=True)
    row.operator('anyfurry.refresh_eye_openings', text='立即刷新实体孔', icon='FILE_REFRESH')
    row.operator('anyfurry.reset_eyes', text='恢复基础值', icon='LOOP_BACK')
    box.label(text='自动更新开启时，停止调节后更新', icon='INFO')


class AF_OT_refresh_eye_openings(bpy.types.Operator):
    bl_idname = 'anyfurry.refresh_eye_openings'
    bl_label = '立即刷新实体孔'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not force_refresh(context.scene):
            self.report({'ERROR'}, context.scene.af_eye.error)
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_reset_eyes(bpy.types.Operator):
    bl_idname = 'anyfurry.reset_eyes'
    bl_label = '恢复眼部基础值'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.af_eye
        p.preset = 'A'
        p.spacing = 0
        p.width = 0
        p.height = 0
        p.position = 0
        p.angle = 0
        p.openings_enabled = True
        if not force_refresh(context.scene):
            self.report({'ERROR'}, context.scene.af_eye.error)
            return {'CANCELLED'}
        return {'FINISHED'}


@persistent
def sync_eye_openings(scene, depsgraph):
    token = scene.as_pointer()
    if token in _refreshing or token in runtime._busy:
        return
    current = state(scene)
    if current is not None and _last_state.get(token) != current:
        schedule_refresh(scene, postpone=False)


CLASSES = (AFEyeSettings, AF_OT_refresh_eye_openings, AF_OT_reset_eyes)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.af_eye = PointerProperty(type=AFEyeSettings)
    if sync_eye_openings not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(sync_eye_openings)


def unregister():
    _pending_refresh.clear()
    _last_state.clear()
    if sync_eye_openings in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(sync_eye_openings)
    if hasattr(bpy.types.Scene, 'af_eye'):
        del bpy.types.Scene.af_eye
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)








