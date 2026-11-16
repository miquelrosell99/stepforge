# STEPforge — a state-of-the-art STEP importer for Blender.
#
# Generic: no workspace assumptions. If the addon happens to be installed from
# inside the GMI pipeline checkout, an optional registry hook appears in the
# import options (see gmi.py); everywhere else it stays invisible.
#
# Install: symlink this folder into the Blender addons directory
#   ln -s "<path>/stepforge" ~/.config/blender/5.4/scripts/addons/stepforge
# then enable "Import-Export: STEPforge" in Preferences > Add-ons.
import bpy
from bpy.props import EnumProperty, StringProperty

bl_info = {
    "name": "STEPforge — STEP Importer",
    "author": "RosellRamos — GMI Dental Implantology pipeline",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import > STEP (.stp); Object > Re-import from Source STEP",
    "description": ("Assembly-tree STEP import with shared instance meshes, unit "
                    "conversion, CAD colors and rule-based PBR materials"),
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

    def draw(self, context):
        self.layout.prop(self, "default_ruleset")
        self.layout.prop(self, "custom_rules_path")


def _menu_import(self, context):
    self.layout.operator("import_scene.stepforge", text="STEP (.stp/.step)")


def _menu_object(self, context):
    self.layout.separator()
    self.layout.operator("object.stepforge_reimport",
                         text="STEP: Re-import from Source")


def register():
    from . import operators
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(STEPforgePreferences)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.VIEW3D_MT_object.append(_menu_object)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    bpy.types.VIEW3D_MT_object.remove(_menu_object)
    bpy.utils.unregister_class(STEPforgePreferences)
    from . import operators
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
