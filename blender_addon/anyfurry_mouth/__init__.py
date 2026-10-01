bl_info = {
    'name': 'AnyFurry 捏脸 · Beta 1 · v0.28.1',
    'author': 'AnyFurry',
    'version': (0, 28, 1),
    'blender': (5, 2, 0),
    'location': '3D View > Sidebar > AnyFurry',
    'description': '最终版头壳基础比例与实时眼嘴实体开孔',
    'category': 'Object',
}
from . import project, runtime, mouth, eye, mouth_opening


def menu_entry(self, context):
    self.layout.separator()
    self.layout.operator('anyfurry.mouth_slider', text='AnyFurry 基础捏脸')


def register():
    project.register()
    mouth.register()
    eye.register()
    mouth_opening.register()
    runtime.register()
    import bpy
    bpy.types.VIEW3D_MT_object.append(menu_entry)


def unregister():
    import bpy
    bpy.types.VIEW3D_MT_object.remove(menu_entry)
    runtime.unregister()
    mouth_opening.unregister()
    eye.unregister()
    mouth.unregister()
    project.unregister()

