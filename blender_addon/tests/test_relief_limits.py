import bpy,json,sys
from pathlib import Path
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import anyfurry_mouth as addon
from anyfurry_mouth import relief,runtime,project
addon.register();bpy.ops.anyfurry.load_mouth();scene=bpy.context.scene;shell=project.shell_for(scene)
scene.af_relief.enabled=True;assert relief.generate(scene),scene.af_relief.error
original_scale=shell.scale.copy()
cutter=relief.objects(scene,'anyfurry_relief_cutter')[0]
before=[tuple(v.co) for v in cutter.data.vertices]
shell.scale.x*=1.2;bpy.context.view_layer.update()
assert not runtime.flush(scene,force=True)
assert scene.af_relief.error and not relief.ready(scene)
assert not relief.modifier_for(shell).show_render
assert relief.objects(scene,'anyfurry_relief_preview')[0].hide_get()
assert [tuple(v.co) for v in cutter.data.vertices]==before
shell.scale=original_scale;bpy.context.view_layer.update()
assert runtime.flush(scene,force=True) and relief.ready(scene)
print('PASS nonuniform_scale_rejection_and_recovery',flush=True)
shell.scale=original_scale*2;bpy.context.view_layer.update()
assert relief.generate(scene),scene.af_relief.error
plan=relief._plans[scene.as_pointer()];hole=plan['holes'][0]
point=cutter.data.vertices[0].co-hole['center']
radial=point-hole['normal']*point.dot(hole['normal'])
actual=(shell.matrix_world.to_3x3()@radial).length*scene.unit_settings.scale_length*1000
assert abs(actual-scene.af_relief.diameter_mm*.5)<1e-4
print('PASS uniform_scale_physical_mm',actual,flush=True)
for field in ('forehead','top','sides'):setattr(scene.af_relief,field,False)
assert runtime.flush(scene,force=True)
assert scene.af_relief.planned_count==0 and not relief.generate(scene)
assert not relief.modifier_for(shell).show_render
try:
 project.export_stl(scene,str(ROOT/'weight_review/empty_should_not_export.stl'));raise AssertionError('empty pending feature exported')
except ValueError:pass
assert not (ROOT/'weight_review/empty_should_not_export.stl').exists()
result={'nonuniform_scale_rejection_keeps_cutter':True,'invalid_pending_holes_hidden':True,
        'recovery_after_scale_restore':True,'uniform_scale_2x_hole_radius_mm':actual,
        'empty_regions_export_blocked':True,'material':None,'printing_process':None,'physical_validation':'not_checked'}
(ROOT/'weight_review/limits_results.json').write_text(json.dumps(result,indent=2),encoding='utf8')
print('RELIEF_LIMITS_PASS',flush=True)
