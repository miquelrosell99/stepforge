# STEPforge — a state-of-the-art STEP importer for Blender.
#
# Generic: no workspace assumptions. If the addon happens to be installed from
# inside the GMI pipeline checkout, an optional registry hook and the D12
# save gate activate (see gmi.py); everywhere else they stay invisible.
#
# Install: symlink this folder into the Blender addons directory
#   ln -s "<path>/stepforge" ~/.config/blender/5.4/scripts/addons/stepforge
# then enable "Import-Export: STEPforge" in Preferences > Add-ons.
import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

bl_info = {
    "name": "STEPforge — STEP Importer",
    "author": "RosellRamos — GMI Dental Implantology pipeline",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > STEP (.stp); Object > Re-import from Source STEP",
    "description": ("Assembly-tree STEP/IGES import with shared instance meshes, unit "
                    "conversion, CAD colors, rule-based PBR materials, re-import"),
    "category": "Import-Export",
}


class STEPforgePreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    default_ruleset: EnumProperty(
        name="Default material ruleset",
        items=[("default", "Default", ""), ("none", "As imported", "")],
        default="default")
    custom_rules_path: StringProperty(
        name="Custom ruleset (JSON)", subtype="FILE_PATH", default="")
    enforce_save_gate: BoolProperty(
        name="D12 save gate (GMI workspace)", default=True,
        description="Gated save refuses missing/outside/_archive libraries, "
                    "and local materials in prop artifacts under 3d_assets/props/")
    remap_ctrl_s: BoolProperty(
        name="Remap Ctrl+S to gated save", default=True,
        update=lambda self, context: _apply_remap(self.remap_ctrl_s),
        description="Ctrl+S runs the gated save instead of the plain save")

    def draw(self, context):
        self.layout.prop(self, "default_ruleset")
        self.layout.prop(self, "custom_rules_path")
        col = self.layout.column(heading="D12 save gate")
        col.prop(self, "enforce_save_gate")
        col.prop(self, "remap_ctrl_s")


def _apply_remap(active):
    from . import gmi
    for _km, kmi in gmi._keymap_items:
        kmi.active = bool(active)


def _menu_import(self, context):
    self.layout.operator("import_scene.stepforge", text="STEP (.stp/.step)")


def _menu_object(self, context):
    self.layout.separator()
    self.layout.operator("object.stepforge_reimport",
                         text="STEP: Re-import from Source")


_registered = False


def register():
    global _registered
    if _registered:
        return
    from . import gmi, operators
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(STEPforgePreferences)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.VIEW3D_MT_object.append(_menu_object)
    if gmi.workspace():
        if gmi.save_gate not in bpy.app.handlers.save_pre:
            bpy.app.handlers.save_pre.append(gmi.save_gate)
        bpy.utils.register_class(gmi.WM_OT_gmi_save)
        gmi.install_save_keymap()
    _registered = True


def unregister():
    global _registered
    if not _registered:
        return
    from . import gmi
    gmi.remove_save_keymap()
    if gmi.save_gate in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(gmi.save_gate)
    if hasattr(bpy.types, "WM_OT_gmi_save"):
        bpy.utils.unregister_class(gmi.WM_OT_gmi_save)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.VIEW3D_MT_object.remove(_menu_object)
    bpy.utils.unregister_class(STEPforgePreferences)
    from . import operators
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
    _registered = False
