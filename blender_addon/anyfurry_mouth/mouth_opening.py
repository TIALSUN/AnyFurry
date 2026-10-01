"""AnyFurry symmetric physical mouth-opening controls."""
import math
import time
import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, PointerProperty, StringProperty
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from .recovery import OpeningTransaction, show_error, set_error
from . import runtime, project

BASE_HALF_WIDTH_MM = 46.0
FAN_HALF_WIDTH_MM = 5.5
WIDTH_RANGE_PCT = 30.0
SIZE_RANGE_PCT = 20.0
CUTTER_OUTSIDE_MM = 120.0
CUTTER_INSIDE_MM = 45.0
DEBOUNCE_SECONDS = 0.20
CUTTER_COLLECTION = 'AnyFurry_实体嘴孔切刀'
CUTTER_NAME = '实体嘴孔切刀'
BOOLEAN_NAME = 'AnyFurry_实体嘴孔'
_refreshing = set()
_last_state = {}
_pending_refresh = {}


def shell_for(scene):
    return project.shell_for(scene)


def shape_value(shell, name):
    keys = shell.data.shape_keys
    return keys.key_blocks[name].value if keys and name in keys.key_blocks else 0.0


def cutter_for(scene):
    existing = next((o for o in scene.objects if o.type == 'MESH' and o.get('anyfurry_mouth_cutter')), None)
    if existing:
        return existing
    collection = next((c for c in scene.collection.children if c.name.startswith(CUTTER_COLLECTION)), None)
    if collection is None:
        collection = bpy.data.collections.new(CUTTER_COLLECTION)
        scene.collection.children.link(collection)
    mesh = bpy.data.meshes.new(CUTTER_NAME + '_网格')
    obj = bpy.data.objects.new(CUTTER_NAME, mesh)
    collection.objects.link(obj)
    obj['anyfurry_mouth_cutter'] = True
    obj.display_type = 'WIRE'
    obj.hide_render = True
    obj.hide_set(True)
    return obj


def mouth_modifier(shell):
    modifier = shell.modifiers.get(BOOLEAN_NAME)
    if modifier is None:
        modifier = shell.modifiers.new(name=BOOLEAN_NAME, type='BOOLEAN')
    return modifier


def opening_state(scene):
    shell = shell_for(scene)
    if shell is None or not hasattr(scene, 'af_mouth_opening'):
        return None
    p = scene.af_mouth_opening
    return (bool(p.enabled), round(p.size, 6), round(p.width, 6), round(p.quadratic_coefficient, 6), round(p.side_gap, 6),
            round(shape_value(shell, '鼻嘴上下位置'), 6), round(shape_value(shell, '嘴巴长度'), 6), round(shape_value(shell, '嘴巴宽度'), 6),
            round(shape_value(shell, '整体宽度'), 6), round(shape_value(shell, '整体高度'), 6),
            round(shape_value(shell, '眼部以下高度'), 6),
            tuple((key.name, round(key.value, 6)) for key in shell.data.shape_keys.key_blocks if key.name != 'Basis'),
            tuple(round(v, 6) for row in shell.matrix_world for v in row))


def smoothstep(value):
    t = max(0.0, min(1.0, value))
    return t * t * (3.0 - 2.0 * t)


def transformed_y(shell, base_y):
    center = float(shell.get('overall_scale_center_y_mm', 0.0))
    overall = shape_value(shell, '整体高度')
    anchor = float(shell.get('lower_face_anchor_y_mm', 20.0))
    lower = shape_value(shell, '眼部以下高度')
    result = base_y + (base_y - center) * 0.10 * overall
    distance = min(base_y - anchor, 0.0)
    weight = smoothstep((-distance) / 35.0)
    result += distance * 0.12 * weight * lower
    result += 8.0 * shape_value(shell, '鼻嘴上下位置')
    return result


def side_gap_mm(settings):
    """Vertical front-projection gap, not geodesic shell-surface distance."""
    return (10.0 * 42.0 / 38.0) * (1.0 + .45 * settings.side_gap)


def outline_xy(shell, settings, samples=97, surface_sampler=None):
    """Follow the muzzle underside; opening controls expand below that rim."""
    face_width=(1+.10*shape_value(shell,'整体宽度'))*(1+.15*shape_value(shell,'嘴巴宽度'))
    half_width=BASE_HALF_WIDTH_MM*face_width*(1+WIDTH_RANGE_PCT/100*settings.width)
    end_x=half_width*.90
    # Local maximum of underside depth slope, bounded to the known muzzle band.
    # The cheek beyond x=45 has no distinct muzzle seam; extend the last anchor.
    anchors=[]
    for i in range(19):
        x=45.0*face_width*i/18
        bx=x/face_width
        seed=-95.0-6.0*smoothstep(bx/18.0)-2.0*smoothstep((bx-30)/15)
        y=seed
        if surface_sampler:
            candidates=[]
            for j in range(31):
                q=seed-6+j*.4
                yl,yh=transformed_y(shell,q-3),transformed_y(shell,q+3)
                zs=[surface_sampler(sign*x,yy) for sign in (-1,1) for yy in (yl,yh)]
                if all(z is not None for z in zs):
                    slope=((zs[1]-zs[0])+(zs[3]-zs[2]))/(2*(yh-yl))
                    candidates.append((q,slope-.004*(q-seed)**2))
            if candidates:
                best=max(v for q,v in candidates)
                weights=[math.exp(45*(v-best)) for q,v in candidates]
                y=sum(q*w for (q,v),w in zip(candidates,weights))/sum(weights)
        anchors.append(transformed_y(shell,y))
    # Smooth mesh-scale noise before interpolation; symmetry gives a level center.
    for _ in range(2):
        anchors=[(anchors[max(0,i-1)]+2*anchors[i]+anchors[min(18,i+1)])/4 for i in range(19)]
    dx=45*face_width/18
    def rim(x):
        u=min(abs(x)/dx,18.0);i=min(int(u),17);f=u-i
        p0,p1=anchors[i],anchors[i+1]
        m0=0 if i==0 else (anchors[i+1]-anchors[i-1])/2
        m1=0 if i+1==18 else (anchors[i+2]-anchors[i])/2
        return (2*f**3-3*f*f+1)*p0+(f**3-2*f*f+f)*m0+(-2*f**3+3*f*f)*p1+(f**3-f*f)*m1
    height_scale=transformed_y(shell,-100)-transformed_y(shell,-101)
    gap=side_gap_mm(settings)*height_scale
    drop=max(0,rim(0)-min(anchors))
    central_depth=(gap+.45*drop)*(1+SIZE_RANGE_PCT/100*settings.size)
    join_x=end_x*.75
    delta_a=(settings.quadratic_coefficient-36)/1000*height_scale
    def corner_weight(x):
        u=max(0.0,(x/end_x-.60)/.40)
        return u*u
    def upper(x):
        return rim(x)+7.5*height_scale*corner_weight(x)
    def original_bottom(x):
        depth=gap+(central_depth-gap)*(1-smoothstep(x/(end_x*.65)))
        depth+=delta_a*max(0,x-join_x)**2
        # Taper the last part into a lifted, rounded smile corner.
        depth*=1-.55*smoothstep((x/end_x-.70)/.30)
        return upper(x)-depth
    # One circular lower arc, symmetric about x=0.
    end_y=original_bottom(end_x)-7.0*height_scale
    sag=6.0*height_scale*(1+.20*settings.size)*(settings.quadratic_coefficient/36.0)
    radius=(end_x*end_x+sag*sag)/(2*sag)
    center_y=end_y+radius-sag
    end_slope=end_x/math.sqrt(max(1e-8,radius*radius-end_x*end_x))
    def bottom(x):
        if x>=end_x:return end_y+end_slope*(x-end_x)
        return center_y-math.sqrt(max(1e-8,radius*radius-x*x))
    points=[]
    n=max(96,samples-1)
    for i in range(n):
        x=end_x*i/n;points.append((x,upper(x)))
    def bezier(p0,p1,p2,p3):
        for j in range(16):
            u=j/16;v=1-u
            points.append(tuple(v**3*p0[k]+3*v*v*u*p1[k]+3*v*u*u*p2[k]+u**3*p3[k] for k in (0,1)))
    top,low=upper(end_x),bottom(end_x)
    # Preserve approved upper cap; reshape only the lower return from its tip.
    old_low=original_bottom(end_x);mid=(top+old_low)/2;g=top-old_low
    top_slope=(upper(end_x+.1)-upper(end_x-.1))/.2
    low_slope=end_slope
    h=(half_width-end_x)*.55
    bezier((end_x,top),(end_x+h,top+h*top_slope),(half_width,mid+.34*g),(half_width,mid))
    bezier((half_width,mid),(half_width,mid-.34*g),(end_x+h,low+h*low_slope),(end_x,low))
    for i in range(n+1):
        x=end_x*(1-i/n);points.append((x,bottom(x)))
    return points+[(-x,y) for x,y in reversed(points[1:-1])]


def update_cutter(obj, coords, matrix_world, surface_sampler=None):
    count=len(coords)
    cy=sum(y for x,y,z in coords)/count
    # Surface-referenced rings: actual inward narrowing through shell thickness.
    profiles=[(CUTTER_OUTSIDE_MM,1.0),(0.0,1.0),(-.25,.995),(-.6,.987),(-1.1,.980),(-1.8,.975),(-2.8,.970),(-4.0,.970),(-CUTTER_INSIDE_MM,.970)]
    verts=[]
    for depth,scale in profiles:
        for x,y,z in coords:
            xx,yy=x*scale,cy+(y-cy)*scale
            zz=z
            if surface_sampler and scale!=1.0:
                samples=[surface_sampler(xx,yy),surface_sampler(-xx,yy)]
                valid=[v for v in samples if v is not None]
                zz=max(valid) if valid else z
            if zz is None:zz=z
            verts.append((xx,yy,zz+depth))
    obj['pitch_degrees']=0.0
    faces=[tuple(reversed(range(count))),tuple(range((len(profiles)-1)*count,len(profiles)*count))]
    for ring in range(len(profiles)-1):
        for i in range(count):
            j=(i+1)%count;a=ring*count;b=(ring+1)*count
            faces.append((a+i,a+j,b+j,b+i))
    mesh=obj.data;mesh.clear_geometry();mesh.from_pydata(verts,[],faces);mesh.update(calc_edges=True)
    bm=bmesh.new();bm.from_mesh(mesh);bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
    if bm.calc_volume(signed=True)<0:bmesh.ops.reverse_faces(bm,faces=list(bm.faces))
    bm.to_mesh(mesh);bm.free()
    for face in mesh.polygons:face.use_smooth=False
    mesh.update(calc_edges=True);obj.matrix_world=matrix_world
    obj['point_count']=count;obj['ring_count']=len(profiles);obj['inner_scale']=.970;obj['transition_depth_mm']=2.8


def _refresh_scene(scene):
    token = scene.as_pointer()
    if token in _refreshing:
        return
    _pending_refresh.pop(token, None)
    shell = shell_for(scene)
    if shell is None or not hasattr(scene, 'af_mouth_opening'):
        return
    _refreshing.add(token)
    evaluated = None
    # Disable all AnyFurry opening booleans while projecting onto the original shell.
    affected = [m for m in shell.modifiers if m.type == 'BOOLEAN' and m.name.startswith('AnyFurry_实体')]
    previous = [(m, m.show_viewport, m.show_render) for m in affected]
    try:
        for modifier, _, _ in previous:
            modifier.show_viewport = False
            modifier.show_render = False
        if bpy.context.view_layer:
            bpy.context.view_layer.update()
        bvh, z_start = runtime.surface(scene, shell)
        def sample_surface(x, y):
            hit = bvh.ray_cast(Vector((x, y, z_start)), Vector((0, 0, -1)))[0]
            return hit.z if hit is not None else None
        xy = outline_xy(shell, scene.af_mouth_opening, surface_sampler=sample_surface)
        coords = []
        misses = 0
        for x, y in xy:
            hit = bvh.ray_cast(Vector((x, y, z_start)), Vector((0, 0, -1)))[0]
            if hit is None:
                misses += 1
                coords.append((x, y, 0.0))
            else:
                coords.append((x, y, hit.z))
        # Pair mirrored samples by position; the rounded corner arcs add points
        # to the old contour, so fixed-index pairing is no longer valid.
        for index, (x, y, z) in enumerate(coords):
            if x < -1e-6:
                target = (-x, y)
                opposite = min(range(len(coords)), key=lambda j: (coords[j][0] - target[0]) ** 2 + (coords[j][1] - target[1]) ** 2)
                if opposite != index:
                    shared_z = max(z, coords[opposite][2])
                    coords[index] = (x, y, shared_z)
                    ox, oy, _ = coords[opposite]
                    coords[opposite] = (ox, oy, shared_z)
        if misses:
            raise ValueError('嘴孔有 %d 个点超出头壳表面，调整参数后重试' % misses)
        cutter = cutter_for(scene)
        if misses == 0:
            update_cutter(cutter, coords, shell.matrix_world, sample_surface)
        cutter['projection_misses'] = misses
        modifier = mouth_modifier(shell)
        modifier.operation = 'DIFFERENCE'
        modifier.solver = 'EXACT'
        modifier.object = cutter
        modifier.show_viewport = bool(scene.af_mouth_opening.enabled and misses == 0)
        modifier.show_render = bool(scene.af_mouth_opening.enabled and misses == 0)
        for other, viewport, render in previous:
            if other != modifier:
                other.show_viewport = viewport
                other.show_render = render
        shell['mouth_opening_physical'] = True
        shell['mouth_opening_live_boolean'] = True
        shell['mouth_opening_width_range_percent'] = WIDTH_RANGE_PCT
        shell['mouth_opening_size_range_percent'] = SIZE_RANGE_PCT
        shell['mouth_opening_design'] = 'circular_lower_inward_rim'
        shell['mouth_opening_cutter_inside_mm'] = CUTTER_INSIDE_MM
        shell['mouth_opening_cutter_outside_mm'] = CUTTER_OUTSIDE_MM
        _last_state[token] = opening_state(scene)
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()
        _refreshing.discard(token)


def refresh_scene(scene):
    shell = shell_for(scene)
    if shell is None or not hasattr(scene, 'af_mouth_opening'):
        return False
    if scene.as_pointer() in _refreshing:
        return True
    settings = scene.af_mouth_opening
    try:
        with OpeningTransaction(scene, shell):
            _refresh_scene(scene)
    except Exception as error:
        set_error(settings, error)
        # Do not repeatedly retry an unchanged failing request in the handler.
        _last_state[scene.as_pointer()] = opening_state(scene)
        return False
    settings.error = ''
    return True


def schedule_refresh(scene, postpone=True):
    runtime.schedule(scene, postpone=postpone)


def force_refresh(scene):
    return runtime.flush(scene, force=True)


def refresh(self, context):
    schedule_refresh(self.id_data, postpone=True)


class AFMouthOpeningSettings(bpy.types.PropertyGroup):
    error: StringProperty(options={'SKIP_SAVE'})
    enabled: BoolProperty(name='实体嘴孔', default=True, update=refresh,
                          description='直接在头壳上生成实体嘴孔')
    size: FloatProperty(name='嘴巴开口大小', default=0, min=-1, max=1, update=refresh,
                        description='控制中央向下张开量 ±20%，上沿跟随鼻下轮廓')
    quadratic_coefficient: FloatProperty(name='嘴巴弧度（圆弧）', default=36.0, min=18.0, max=54.0, update=refresh,
                                         description='调节整条下沿圆弧的弧高；36为基础圆弧，保持上沿不变')
    side_gap: FloatProperty(name='嘴巴两侧缝隙大小', default=0, min=-1, max=1, update=refresh,
                            description='调节侧缝及下沿圆弧；参考参数约6.1–16.0 mm，不等于孔壁各处实测宽度')
    pitch: FloatProperty(name='旧版角度（兼容数据）', default=0.0, min=-15.0, max=15.0, update=refresh,
                         description='整体嘴孔与内收孔壁绕左右轴旋转；负值俯下，正值仰起，单位度')
    width: FloatProperty(name='嘴巴宽度（开孔）', default=0, min=-1, max=1, update=refresh,
                         description='控制实体嘴孔的横向跨度 ±30%')


class AF_OT_refresh_mouth_opening(bpy.types.Operator):
    bl_idname = 'anyfurry.refresh_mouth_opening'
    bl_label = '立即刷新嘴孔'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not force_refresh(context.scene):
            self.report({'ERROR'}, context.scene.af_mouth_opening.error)
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_reset_mouth_opening(bpy.types.Operator):
    bl_idname = 'anyfurry.reset_mouth_opening'
    bl_label = '恢复嘴孔基础值'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.af_mouth_opening
        p.enabled = True
        p.quadratic_coefficient = 36.0
        p.side_gap = 0
        p.size = 0
        p.width = 0
        p.pitch = 0.0
        if not force_refresh(context.scene):
            self.report({'ERROR'}, context.scene.af_mouth_opening.error)
            return {'CANCELLED'}
        return {'FINISHED'}


def controls(layout, scene):
    box = layout.box()
    show_error(box, scene.af_mouth_opening)
    box.label(text='实体嘴孔 · 沿鼻下开孔 · 上扬嘴角')
    box.prop(scene.af_mouth_opening, 'enabled', text='显示实体嘴孔', toggle=True, icon='MOD_BOOLEAN')
    box.prop(scene.af_mouth_opening, 'size', text='嘴巴开口大小', slider=True)
    box.prop(scene.af_mouth_opening, 'width', text='嘴巴宽度（开孔）', slider=True)
    box.prop(scene.af_mouth_opening, 'quadratic_coefficient', text='嘴巴弧度（圆弧）', slider=True)
    box.prop(scene.af_mouth_opening, 'side_gap', text='嘴巴两侧缝隙大小', slider=True)
    p = scene.af_mouth_opening
    box.label(text='圆弧弧高比例：%.2f' % (p.quadratic_coefficient/36.0))
    box.label(text='侧缝参考参数：%.2f mm（非实测）' % side_gap_mm(p))
    box.label(text='嘴孔宽度 %+.0f%% · 开口参数 %+.0f%%' % (p.width * WIDTH_RANGE_PCT, p.size * SIZE_RANGE_PCT))
    row = box.row(align=True)
    row.operator('anyfurry.refresh_mouth_opening', text='立即刷新嘴孔', icon='FILE_REFRESH')
    row.operator('anyfurry.reset_mouth_opening', text='恢复基础值', icon='LOOP_BACK')
    box.label(text='自动更新开启时，停止调节后更新', icon='INFO')


@persistent
def sync_mouth_opening(scene, depsgraph):
    token = scene.as_pointer()
    if token in _refreshing or token in runtime._busy:
        return
    current = opening_state(scene)
    if current is not None and _last_state.get(token) != current:
        schedule_refresh(scene, postpone=False)


CLASSES = (AFMouthOpeningSettings, AF_OT_refresh_mouth_opening, AF_OT_reset_mouth_opening)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.af_mouth_opening = PointerProperty(type=AFMouthOpeningSettings)
    if sync_mouth_opening not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(sync_mouth_opening)


def unregister():
    _pending_refresh.clear()
    _last_state.clear()
    if sync_mouth_opening in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(sync_mouth_opening)
    if hasattr(bpy.types.Scene, 'af_mouth_opening'):
        del bpy.types.Scene.af_mouth_opening
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)








