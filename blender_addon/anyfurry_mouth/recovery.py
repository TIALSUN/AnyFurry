"""Keep opening geometry and modifier state intact when a refresh fails."""
import bpy
from .privacy import error_message


def is_cutter(obj):
    return obj.type == 'MESH' and (obj.get('anyfurry_eye_cutter') or obj.get('anyfurry_mouth_cutter'))


def copy_property(value):
    # IDPropertyArray/Group wrappers reference Blender-owned memory. Copy them
    # before deleting or changing the original property during rollback.
    if hasattr(value, 'to_dict'):
        return {key: copy_property(item) for key, item in value.to_dict().items()}
    if hasattr(value, 'to_list'):
        return value.to_list()
    return value


class OpeningTransaction:
    def __init__(self, scene, shell):
        self.scene = scene
        self.shell = shell
        self.objects = []
        self.modifiers = []

    def __enter__(self):
        self.original_objects = {o.as_pointer() for o in bpy.data.objects}
        for obj in self.scene.objects:
            if is_cutter(obj):
                original = ([tuple(v.co) for v in obj.data.vertices],
                            [tuple(p.vertices) for p in obj.data.polygons],
                            [(p.use_smooth, p.material_index) for p in obj.data.polygons])
                props = {key: copy_property(value) for key, value in obj.items()}
                self.objects.append((obj, original, obj.matrix_world.copy(), props))
        self.modifiers = [(m, m.object, m.operation, m.solver, m.show_viewport, m.show_render)
                          for m in self.shell.modifiers
                          if m.type == 'BOOLEAN' and m.name.startswith('AnyFurry_实体')]
        self.original_modifiers = {m.as_pointer() for m in self.shell.modifiers}
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is not None:
            for mod in list(self.shell.modifiers):
                if mod.as_pointer() not in self.original_modifiers:
                    self.shell.modifiers.remove(mod)
            for mod, obj, operation, solver, viewport, render in self.modifiers:
                mod.object, mod.operation, mod.solver = obj, operation, solver
                mod.show_viewport, mod.show_render = viewport, render
        for obj, original, matrix, props in self.objects:
            if error_type is not None:
                # Restore in place: swapping meshes used by evaluated booleans
                # can leave a live dependency graph pointing at freed data.
                verts, faces, properties = original
                obj.data.clear_geometry()
                obj.data.from_pydata(verts, [], faces)
                for face, (smooth, material) in zip(obj.data.polygons, properties):
                    face.use_smooth = smooth
                    face.material_index = material
                obj.data.update()
                obj.matrix_world = matrix
                for key in list(obj.keys()):
                    del obj[key]
                for key, value in props.items():
                    obj[key] = value
        if error_type is not None:
            for obj in list(bpy.data.objects):
                if obj.as_pointer() not in self.original_objects and is_cutter(obj):
                    mesh = obj.data
                    bpy.data.objects.remove(obj, do_unlink=True)
                    if mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
        return False


def show_error(layout, settings):
    if settings.error:
        box = layout.box()
        box.alert = True
        box.label(text='开孔更新失败，已保留上一次结果', icon='ERROR')
        # Keep long exception messages readable in the narrow sidebar.
        for start in range(0, len(settings.error), 24):
            box.label(text=settings.error[start:start + 24])


def set_error(settings, error):
    settings.error = '%s: %s' % (type(error).__name__, error_message(error)[:240])
