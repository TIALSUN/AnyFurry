"""Integration checks for relief geometry and editable-project lifecycle."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import time
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import anyfurry_mouth as addon
from anyfurry_mouth import relief, project, runtime, eye, mouth_opening

OUTPUT = ROOT / 'weight_review/tests'
OUTPUT.mkdir(parents=True, exist_ok=True)
RESULTS = {}
addon.register()
assert bpy.ops.anyfurry.load_mouth() == {'FINISHED'}
scene = bpy.context.scene
shell = project.shell_for(scene)
assert runtime.flush(scene, force=True)


def passed(name, value=True):
    RESULTS[name] = value
    print('PASS', name, json.dumps(value, ensure_ascii=True), flush=True)


def source_hash():
    digest = hashlib.sha256()
    for key in shell.data.shape_keys.key_blocks:
        for point in key.data:
            digest.update(struct.pack('<3f', *point.co))
    for face in shell.data.polygons:
        digest.update(struct.pack('<I', len(face.vertices)))
        digest.update(struct.pack('<%dI' % len(face.vertices), *face.vertices))
    return digest.hexdigest()


def cutter_snapshot():
    result = {}
    for obj in scene.objects:
        if obj.get('anyfurry_relief_cutter') or obj.get('anyfurry_relief_preview'):
            result[obj.name] = ([tuple(v.co) for v in obj.data.vertices],
                                [tuple(e.vertices) for e in obj.data.edges],
                                [tuple(p.vertices) for p in obj.data.polygons], obj.hide_get())
    result['modifier'] = [(m.name, m.show_viewport, m.show_render, m.object.name if m.object else None)
                          for m in shell.modifiers if m.type == 'BOOLEAN']
    return result


def check_geometry():
    evaluated = shell.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        nonmanifold = sum(not edge.is_manifold for edge in bm.edges)
        assert nonmanifold == 0
        assert bm.calc_volume(signed=True) > 0
        tree = BVHTree.FromPolygons([v.co.copy() for v in mesh.vertices],
                                   [tuple(p.vertices) for p in mesh.polygons])
        _, _, _, ids, _, _ = relief.basis_data(shell)
        rear_error = max(tree.find_nearest(relief.deformed_vertex(shell, i))[3] for i in ids)
        assert rear_error < 1e-4, rear_error
        return {'nonmanifold': nonmanifold, 'rear_surface_error_mm': rear_error * relief.mm_factor(scene, shell),
                'hole_count': scene.af_relief.generated_count, 'volume_reduction_percent': scene.af_relief.volume_reduction}
    finally:
        bm.free()
        evaluated.to_mesh_clear()


def check_plan():
    plan = relief._plans[scene.as_pointer()]
    holes = plan['holes']
    assert holes and len(holes) % 2 == 0
    polygons = relief.opening_polygons(scene)
    minimum = float('inf')
    for i in range(0, len(holes), 2):
        a, b = holes[i:i+2]
        assert (Vector((-a['center'].x, a['center'].y, a['center'].z)) - b['center']).length < 1e-5
        assert (Vector((-a['normal'].x, a['normal'].y, a['normal'].z)) - b['normal']).length < 1e-5
        for hole in (a, b):
            for point in hole['preview']:
                outer = point - hole['normal'] * (.6 / plan['factor'])
                minimum = min(minimum, *(relief.distance_to_polygon(outer.x, outer.y, polygon) * plan['factor']
                                        for polygon in polygons))
    assert minimum >= scene.af_relief.clearance_mm - 1e-4, minimum
    return {'holes': len(holes), 'skipped': plan['rejected'], 'minimum_opening_clearance_mm': minimum,
            'regions': sorted({hole['region'] for hole in holes})}


baseline_source = source_hash()
assert not scene.af_relief.enabled and not relief.objects(scene, 'anyfurry_relief_cutter')
baseline_preset = project.parameters(scene)
scene.af_relief.enabled = True
assert runtime.flush(scene, force=True)
assert not relief.ready(scene)
assert not relief.objects(scene, 'anyfurry_relief_preview')[0].hide_get()
try:
    project.export_stl(scene, str(OUTPUT / 'preview_should_not_export.stl'))
    raise AssertionError('Preview was exported')
except ValueError:
    pass
assert not (OUTPUT / 'preview_should_not_export.stl').exists()
passed('preview_export_guard_and_default_off', check_plan())

started = time.perf_counter()
assert relief.generate(scene), scene.af_relief.error
assert relief.ready(scene)
assert source_hash() == baseline_source
default = check_geometry()
default['generation_seconds'] = time.perf_counter() - started
default['layout'] = check_plan()
assert default['layout']['regions'] == ['forehead', 'sides', 'top']
cutter = relief.objects(scene, 'anyfurry_relief_cutter')[0]
for offset in range(0, len(cutter.data.vertices), relief.SEGMENTS*4):
    for i in range(relief.SEGMENTS*2):
        left = cutter.data.vertices[offset+i].co
        right = cutter.data.vertices[offset+relief.SEGMENTS*2+i].co
        assert (Vector((-left.x, left.y, left.z)) - right).length < 1e-5
passed('default_geometry_symmetry_and_source_preserved', default)

# Parameter edits never mutate the generated cutter until explicit generation.
old_vertices = [tuple(v.co) for v in cutter.data.vertices]
scene.af_relief.diameter_mm = 16
assert runtime.flush(scene)
assert not relief.ready(scene)
assert [tuple(v.co) for v in cutter.data.vertices] == old_vertices
assert not relief.modifier_for(shell).show_render
assert relief.generate(scene)
passed('manual_generation_after_parameter_change')

# Inject failure after a real cutter mutation; wire edges and Boolean visibility
# must roll back too, including their saved IDProperty-backed data.
scene.af_relief.diameter_mm = 18
assert runtime.flush(scene, force=True)
before = cutter_snapshot()
old_update = relief.update_cutter
def fail_after_update(*args, **kwargs):
    old_update(*args, **kwargs)
    raise RuntimeError('controlled relief generation failure')
relief.update_cutter = fail_after_update
try:
    assert not relief.generate(scene)
    assert cutter_snapshot() == before
    assert scene.af_relief.error
finally:
    relief.update_cutter = old_update
assert relief.generate(scene), scene.af_relief.error
passed('relief_failure_rollback_and_retry')

# Preset schema 2 round-trip; v1 remains compatible and disables this new feature.
preset_path = OUTPUT / 'preset.json'
assert bpy.ops.anyfurry.save_preset(filepath=str(preset_path)) == {'FINISHED'}
saved_parameters = project.parameters(scene)
assert bpy.ops.anyfurry.reset_all() == {'FINISHED'}
assert bpy.ops.anyfurry.load_preset(filepath=str(preset_path)) == {'FINISHED'}
assert project.parameters(scene) == saved_parameters
legacy = dict(baseline_preset)
legacy['schema'] = 1
legacy.pop('relief')
project.apply_parameters(scene, legacy)
assert not scene.af_relief.enabled
bad = dict(saved_parameters)
bad['relief'] = dict(saved_parameters['relief'], diameter_mm=1000)
try:
    project.validate_parameters(bad)
    raise AssertionError('Invalid relief preset accepted')
except ValueError:
    pass
passed('new_and_legacy_preset_compatibility')

cases = [
    ('dense_small_mixed', 'DENSE', 8, 4, ('forehead','top','sides'), 1),
    ('large_sparse_mixed', 'SPARSE', 26, 12, ('forehead','top','sides'), -1),
    ('sides_only', 'MEDIUM', 18, 8, ('sides',), 0),
    ('top_only', 'MEDIUM', 18, 8, ('top',), 0),
]
checks = []
for name, density, diameter, gap, regions, sign in cases:
    assert bpy.ops.anyfurry.reset_all() == {'FINISHED'}
    for key, value in [('整体宽度', sign), ('整体高度', -sign), ('额头高度', sign),
                        ('眼部以下高度', -sign), ('鼻嘴上下位置', sign), ('嘴巴宽度', sign)]:
        shell.data.shape_keys.key_blocks[key].value = value
    scene.af_eye.preset = 'C' if sign > 0 else 'B' if sign < 0 else 'A'
    scene.af_eye.spacing = sign
    scene.af_eye.position = -sign
    scene.af_eye.angle = sign
    scene.af_mouth_opening.width = sign
    scene.af_mouth_opening.size = sign
    scene.af_mouth_opening.side_gap = sign
    p = scene.af_relief
    p.enabled, p.density, p.diameter_mm, p.clearance_mm = True, density, diameter, gap
    for region in ('forehead', 'top', 'sides'):
        setattr(p, region, region in regions)
    assert runtime.flush(scene, force=True), p.error
    layout = check_plan()
    assert relief.generate(scene), p.error
    check = check_geometry()
    check.update(case=name, layout=layout)
    checks.append(check)
    passed(name, check)
passed('combined_geometry_and_region_switches', checks)

# Undo/redo and save/reopen preserve the accepted design signature.
assert bpy.ops.anyfurry.reset_all() == {'FINISHED'}
scene.af_relief.enabled = True
assert relief.generate(scene)
bpy.ops.ed.undo_push(message='relief_generated')
scene.af_relief.diameter_mm = 16
assert runtime.flush(scene, force=True) and not relief.ready(scene)
bpy.ops.ed.undo_push(message='relief_changed')
bpy.ops.ed.undo()
scene = bpy.context.scene; shell = project.shell_for(scene)
assert runtime.flush(scene, force=True) and relief.ready(scene)
bpy.ops.ed.redo()
scene = bpy.context.scene; shell = project.shell_for(scene)
assert runtime.flush(scene, force=True) and not relief.ready(scene)
assert relief.generate(scene)
blend = OUTPUT / 'saved.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend))
bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)
scene = bpy.context.scene; shell = project.shell_for(scene)
assert runtime.flush(scene, force=True) and relief.ready(scene)
passed('undo_redo_and_save_reopen')

# Reimport the evaluated STL: source-only diagnostics do not prove export health.
stl = OUTPUT / 'head_relief_mm.stl'
triangles = project.export_stl(scene, str(stl))
assert stl.stat().st_size == 84 + 50*triangles
bpy.ops.wm.stl_import(filepath=str(stl))
imported = bpy.context.object
bm = bmesh.new()
try:
    bm.from_mesh(imported.data)
    assert not any(not edge.is_manifold for edge in bm.edges)
    assert bm.calc_volume(signed=True) > 0
finally:
    bm.free()
passed('stl_reimport_closed_mm', {'triangles': triangles, 'dimensions_mm': list(imported.dimensions)})

assert source_hash() == baseline_source
asset_hash = hashlib.sha256((ROOT/'anyfurry_mouth/assets/mouth_eye_base_v0281.blend').read_bytes()).hexdigest()
assert asset_hash == 'bb0c11cb9b784ff05e010a1db2077a447a75f96c512f429860e5f9ce8cd1e513'
scene.af_relief.enabled = False
assert runtime.flush(scene, force=True) and relief.ready(scene)
assert not relief.modifier_for(shell).show_render
addon.unregister();addon.register()
assert runtime.flush(bpy.context.scene, force=True)
passed('disable_reenable_and_asset_hash_unchanged')
(OUTPUT/'results.json').write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding='utf8')
print('RELIEF_INTEGRATION_PASS', flush=True)
