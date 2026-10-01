"""Current head, project settings and user-facing file operations."""
import json
import math
import os
import struct
from pathlib import Path
import bpy
import bmesh
from bpy.props import BoolProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper
from . import runtime
from .privacy import error_message

SHAPE_NAMES = ('嘴巴长度', '嘴巴宽度', '整体宽度', '整体高度', '额头高度', '眼部以下高度', '鼻嘴上下位置')
EYE_NAMES = ('preset', 'openings_enabled', 'spacing', 'width', 'height', 'position', 'angle')
MOUTH_NAMES = ('enabled', 'size', 'width', 'quadratic_coefficient', 'side_gap')
RELIEF_NAMES = ('enabled', 'diameter_mm', 'clearance_mm', 'density', 'forehead', 'top', 'sides')


def valid_shell(obj):
    return bool(obj and obj.type == 'MESH' and obj.get('anyfurry_mouth_version') == 3
                and obj.data.shape_keys and '嘴巴长度' in obj.data.shape_keys.key_blocks)


def shell_for(scene):
    if hasattr(scene, 'af_project'):
        obj = scene.af_project.shell
        if valid_shell(obj) and obj.name in scene.objects:
            return obj
    candidates = [o for o in scene.objects if valid_shell(o)]
    return candidates[0] if len(candidates) == 1 else None


def auto_changed(self, context):
    if self.auto_refresh:
        runtime.schedule(self.id_data)
    else:
        runtime._queue.pop(self.id_data.as_pointer(), None)
        runtime.resume(self.id_data.as_pointer())


class AFProjectSettings(bpy.types.PropertyGroup):
    shell: PointerProperty(type=bpy.types.Object)
    auto_refresh: BoolProperty(name='自动更新开孔', default=True, update=auto_changed,
                              description='停止调节后计算实体开孔；关闭后可手动更新，保存和导出仍会更新')
    fast_preview: BoolProperty(name='快速拖动预览', default=True,
                              description='拖动期间暂时隐藏孔，停止后恢复完整实体孔，减少高面数布尔重复计算')


def parameters(scene):
    obj = shell_for(scene)
    return {'format': 'anyfurry-preset', 'schema': 2,
            'model': 'final-v0270',
            'shape': {n: obj.data.shape_keys.key_blocks[n].value for n in SHAPE_NAMES
                      if n in obj.data.shape_keys.key_blocks},
            'eye': {n: getattr(scene.af_eye, n) for n in EYE_NAMES},
            'mouth': {n: getattr(scene.af_mouth_opening, n) for n in MOUTH_NAMES},
            'relief': {n: getattr(scene.af_relief, n) for n in RELIEF_NAMES}}


def validate_parameters(data):
    if not isinstance(data, dict) or data.get('format') != 'anyfurry-preset' or data.get('schema') not in (1, 2):
        raise ValueError('不是支持的 AnyFurry 参数预设')
    if data.get('model') != 'final-v0270':
        raise ValueError('预设基础模型与当前最终版不同')
    for group, names in (('shape', SHAPE_NAMES), ('eye', EYE_NAMES), ('mouth', MOUTH_NAMES)):
        values = data.get(group)
        if not isinstance(values, dict) or set(values) != set(names):
            raise ValueError('预设参数不完整：' + group)
        for name, value in values.items():
            if name == 'preset':
                if value not in ('A', 'B', 'C'):
                    raise ValueError('眼型必须为 1、2 或 3 号')
            elif name in ('enabled', 'openings_enabled'):
                if type(value) is not bool:
                    raise ValueError('开孔开关必须为布尔值')
            else:
                minimum, maximum = (-0.6, 1) if name == '嘴巴长度' else ((18, 54) if name == 'quadratic_coefficient' else (-1, 1))
                if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                    raise ValueError('参数超出范围：' + name)
    if data['schema'] == 2:
        values = data.get('relief')
        if not isinstance(values, dict) or set(values) != set(RELIEF_NAMES):
            raise ValueError('减重孔预设参数不完整')
        for name, value in values.items():
            if name == 'density':
                if type(value) is not str or value not in ('SPARSE', 'MEDIUM', 'DENSE'):
                    raise ValueError('减重孔疏密必须为疏、中或密')
            elif name in ('enabled', 'forehead', 'top', 'sides'):
                if type(value) is not bool:
                    raise ValueError('减重孔开关必须为布尔值')
            else:
                minimum, maximum = (8, 26) if name == 'diameter_mm' else (4, 20)
                if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
                    raise ValueError('减重孔参数超出范围：' + name)


def apply_parameters(scene, data):
    validate_parameters(data)
    from .mouth import ensure_muzzle_vertical_key
    obj = shell_for(scene)
    ensure_muzzle_vertical_key(obj)
    for name, value in data['shape'].items():
        obj.data.shape_keys.key_blocks[name].value = value
    for name, value in data['eye'].items():
        setattr(scene.af_eye, name, value)
    for name, value in data['mouth'].items():
        setattr(scene.af_mouth_opening, name, value)
    from . import relief
    for name, value in (data['relief'] if data['schema'] == 2 else relief.DEFAULTS).items():
        setattr(scene.af_relief, name, value)


class HeadOperator:
    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and shell_for(context.scene) is not None


class AF_OT_refresh_all(HeadOperator, bpy.types.Operator):
    bl_idname = 'anyfurry.refresh_all'
    bl_label = '更新眼嘴孔与孔位 / 重试'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not runtime.flush(context.scene, force=True):
            self.report({'ERROR'}, '更新失败，已保留上次开孔；请查看眼、嘴或减重孔提示')
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_reset_all(HeadOperator, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_all'
    bl_label = '恢复全部默认值'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        data = parameters(context.scene)
        data['shape'] = {n: 0 for n in SHAPE_NAMES}
        data['eye'] = {n: (True if n == 'openings_enabled' else 'A' if n == 'preset' else 0) for n in EYE_NAMES}
        data['mouth'] = {n: (True if n == 'enabled' else 36 if n == 'quadratic_coefficient' else 0) for n in MOUTH_NAMES}
        from . import relief
        data['relief'] = dict(relief.DEFAULTS)
        apply_parameters(context.scene, data)
        return bpy.ops.anyfurry.refresh_all()


class AF_OT_save_preset(HeadOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'anyfurry.save_preset'
    bl_label = '保存参数预设'
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            data = parameters(context.scene)
            validate_parameters(data)
            Path(self.filepath).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
        except Exception as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_load_preset(HeadOperator, bpy.types.Operator, ImportHelper):
    bl_idname = 'anyfurry.load_preset'
    bl_label = '载入参数预设'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'
    filter_glob: StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        previous = parameters(context.scene)
        try:
            data = json.loads(Path(self.filepath).read_text(encoding='utf-8-sig'))
            validate_parameters(data)
            apply_parameters(context.scene, data)
            if not runtime.flush(context.scene, force=True):
                raise ValueError('预设开孔无法生成，已恢复原参数')
        except Exception as error:
            if previous != parameters(context.scene):
                apply_parameters(context.scene, previous)
                runtime.flush(context.scene, force=True)
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        return {'FINISHED'}


class AF_OT_save_project(HeadOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'anyfurry.save_project'
    bl_label = '保存可编辑工程'
    filename_ext = '.blend'
    filter_glob: StringProperty(default='*.blend', options={'HIDDEN'})

    def execute(self, context):
        if not runtime.flush(context.scene):
            self.report({'ERROR'}, '开孔更新失败，请重试后保存；原工程仍可用 Blender 保存')
            return {'CANCELLED'}
        return bpy.ops.wm.save_as_mainfile(filepath=self.filepath)


def export_stl(scene, filepath):
    if not runtime.flush(scene):
        raise ValueError('开孔未更新成功，暂不导出')
    from . import relief
    if not relief.ready(scene):
        raise ValueError('减重孔仍为预览，请先生成 / 更新减重孔，或关闭减重孔后导出')
    obj = shell_for(scene)
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if not bm.faces or any(not e.is_manifold for e in bm.edges):
            raise ValueError('当前头壳不是闭合网格，请调整参数或检查模型')
        # STL stores float32 triangles rather than polygons. Boolean ngons can
        # triangulate into coincident/degenerate facets even on a closed mesh.
        # Clean only this disposable export copy, in physical millimetres.
        scale = scene.unit_settings.scale_length * 1000
        matrix = obj.matrix_world
        for vertex in bm.verts:
            vertex.co = (matrix @ vertex.co) * scale
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=0.005)
        bmesh.ops.dissolve_degenerate(bm, edges=list(bm.edges), dist=0.005)
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
        bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
        if bm.calc_volume(signed=True) < 0:
            bmesh.ops.reverse_faces(bm, faces=list(bm.faces))
        bm.normal_update()
        if any(not e.is_manifold for e in bm.edges):
            raise ValueError('三角化后的导出网格存在异常，请调整参数后重试')
        seen = set()
        for root in bm.verts:
            if root in seen:
                continue
            if seen:
                raise ValueError('头壳包含分离部分，请检查开孔和比例')
            stack = [root]
            seen.add(root)
            while stack:
                vertex = stack.pop()
                for edge in vertex.link_edges:
                    other = edge.other_vert(vertex)
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        if bm.calc_volume(signed=True) <= 0:
            raise ValueError('头壳法线或体积异常')
        triangles = list(bm.faces)
        path = Path(filepath)
        temporary = path.with_name(path.name + '.anyfurry.tmp')
        try:
            with temporary.open('wb') as stream:
                stream.write(b'AnyFurry Beta1; units=mm'.ljust(80, b'\0'))
                stream.write(struct.pack('<I', len(triangles)))
                for triangle in triangles:
                    points = [v.co for v in triangle.verts]
                    normal = triangle.normal
                    stream.write(struct.pack('<12fH', *normal, *points[0], *points[1], *points[2], 0))
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return len(triangles)
    finally:
        bm.free()
        evaluated.to_mesh_clear()


class AF_OT_export_head(HeadOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'anyfurry.export_head'
    bl_label = '导出当前头壳 STL（毫米）'
    filename_ext = '.stl'
    filter_glob: StringProperty(default='*.stl', options={'HIDDEN'})

    def execute(self, context):
        try:
            count = export_stl(context.scene, self.filepath)
        except Exception as error:
            self.report({'ERROR'}, error_message(error))
            return {'CANCELLED'}
        self.report({'INFO'}, '已导出当前头壳，单位毫米，%d 个三角面' % count)
        return {'FINISHED'}


CLASSES = (AFProjectSettings, AF_OT_refresh_all, AF_OT_reset_all, AF_OT_save_preset,
           AF_OT_load_preset, AF_OT_save_project, AF_OT_export_head)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.af_project = PointerProperty(type=AFProjectSettings)


def unregister():
    del bpy.types.Scene.af_project
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
