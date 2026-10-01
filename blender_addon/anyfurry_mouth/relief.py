"""Symmetric, surface-following relief holes with explicit generation."""
import hashlib
import json
import math
import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform
from . import project, runtime, eye, mouth_opening
from .recovery import OpeningTransaction, set_error, show_error

BOOLEAN_NAME = 'AnyFurry_实体减重孔'
SEGMENTS = 48
DEFAULTS = {'enabled': False, 'diameter_mm': 18.0, 'clearance_mm': 8.0,
            'density': 'MEDIUM', 'forehead': True, 'top': True, 'sides': True}
_last_state = {}
_basis_cache = {}
_plans = {}

# Nested templates keep the sparse pattern when increasing density. These are
# design guides on the approved Basis; actual placement uses deformed vertices.
SPARSE = [('forehead', 24, 60), ('forehead', 82, 60), ('forehead', 54, 85),
          ('top', 24, 110), ('top', 78, 105), ('sides', 94, -64)]
MEDIUM = SPARSE + [('forehead', 54, 60), ('forehead', 24, 85), ('forehead', 82, 85),
                   ('top', 54, 110), ('top', 24, 128), ('top', 50, 126),
                   ('sides', 104, 42), ('sides', 84, -98)]
DENSE = MEDIUM + [('forehead', 22, 39), ('forehead', 52, 39), ('forehead', 100, 76),
                  ('top', 20, 141), ('top', 45, 139), ('top', 70, 125),
                  ('sides', 107, 78), ('sides', 100, -45), ('sides', 98, -88),
                  ('sides', 70, -123)]


def design_state(scene):
    shell = project.shell_for(scene)
    if not shell or not hasattr(scene, 'af_relief'):
        return None
    p = scene.af_relief
    return (tuple((name, round(getattr(p, name), 6) if type(getattr(p, name)) is float
                   else getattr(p, name)) for name in DEFAULTS),
            eye.state(scene), mouth_opening.opening_state(scene),
            round(scene.unit_settings.scale_length, 9))


def state(scene):
    value = design_state(scene)
    return (value, bool(scene.af_relief.preview)) if value is not None else None


def fingerprint(scene):
    return hashlib.sha256(json.dumps(design_state(scene), separators=(',', ':')).encode('utf8')).hexdigest()


def objects(scene, kind):
    return [o for o in scene.objects if o.type == 'MESH' and o.get(kind)]


def mesh_object(scene, kind):
    found = objects(scene, kind)
    if found:
        return found[0]
    collection = next((c for c in scene.collection.children if c.name.startswith('AnyFurry_减重孔工具')), None)
    if collection is None:
        collection = bpy.data.collections.new('AnyFurry_减重孔工具')
        scene.collection.children.link(collection)
    name = '减重孔_孔位预览' if kind == 'anyfurry_relief_preview' else '减重孔_实体切刀'
    obj = bpy.data.objects.new(name, bpy.data.meshes.new(name + '_网格'))
    collection.objects.link(obj)
    obj[kind] = True
    obj.hide_render = True
    obj.hide_select = True
    obj.display_type = 'WIRE'
    if kind == 'anyfurry_relief_preview':
        material = bpy.data.materials.get('AnyFurry_孔位预览')
        if material is None:
            material = bpy.data.materials.new('AnyFurry_孔位预览')
            material.diffuse_color = (1.0, .38, .03, 1.0)
        obj.data.materials.append(material)
    obj.hide_set(True)
    return obj


def modifier_for(shell):
    return shell.modifiers.get(BOOLEAN_NAME)


def ready(scene):
    p = scene.af_relief
    if not p.enabled:
        return True
    shell = project.shell_for(scene)
    modifier = modifier_for(shell) if shell else None
    return bool(not p.error and modifier and modifier.object and modifier.show_render
                and p.applied_signature == fingerprint(scene))


def changed(self, context):
    scene = self.id_data
    self.error = ''
    shell = project.shell_for(scene)
    mod = modifier_for(shell) if shell else None
    if mod and (not self.enabled or self.applied_signature != fingerprint(scene)):
        mod.show_viewport = mod.show_render = False
    if not self.enabled or not self.preview:
        for obj in objects(scene, 'anyfurry_relief_preview'):
            obj.hide_set(True)
    runtime.schedule(scene)


def mm_factor(scene, shell):
    lengths = [shell.matrix_world.to_3x3().col[i].length for i in range(3)]
    if min(lengths) <= 0 or max(lengths) - min(lengths) > max(lengths) * 1e-5:
        raise ValueError('减重孔需要对象等比例缩放；请恢复缩放后重试')
    columns = shell.matrix_world.to_3x3()
    if any(abs(columns.col[a].normalized().dot(columns.col[b].normalized())) > 1e-5
           for a, b in ((0, 1), (0, 2), (1, 2))):
        raise ValueError('减重孔不支持对象剪切变换')
    value = scene.unit_settings.scale_length * 1000 * lengths[0]
    if value <= 0:
        raise ValueError('场景单位无效')
    return value


def basis_data(shell):
    key = shell.data.as_pointer()
    if key in _basis_cache:
        return _basis_cache[key]
    mesh = shell.data
    coords = [v.co.copy() for v in mesh.shape_keys.key_blocks['Basis'].data]
    mesh.calc_loop_triangles()
    triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    tree = BVHTree.FromPolygons(coords, triangles, all_triangles=True)
    # These two cut planes belong to the approved final model. Store their
    # original vertex IDs, then move the protected rim with every shape key.
    rear = [i for i, v in enumerate(coords)
            if abs(v.z + 96.9) < .002 or abs(v.z + .4573263 * v.y + 80.3) < .002]
    if not rear:
        raise ValueError('无法识别最终版的后沿保护区域')
    rear_ids = set(rear)
    cap_faces = [t for t in triangles if all(i in rear_ids for i in t)]
    if not cap_faces:
        raise ValueError('后沿保护面缺失')
    data = coords, triangles, tree, rear, max(v.z for v in coords) + 80, cap_faces
    _basis_cache[key] = data
    return data


def deformed_vertex(shell, index):
    keys = shell.data.shape_keys.key_blocks
    value = keys['Basis'].data[index].co.copy()
    for key in keys:
        if key.name != 'Basis' and key.value:
            value += (key.data[index].co - key.relative_key.data[index].co) * key.value
    return value


def anchor(shell, x, y):
    coords, triangles, tree, _, z_start, _ = basis_data(shell)
    hit, normal, index, _ = tree.ray_cast(Vector((x, y, z_start)), Vector((0, 0, -1)))
    if hit is None or normal.z <= .08:
        return None
    ids = triangles[index]
    return barycentric_transform(hit, *(coords[i] for i in ids),
                                  *(deformed_vertex(shell, i) for i in ids))


def opening_polygons(scene):
    result = []
    if scene.af_eye.openings_enabled:
        for obj in eye.eye_objects(scene):
            if obj.get('anyfurry_eye_preset') == scene.af_eye.preset:
                result.append([(point.co.x, point.co.y) for point in obj.data.splines[0].points])
    if scene.af_mouth_opening.enabled:
        for obj in scene.objects:
            if obj.get('anyfurry_mouth_cutter'):
                count = int(obj.get('point_count', 0))
                result.append([(v.co.x, v.co.y) for v in list(obj.data.vertices)[:count]])
    return [polygon for polygon in result if len(polygon) >= 3]


def distance_to_polygon(x, y, polygon):
    inside = False
    best = float('inf')
    for i, (ax, ay) in enumerate(polygon):
        bx, by = polygon[(i + 1) % len(polygon)]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
        dx, dy = bx - ax, by - ay
        t = max(0., min(1., ((x - ax) * dx + (y - ay) * dy) / max(1e-12, dx * dx + dy * dy)))
        best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
    return 0. if inside else best


def circle_frame(normal):
    guide = Vector((0, 1, 0)) if abs(normal.y) < .9 else Vector((1, 0, 0))
    u = guide.cross(normal).normalized()
    return u, normal.cross(u).normalized()


def clear_of_openings(point, gap, protected):
    for polygon, bounds in protected:
        dx = max(bounds[0] - point.x, 0., point.x - bounds[1])
        dy = max(bounds[2] - point.y, 0., point.y - bounds[3])
        if math.hypot(dx, dy) < gap and distance_to_polygon(point.x, point.y, polygon) < gap:
            return False
    return True


def plan_holes(scene, shell):
    p = scene.af_relief
    factor = mm_factor(scene, shell)
    radius, gap = p.diameter_mm / (2 * factor), p.clearance_mm / factor
    modifiers = [(m, m.show_viewport, m.show_render) for m in shell.modifiers
                 if m.type == 'BOOLEAN' and m.name.startswith('AnyFurry_实体')]
    try:
        for mod, _, _ in modifiers:
            mod.show_viewport = mod.show_render = False
        bpy.context.view_layer.update()
        tree, z_start = runtime.surface(scene, shell)
        _, _, _, rear_ids, _, cap_faces = basis_data(shell)
        rear_index = {index: i for i, index in enumerate(rear_ids)}
        rear = BVHTree.FromPolygons([deformed_vertex(shell, index) for index in rear_ids],
                                   [tuple(rear_index[i] for i in face) for face in cap_faces], all_triangles=True)
        protected = opening_polygons(scene)
        bounded = [(poly, (min(x for x, y in poly), max(x for x, y in poly),
                          min(y for x, y in poly), max(y for x, y in poly))) for poly in protected]
        template = {'SPARSE': SPARSE, 'MEDIUM': MEDIUM, 'DENSE': DENSE}[p.density]
        accepted, rejected = [], 0
        pad = 2.0 / factor
        for region, base_x, base_y in template:
            if not getattr(p, region):
                continue
            positive, negative = anchor(shell, base_x, base_y), anchor(shell, -base_x, base_y)
            if positive is None or negative is None:
                rejected += 2
                continue
            x, y = (abs(positive.x) + abs(negative.x)) * .5, (positive.y + negative.y) * .5
            # Preserve the facial midline and use a conservative XY clearance
            # around active eyes/mouth, including their actual current settings.
            if x < radius + gap or any(distance_to_polygon(sign*x, y, polygon) < radius + gap
                                       for sign in (-1, 1) for polygon in protected):
                rejected += 2
                continue
            hp, np, _, _ = tree.ray_cast(Vector((x, y, z_start)), Vector((0, 0, -1)))
            hn, nn, _, _ = tree.ray_cast(Vector((-x, y, z_start)), Vector((0, 0, -1)))
            if hp is None or hn is None or min(np.z, nn.z) <= .12:
                rejected += 2
                continue
            center = Vector((x, y, (hp.z + hn.z) * .5))
            normal = (np + Vector((-nn.x, nn.y, nn.z))).normalized()
            u, v = circle_frame(normal)
            rings, near, far, okay = [], -1e9, 1e9, True
            for sign in (1, -1):
                mirror = lambda point: Vector((sign * point.x, point.y, point.z))
                cn, nr = mirror(center), mirror(normal)
                samples = [cn] + [mirror(center + radius * (u * math.cos(2*math.pi*i/SEGMENTS)
                                                         + v * math.sin(2*math.pi*i/SEGMENTS)))
                                 for i in range(SEGMENTS)]
                ring = []
                for sample_index, sample in enumerate(samples):
                    outer, n_outer, _, _ = tree.ray_cast(sample + nr * (25/factor), -nr, 50/factor)
                    if outer is None or n_outer.dot(nr) < .35:
                        okay = False
                        break
                    inner, n_inner, _, thick = tree.ray_cast(outer - nr*(.03/factor), -nr, 20/factor)
                    if inner is None or n_inner.dot(nr) > -.2 or thick < .05/factor:
                        okay = False
                        break
                    if rear.find_nearest(outer)[3] < gap or rear.find_nearest(inner)[3] < gap:
                        okay = False
                        break
                    if not clear_of_openings(outer, gap, bounded) or not clear_of_openings(inner, gap, bounded):
                        okay = False
                        break
                    if sample_index == 0 and any((cn - old['center']).length < radius * 2 + gap + pad for old in accepted):
                        okay = False
                        break
                    near = max(near, (outer - cn).dot(nr) + pad)
                    far = min(far, (inner - cn).dot(nr) - pad)
                    if sample_index:
                        ring.append(outer + nr * (.6/factor))
                if not okay:
                    break
                rings.append(ring)
            if not okay or near - far > 24/factor:
                rejected += 2
                continue
            # Pair uses exactly mirrored centers, directions, radius and depth.
            for index, sign in enumerate((1, -1)):
                mirror = lambda point: Vector((sign * point.x, point.y, point.z))
                accepted.append({'center': mirror(center), 'normal': mirror(normal),
                                 'u': mirror(u), 'v': mirror(v), 'radius': radius,
                                 'near': near, 'far': far, 'preview': rings[index], 'region': region})
        return {'holes': accepted, 'rejected': rejected, 'factor': factor}
    finally:
        for mod, viewport, render in modifiers:
            mod.show_viewport, mod.show_render = viewport, render
        bpy.context.view_layer.update()


def update_preview(scene, shell, plan):
    obj = mesh_object(scene, 'anyfurry_relief_preview')
    vertices, edges = [], []
    for hole in plan['holes']:
        offset = len(vertices)
        ring = hole['preview']
        vertices.extend(tuple(point) for point in ring)
        edges.extend((offset+i, offset+(i+1) % len(ring)) for i in range(len(ring)))
    obj.data.clear_geometry()
    obj.data.from_pydata(vertices, edges, [])
    obj.data.update()
    obj.matrix_world = shell.matrix_world
    obj.hide_viewport = False
    obj.hide_set(not scene.af_relief.preview or ready(scene))


def refresh_scene(scene):
    shell = project.shell_for(scene)
    if shell is None:
        return False
    p = scene.af_relief
    try:
        if not p.enabled:
            mod = modifier_for(shell)
            if mod:
                mod.show_viewport = mod.show_render = False
            for obj in objects(scene, 'anyfurry_relief_preview'):
                obj.hide_set(True)
            p.planned_count = p.skipped_count = 0
        else:
            mod = modifier_for(shell)
            if mod and p.applied_signature != fingerprint(scene):
                mod.show_viewport = mod.show_render = False
            with OpeningTransaction(scene, shell):
                plan = plan_holes(scene, shell)
                _plans[scene.as_pointer()] = plan
                p.planned_count, p.skipped_count = len(plan['holes']), plan['rejected']
                mod = modifier_for(shell)
                current = bool(mod and mod.object and p.applied_signature == fingerprint(scene))
                if mod:
                    mod.show_viewport = mod.show_render = current
                update_preview(scene, shell, plan)
    except Exception as error:
        if p.applied_signature != fingerprint(scene):
            for obj in objects(scene, 'anyfurry_relief_preview'):
                obj.hide_set(True)
        set_error(p, error)
        _last_state[scene.as_pointer()] = state(scene)
        return False
    p.error = ''
    _last_state[scene.as_pointer()] = state(scene)
    return True


def update_cutter(obj, shell, plan):
    vertices, faces = [], []
    for hole in plan['holes']:
        offset = len(vertices)
        for depth in (hole['far'], hole['near']):
            for i in range(SEGMENTS):
                t = 2*math.pi*i/SEGMENTS
                point = hole['center'] + hole['normal']*depth + hole['radius']*(hole['u']*math.cos(t) + hole['v']*math.sin(t))
                vertices.append(tuple(point))
        faces.extend((tuple(offset+i for i in reversed(range(SEGMENTS))),
                      tuple(offset+SEGMENTS+i for i in range(SEGMENTS))))
        for i in range(SEGMENTS):
            j = (i+1) % SEGMENTS
            faces.append((offset+i, offset+j, offset+SEGMENTS+j, offset+SEGMENTS+i))
    obj.data.clear_geometry()
    obj.data.from_pydata(vertices, [], faces)
    obj.data.update()
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        bm.to_mesh(obj.data)
    finally:
        bm.free()
    obj.data.update()
    obj.matrix_world = shell.matrix_world
    obj['hole_count'] = len(plan['holes'])
    obj.hide_set(True)


def geometry_volume(shell, check=False):
    bpy.context.view_layer.update()
    evaluated = shell.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if not bm.faces or any(not edge.is_manifold for edge in bm.edges):
            raise ValueError('减重孔结果有非闭合几何，已保留上次有效结果')
        if check:
            seen = set()
            for vertex in bm.verts:
                if vertex in seen:
                    continue
                if seen:
                    raise ValueError('减重孔切出了分离部分，请减小孔径或增加间距')
                stack = [vertex]
                seen.add(vertex)
                while stack:
                    current = stack.pop()
                    for edge in current.link_edges:
                        other = edge.other_vert(current)
                        if other not in seen:
                            seen.add(other)
                            stack.append(other)
        volume = bm.calc_volume(signed=True)
        if volume <= 0:
            raise ValueError('减重孔结果体积异常')
        return volume
    finally:
        bm.free()
        evaluated.to_mesh_clear()


def generate(scene):
    p, shell = scene.af_relief, project.shell_for(scene)
    if not p.enabled:
        set_error(p, ValueError('请先启用减重孔'))
        return False
    if not runtime.flush(scene, force=True):
        return False
    plan = _plans.get(scene.as_pointer())
    if not plan or not plan['holes']:
        set_error(p, ValueError('没有可生成的孔位，请选择区域、减小孔径或间距'))
        return False
    try:
        with OpeningTransaction(scene, shell):
            mod = modifier_for(shell)
            if mod:
                mod.show_viewport = mod.show_render = False
            base_volume = geometry_volume(shell)
            cutter = mesh_object(scene, 'anyfurry_relief_cutter')
            update_cutter(cutter, shell, plan)
            if mod is None:
                mod = shell.modifiers.new(BOOLEAN_NAME, 'BOOLEAN')
            mod.operation, mod.solver, mod.object = 'DIFFERENCE', 'EXACT', cutter
            mod.show_viewport = mod.show_render = True
            volume = geometry_volume(shell, check=True)
            if volume >= base_volume:
                raise ValueError('减重孔未穿透壳壁，请调整参数后重试')
        p.applied_signature = fingerprint(scene)
        p.generated_count = len(plan['holes'])
        p.volume_reduction = (base_volume - volume) / base_volume * 100
        p.error = ''
        for obj in objects(scene, 'anyfurry_relief_preview'):
            obj.hide_set(True)
        _last_state[scene.as_pointer()] = state(scene)
        return True
    except Exception as error:
        set_error(p, error)
        _last_state[scene.as_pointer()] = state(scene)
        return False


def clear():
    _last_state.clear()
    _plans.clear()
    _basis_cache.clear()


class AFReliefSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name='启用减重孔', default=False, update=changed)
    preview: BoolProperty(name='显示孔位预览', default=True, update=changed)
    diameter_mm: FloatProperty(name='孔径（mm）', default=18, min=8, max=26, update=changed)
    clearance_mm: FloatProperty(name='保留间距（mm）', default=8, min=4, max=20, update=changed,
                                description='孔间、后沿和眼嘴孔附近的设计避让距离；不是材料强度保证')
    density: EnumProperty(name='疏密', default='MEDIUM', update=changed,
                          items=(('SPARSE', '疏', '较少孔位'), ('MEDIUM', '中', '基础孔位'), ('DENSE', '密', '增加候选孔位；不符合间距的仍会跳过')))
    forehead: BoolProperty(name='额头', default=True, update=changed)
    top: BoolProperty(name='顶部', default=True, update=changed)
    sides: BoolProperty(name='两侧', default=True, update=changed)
    applied_signature: StringProperty(options={'HIDDEN'})
    planned_count: IntProperty(options={'SKIP_SAVE'})
    skipped_count: IntProperty(options={'SKIP_SAVE'})
    generated_count: IntProperty()
    volume_reduction: FloatProperty()
    error: StringProperty(options={'SKIP_SAVE'})


class AF_OT_generate_relief(project.HeadOperator, bpy.types.Operator):
    bl_idname = 'anyfurry.generate_relief'
    bl_label = '生成 / 更新减重孔'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not generate(context.scene):
            self.report({'ERROR'}, context.scene.af_relief.error or '眼嘴孔更新失败，请先重试')
            return {'CANCELLED'}
        self.report({'INFO'}, '已生成 %d 个对称减重孔' % context.scene.af_relief.generated_count)
        return {'FINISHED'}


class AF_OT_reset_relief(project.HeadOperator, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_relief'
    bl_label = '恢复减重孔默认值'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for name, value in DEFAULTS.items():
            setattr(context.scene.af_relief, name, value)
        context.scene.af_relief.preview = True
        return {'FINISHED'}


def controls(layout, scene):
    p = scene.af_relief
    layout.prop(p, 'enabled', toggle=True)
    column = layout.column()
    column.enabled = p.enabled
    row = column.row(align=True)
    for name in ('forehead', 'top', 'sides'):
        row.prop(p, name, toggle=True)
    column.prop(p, 'diameter_mm', slider=True)
    column.prop(p, 'clearance_mm', slider=True)
    column.prop(p, 'density', expand=True)
    column.prop(p, 'preview')
    if p.enabled and ready(scene):
        column.label(text='已生成 %d 孔 · 实体体积减少约 %.1f%%' % (p.generated_count, p.volume_reduction), icon='CHECKMARK')
    elif p.enabled:
        column.label(text='可用孔位 %d · 自动避让 %d' % (p.planned_count, p.skipped_count))
        column.label(text='仅孔位预览 · 生成后才可导出', icon='INFO')
    column.operator('anyfurry.generate_relief', icon='MOD_BOOLEAN')
    layout.operator('anyfurry.reset_relief', icon='LOOP_BACK')
    layout.label(text='默认尺寸为设计起点，需实物验证')
    show_error(layout, p)


CLASSES = (AFReliefSettings, AF_OT_generate_relief, AF_OT_reset_relief)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.af_relief = PointerProperty(type=AFReliefSettings)


def unregister():
    clear()
    del bpy.types.Scene.af_relief
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
