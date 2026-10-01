"""AnyFurry native shape-key controls."""
from pathlib import Path
import bpy
from . import eye, mouth_opening, runtime, project, relief
from .privacy import error_message
from bpy.props import BoolProperty
from mathutils import Quaternion, Vector

KEY = '嘴巴长度'
MOUTH_WIDTH_KEY = '嘴巴宽度'
OVERALL_WIDTH_KEY = '整体宽度'
OVERALL_HEIGHT_KEY = '整体高度'
FOREHEAD_KEY = '额头高度'
LOWER_FACE_KEY = '眼部以下高度'
MUZZLE_VERTICAL_KEY = '鼻嘴上下位置'
MUZZLE_VERTICAL_MM = 8.0
SHORT_MM = 15.0
LONG_MM = 25.0
MOUTH_WIDTH_PCT = 15.0
OVERALL_PCT = 10.0
FOREHEAD_PCT = 15.0
LOWER_FACE_PCT = 12.0
SCENE_NAME = 'AnyFurry 嘴巴捏脸'


def target(context):
    return project.shell_for(context.scene)


def _smooth(value):
    u=max(0.0,min(1.0,value));return u*u*(3-2*u)


def ensure_muzzle_vertical_key(obj):
    """Localized front-shell translation; protect eye rims and rear planes."""
    keys=obj.data.shape_keys.key_blocks
    if MUZZLE_VERTICAL_KEY in keys:return keys[MUZZLE_VERTICAL_KEY]
    key=obj.shape_key_add(name=MUZZLE_VERTICAL_KEY,from_mix=False)
    key.relative_key=keys['Basis'];key.slider_min=-1;key.slider_max=1;key.value=0
    for source,target in zip(keys['Basis'].data,key.data):
        x,y,z=source.co
        top_end=20.0
        top=1-_smooth((y-(top_end-55.0))/55.0)
        bottom=_smooth((y+145)/20)
        sides=1-_smooth((abs(x)-80)/35)
        front=_smooth((z+25)/50)
        target.co=source.co
        target.co.y+=MUZZLE_VERTICAL_MM*top*bottom*sides*front
    obj['muzzle_vertical_range_mm']=MUZZLE_VERTICAL_MM
    obj['muzzle_vertical_region']='nose/muzzle core; smooth blend; forehead y>=20 and rear z<=-25 protected; broad bridge blend; eye cutters keep XY positions'
    return key


def shape_controls(layout, obj, names):
    blocks = obj.data.shape_keys.key_blocks
    definitions = {
        KEY: ('口鼻部长度', 'anyfurry.reset_mouth', 25, 'mm'),
        MOUTH_WIDTH_KEY: ('口鼻部宽度', 'anyfurry.reset_mouth_width', 15, '%'),
        MUZZLE_VERTICAL_KEY: ('鼻嘴整体上下位置', 'anyfurry.reset_muzzle_vertical', 8, 'mm'),
        OVERALL_WIDTH_KEY: ('整体宽度', 'anyfurry.reset_overall_width', 10, '%'),
        OVERALL_HEIGHT_KEY: ('整体高度', 'anyfurry.reset_overall_height', 10, '%'),
        FOREHEAD_KEY: ('额头高度', 'anyfurry.reset_forehead', 15, '%'),
        LOWER_FACE_KEY: ('眼部以下高度', 'anyfurry.reset_lower_face', 12, '%'),
    }
    for name in names:
        if name not in blocks:
            if name == MUZZLE_VERTICAL_KEY:
                layout.operator('anyfurry.add_muzzle_vertical', text='升级：启用鼻嘴上下位置')
            else:
                layout.label(text='缺少参数：' + name, icon='ERROR')
            continue
        label, operator, scale, unit = definitions[name]
        row = layout.row(align=True)
        row.prop(blocks[name], 'value', text=label, slider=True)
        row.operator(operator, text='', icon='LOOP_BACK')
        layout.label(text='相对最终版：%+.1f %s' % (blocks[name].value * scale, unit))


def controls(layout, obj, scene):
    layout.label(text='整体比例')
    shape_controls(layout, obj, (OVERALL_WIDTH_KEY, OVERALL_HEIGHT_KEY, FOREHEAD_KEY, LOWER_FACE_KEY))
    layout.separator()
    layout.label(text='鼻嘴形态')
    shape_controls(layout, obj, (KEY, MOUTH_WIDTH_KEY, MUZZLE_VERTICAL_KEY))
    eye.controls(layout, scene)
    mouth_opening.controls(layout, scene)
    relief.controls(layout, scene)


def focus_model(context, obj):
    obj.hide_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    center = obj.matrix_world @ (sum((Vector(p) for p in obj.bound_box), Vector()) / 8)
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            space.show_region_ui = True
            space.region_3d.view_location = center
            space.region_3d.view_distance = 440
            space.region_3d.view_rotation = Quaternion((0.70710678, 0.70710678, 0, 0))
            space.region_3d.view_perspective = 'ORTHO'


class AF_OT_load_mouth(bpy.types.Operator):
    bl_idname = 'anyfurry.load_mouth'
    bl_label = '打开最终版头壳 / 继续编辑'
    bl_description = '优先继续已有头壳；新建按钮会另开独立场景'
    new_model: BoolProperty(default=False, options={'HIDDEN'})

    def execute(self, context):
        if not self.new_model:
            obj = target(context)
            if obj:
                context.scene.af_project.shell = obj
                focus_model(context, obj)
                return {'FINISHED'}
            existing = next((scene for scene in bpy.data.scenes if project.shell_for(scene)), None)
            if existing:
                context.window.scene = existing
                obj = project.shell_for(existing)
                existing.af_project.shell = obj
                focus_model(context, obj)
                return {'FINISHED'}
        path = Path(__file__).parent / 'assets' / 'mouth_eye_base_v0281.blend'
        try:
            if not path.is_file():
                raise ValueError('缺少模型资产，请重新安装完整 ZIP 包')
            with bpy.data.libraries.load(str(path), link=False) as (src, dst):
                if SCENE_NAME not in src.scenes:
                    raise ValueError('模型资产缺少可调场景')
                dst.scenes = [SCENE_NAME]
            scene = dst.scenes[0]
            obj = project.shell_for(scene)
            if not obj:
                raise ValueError('模型资产缺少有效最终版头壳')
            context.window.scene = scene
            scene.af_project.shell = obj
            ensure_muzzle_vertical_key(obj)
            focus_model(context, obj)
            runtime.schedule(scene)
        except Exception as error:
            self.report({'ERROR'}, '载入失败：' + error_message(error))
            return {'CANCELLED'}
        self.report({'INFO'}, '已打开最终版；N → AnyFurry')
        return {'FINISHED'}


class AF_OT_focus_model(bpy.types.Operator):
    bl_idname = 'anyfurry.focus_model'
    bl_label = '正面查看头壳'
    @classmethod
    def poll(cls, context):
        return target(context) is not None and context.mode == 'OBJECT'
    def execute(self, context):
        focus_model(context, target(context))
        return {'FINISHED'}


class AF_OT_mouth_popup(bpy.types.Operator):
    bl_idname = 'anyfurry.mouth_slider'
    bl_label = 'AnyFurry 基础捏脸'

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.window is not None

    def invoke(self, context, event):
        if not target(context):
            obj = next((o for o in context.scene.objects
                        if o.get('anyfurry_mouth_version') == 3
                        and o.type == 'MESH' and o.data.shape_keys
                        and KEY in o.data.shape_keys.key_blocks), None)
            if obj:
                obj.hide_set(False)
                obj.select_set(True)
                context.view_layer.objects.active = obj
            elif bpy.ops.anyfurry.load_mouth() != {'FINISHED'}:
                return {'CANCELLED'}
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        obj = target(context)
        if obj:
            controls(self.layout, obj, context.scene)

    def execute(self, context):
        return {'FINISHED'}


class _ResetBase:
    key_name = ''

    @classmethod
    def poll(cls, context):
        obj = target(context)
        return obj is not None and cls.key_name in obj.data.shape_keys.key_blocks and context.mode == 'OBJECT'

    def execute(self, context):
        target(context).data.shape_keys.key_blocks[self.key_name].value = 0
        return {'FINISHED'}


class AF_OT_reset_mouth(_ResetBase, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_mouth'
    bl_label = '恢复最终版嘴长'
    bl_options = {'REGISTER', 'UNDO'}
    key_name = KEY


class AF_OT_reset_mouth_width(_ResetBase, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_mouth_width'
    bl_label = '恢复最终版嘴宽'
    bl_options = {'REGISTER', 'UNDO'}
    key_name = MOUTH_WIDTH_KEY


class AF_OT_reset_overall_width(_ResetBase, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_overall_width'
    bl_label = '恢复最终版整体宽度'
    bl_options = {'REGISTER', 'UNDO'}
    key_name = OVERALL_WIDTH_KEY


class AF_OT_reset_overall_height(_ResetBase, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_overall_height'
    bl_label = '恢复最终版整体高度'
    bl_options = {'REGISTER', 'UNDO'}
    key_name = OVERALL_HEIGHT_KEY


class AF_OT_reset_forehead(_ResetBase, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_forehead'
    bl_label = '恢复最终版额头高度'
    bl_options = {'REGISTER', 'UNDO'}
    key_name = FOREHEAD_KEY


class AF_OT_reset_lower_face(_ResetBase, bpy.types.Operator):
    bl_idname = 'anyfurry.reset_lower_face'
    bl_label = '恢复最终版眼部以下高度'
    bl_options = {'REGISTER', 'UNDO'}
    key_name = LOWER_FACE_KEY


class AF_OT_reset_muzzle_vertical(_ResetBase,bpy.types.Operator):
    bl_idname='anyfurry.reset_muzzle_vertical'
    bl_label='恢复鼻嘴位置'
    bl_options={'REGISTER','UNDO'}
    key_name=MUZZLE_VERTICAL_KEY
    def execute(self,context):
        obj=target(context);obj.data.shape_keys.key_blocks[MUZZLE_VERTICAL_KEY].value=0
        mouth_opening.force_refresh(context.scene)
        return {'FINISHED'}


class AF_OT_add_muzzle_vertical(bpy.types.Operator):
    bl_idname='anyfurry.add_muzzle_vertical'
    bl_label='启用鼻嘴上下位置'
    bl_options={'REGISTER','UNDO'}
    @classmethod
    def poll(cls,context):return target(context) is not None
    def execute(self,context):
        ensure_muzzle_vertical_key(target(context));mouth_opening.force_refresh(context.scene)
        return {'FINISHED'}


class AF_PT_mouth(bpy.types.Panel):
    bl_label = 'AnyFurry · Beta 1 · v0.29.0'
    bl_idname = 'AF_PT_mouth'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'AnyFurry'
    bl_order = -10

    def draw(self, context):
        layout = self.layout
        obj = target(context)
        if context.mode != 'OBJECT':
            layout.label(text='请切换到物体模式后调整', icon='INFO')
            return
        if obj:
            layout.label(text='当前头壳：' + obj.name, icon='MESH_DATA')
            layout.label(text='基础模型：最终版 v0.27.0')
            layout.prop(context.scene.af_project, 'auto_refresh')
            layout.prop(context.scene.af_project, 'fast_preview')
            runtime.draw_status(layout, context.scene)
            row = layout.row(align=True)
            row.operator('anyfurry.refresh_all', text='更新眼嘴 / 孔位', icon='FILE_REFRESH')
            row.operator('anyfurry.focus_model', text='正面查看')
        else:
            layout.operator('anyfurry.load_mouth', icon='MESH_MONKEY')
        layout.operator('anyfurry.load_mouth', text='新建独立头壳', icon='ADD').new_model = True


class HeadPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'AnyFurry'
    bl_parent_id = 'AF_PT_mouth'
    @classmethod
    def poll(cls, context):
        return target(context) is not None and context.mode == 'OBJECT'


class AF_PT_proportions(HeadPanel, bpy.types.Panel):
    bl_label = '整体比例'
    bl_idname = 'AF_PT_proportions'
    def draw(self, context):
        shape_controls(self.layout, target(context), (OVERALL_WIDTH_KEY, OVERALL_HEIGHT_KEY, FOREHEAD_KEY, LOWER_FACE_KEY))


class AF_PT_nose(HeadPanel, bpy.types.Panel):
    bl_label = '鼻嘴形态'
    bl_idname = 'AF_PT_nose'
    def draw(self, context):
        shape_controls(self.layout, target(context), (KEY, MOUTH_WIDTH_KEY, MUZZLE_VERTICAL_KEY))


class AF_PT_eye(HeadPanel, bpy.types.Panel):
    bl_label = '眼部开孔'
    bl_idname = 'AF_PT_eye'
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        eye.controls(self.layout, context.scene)


class AF_PT_opening(HeadPanel, bpy.types.Panel):
    bl_label = '嘴部开孔'
    bl_idname = 'AF_PT_opening'
    bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        mouth_opening.controls(self.layout, context.scene)


class AF_PT_files(HeadPanel, bpy.types.Panel):
    bl_label = '保存与导出'
    bl_idname = 'AF_PT_files'
    def draw(self, context):
        layout = self.layout
        layout.operator('anyfurry.save_project', icon='FILE_BLEND')
        row = layout.row(align=True)
        row.operator('anyfurry.save_preset', text='保存参数')
        row.operator('anyfurry.load_preset', text='载入参数')
        layout.operator('anyfurry.export_head', icon='EXPORT')
        layout.operator('anyfurry.reset_all', icon='LOOP_BACK')


class AF_PT_relief(HeadPanel, bpy.types.Panel):
    bl_label = '减重孔'
    bl_idname = 'AF_PT_relief'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        relief.controls(self.layout, context.scene)


CLASSES = (
    AF_OT_load_mouth,
    AF_OT_focus_model,
    AF_OT_mouth_popup,
    AF_OT_reset_mouth,
    AF_OT_reset_mouth_width,
    AF_OT_reset_overall_width,
    AF_OT_reset_overall_height,
    AF_OT_reset_forehead,
    AF_OT_reset_lower_face,
    AF_OT_reset_muzzle_vertical,
    AF_OT_add_muzzle_vertical,
    AF_PT_mouth,
    AF_PT_proportions,
    AF_PT_nose,
    AF_PT_eye,
    AF_PT_opening,
    AF_PT_relief,
    AF_PT_files,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)







