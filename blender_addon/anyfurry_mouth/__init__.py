bl_info = {
    'name': 'AnyFurry 捏脸 · Beta 1 · v0.29.0',
    'author': 'AnyFurry',
    'version': (0, 29, 0),
    'blender': (5, 2, 0),
    'location': '3D View > Sidebar > AnyFurry',
    'description': '最终版头壳捏脸、眼嘴开孔与对称减重孔',
    'category': 'Object',
}
from . import project, runtime, mouth, eye, mouth_opening, relief


def menu_entry(self, context):
    self.layout.separator()
    self.layout.operator('anyfurry.mouth_slider', text='AnyFurry 基础捏脸')


def register():
    project.register()
    mouth.register()
    eye.register()
    mouth_opening.register()
    relief.register()
    runtime.register()
    import bpy
    bpy.types.VIEW3D_MT_object.append(menu_entry)


def unregister():
    import bpy
    bpy.types.VIEW3D_MT_object.remove(menu_entry)
    runtime.unregister()
    relief.unregister()
    mouth_opening.unregister()
    eye.unregister()
    mouth.unregister()
    project.unregister()

