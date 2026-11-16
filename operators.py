"""STEPforge operators: import (multi-file, options panel) and re-import."""
import json
import os
import time

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty,
                       FloatProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper

from . import build, gmi, materials
from .build import OPTIONS_PROP, SOURCE_PROP, default_options
from .xcaf import StepDoc

QUALITY_PRESETS = {
    "DRAFT": (0.20, 0.60),
    "MEDIUM": (0.10, 0.35),
    "FINE": (0.05, 0.20),
    "EXTRA": (0.02, 0.10),
}


def _ruleset_items(self, context):
    items = [("none", "As imported (no rules)", "Only CAD colors, if any"),
             ("default", "Default", "Keyword rules + CAD colors"),
             ("custom", "Custom JSON…", "Rules from the path below")]
    if gmi.workspace():
        items.insert(2, ("gmi", "GMI product policy",
                         "Implants/abutments titanium, instruments steel"))
    return items


class IMPORT_OT_stepforge(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.stepforge"
    bl_label = "Import STEP"
    bl_description = ("Import STEP (.stp/.step) as an assembly tree with shared "
                      "instance meshes, CAD colors and rule-based materials")
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".stp"
    filter_glob: StringProperty(default="*.stp;*.step;*.STP;*.STEP", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: StringProperty(subtype="DIR_PATH")

    # tessellation
    quality: EnumProperty(
        name="Quality", default="FINE",
        items=[("DRAFT", "Draft", "Fast preview"),
               ("MEDIUM", "Medium", ""),
               ("FINE", "Fine", "Good default for lookdev"),
               ("EXTRA", "Extra fine", "Near-indistinguishable from CAD"),
               ("CUSTOM", "Custom", "Use the deflections below")])
    linear_deflection: FloatProperty(
        name="Linear (mm)", default=0.05, min=0.0001, soft_max=1.0)
    angular_deflection: FloatProperty(
        name="Angular (rad)", default=0.20, min=0.01, soft_max=1.5)

    # structure
    collection_mode: EnumProperty(
        name="Collections", default="TREE",
        items=[("TREE", "Assembly tree", "Nested collections mirroring the CAD structure"),
               ("FLAT", "Flat", "All objects directly under the root collection")])
    sort_alpha: BoolProperty(
        name="Sort A–Z", default=True,
        description="Sort collections and objects alphabetically")
    share_instances: BoolProperty(
        name="Share instance meshes", default=True,
        description="One mesh per unique part; instances reference it (big memory win)")
    root_prefix: StringProperty(name="Name prefix", default="")
    scale: FloatProperty(
        name="Scale", default=1.0, min=0.000001,
        description="Extra multiplier on top of the automatic unit conversion")

    # appearance
    import_colors: BoolProperty(
        name="CAD colors", default=True,
        description="Read surface colors from the STEP file (viewport + materials)")
    color_materials: BoolProperty(
        name="Colors as materials", default=False,
        description="Create a shared material per unique CAD color")
    ruleset: EnumProperty(name="Material rules", items=_ruleset_items, default=1)
    custom_rules_path: StringProperty(
        name="Custom rules", subtype="FILE_PATH",
        description="JSON ruleset: {\"rules\": [{\"field\": \"name|color\", \"pattern\": ..., \"material\": preset|\"color\"}]}")
    smooth: BoolProperty(name="Smooth shading", default=False)

    # optional GMI workspace integration
    gmi_register: BoolProperty(
        name="Register in GMI registry", default=True,
        description="Offered only inside the GMI workspace")

    def draw(self, context):
        l = self.layout
        col = l.column(align=True)
        col.label(text="Tessellation:")
        col.prop(self, "quality")
        if self.quality == "CUSTOM":
            col.prop(self, "linear_deflection")
            col.prop(self, "angular_deflection")
        col = l.column(align=True)
        col.label(text="Structure:")
        col.prop(self, "collection_mode")
        col.prop(self, "sort_alpha")
        col.prop(self, "share_instances")
        col.prop(self, "root_prefix")
        col.prop(self, "scale")
        col = l.column(align=True)
        col.label(text="Appearance:")
        col.prop(self, "import_colors")
        col.prop(self, "color_materials")
        col.prop(self, "ruleset")
        if self.ruleset == "custom":
            col.prop(self, "custom_rules_path")
        col.prop(self, "smooth")
        if gmi.workspace():
            col = l.column(align=True)
            col.label(text="GMI workspace:")
            col.prop(self, "gmi_register")

    def _deflections(self):
        if self.quality == "CUSTOM":
            return self.linear_deflection, self.angular_deflection
        return QUALITY_PRESETS[self.quality]

    def _options(self):
        o = default_options()
        o.update({
            "collection_mode": self.collection_mode,
            "sort_alpha": self.sort_alpha,
            "import_colors": self.import_colors,
            "color_materials": self.color_materials,
            "ruleset": "" if self.ruleset == "none" else self.ruleset,
            "custom_rules_path": self.custom_rules_path,
            "share_instances": self.share_instances,
            "scale": self.scale,
            "smooth": self.smooth,
            "root_prefix": self.root_prefix,
            "quality": self.quality,
            "linear": self._deflections()[0],
            "angular": self._deflections()[1],
            "gmi_register": bool(self.gmi_register),
        })
        return o

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        paths = []
        if self.files:
            paths = [os.path.join(self.directory, f.name) for f in self.files]
        elif self.filepath:
            paths = [self.filepath]
        if not paths:
            self.report({"ERROR"}, "No STEP file selected.")
            return {"CANCELLED"}

        options = self._options()
        wm = context.window_manager
        try:
            wm.progress_begin(0, len(paths))
        except Exception:
            wm = None
        reports, failures = [], []
        for pi, path in enumerate(paths):
            if wm:
                wm.progress_update(pi)
            try:
                reports.append(self._import_one(context, path, options))
            except Exception as e:
                import traceback
                failures.append((os.path.basename(path), str(e).strip()[:200]))
                print("STEPforge import failed for %s:\n%s" % (path, traceback.format_exc()))
        if wm:
            wm.progress_end()

        for ok in reports:
            self.report({"INFO"}, ok)
        for name, err in failures:
            self.report({"ERROR"}, "%s: %s" % (name, err))
        if not reports:
            return {"CANCELLED"}
        return {"FINISHED"}

    def _import_one(self, context, path, options):
        from OCP.BRep import BRep_Builder
        from OCP.BRepGProp import BRepGProp
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.GProp import GProp_GProps
        from OCP.TopoDS import TopoDS_Compound

        t0 = time.time()
        doc = StepDoc(path)
        linear, angular = options["linear"], options["angular"]
        compound = TopoDS_Compound()
        BRep_Builder().MakeCompound(compound)
        for proto in doc.protos.values():
            BRep_Builder().Add(compound, proto.shape)
        BRepMesh_IncrementalMesh(compound, linear, False, angular, True)

        res = build.build(doc, options)
        if res.stats["built"] == 0:
            raise RuntimeError("No tessellated geometry (try a finer quality)")

        # materials
        rules = materials.load_ruleset(options["ruleset"], options["custom_rules_path"])
        if rules:
            log = materials.apply_to_objects(res.objects, rules)
            res.root["stepforge/materials"] = ", ".join(
                sorted(log.by_preset) + ["%d CAD-color" % len(log.by_color)])
        elif options["color_materials"]:
            # no rules: everything falls back to its CAD color
            log = materials.apply_to_objects(res.objects, [])
            res.root["stepforge/materials"] = "%d CAD-color" % len(log.by_color)
        else:
            res.root["stepforge/materials"] = "none"

        # optional GMI registry hook
        if options.get("gmi_register") and gmi.workspace():
            note = gmi.register_import(res.root, path)
            if note:
                res.root["stepforge/gmi"] = note

        area = GProp_GProps()
        BRepGProp.SurfaceProperties_s(compound, area)
        dt = time.time() - t0
        msg = ("%s: %d object(s), %d unique mesh(es), %d tri(s), %.1f cm², "
               "unit %s, %.1fs"
               % (res.root.name, res.stats["built"], res.stats["unique_meshes"],
                  res.stats["tris"], area.Mass() / 100.0, doc.unit, dt))
        res.root["stepforge/area_cm2"] = round(area.Mass() / 100.0, 3)
        return msg


class OBJECT_OT_stepforge_reimport(bpy.types.Operator):
    bl_idname = "object.stepforge_reimport"
    bl_label = "Re-import from Source STEP"
    bl_description = ("Rebuild this STEPforge import from its source file "
                      "(product changed: replace the STEP, run this, done)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        from datetime import datetime
        from OCP.BRep import BRep_Builder
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopoDS import TopoDS_Compound

        obj = context.active_object
        root = build.find_root_collection(obj, context.scene)
        if root is None:
            self.report({"ERROR"},
                        "Active object is not inside a STEPforge import.")
            return {"CANCELLED"}
        src = root.get(SOURCE_PROP)
        if not src or not os.path.isfile(src):
            self.report({"ERROR"}, "Source STEP missing: %s" % src)
            return {"CANCELLED"}
        try:
            options = json.loads(root.get(OPTIONS_PROP) or "{}")
        except ValueError:
            options = {}
        merged = default_options()
        merged.update(options)

        # wipe the previous build; the root collection and its props stay
        for coll in list(root.children):
            bpy.data.collections.remove(coll)
        for ob in list(root.objects):
            bpy.data.objects.remove(ob, do_unlink=True)

        try:
            doc = StepDoc(src)
            compound = TopoDS_Compound()
            BRep_Builder().MakeCompound(compound)
            for proto in doc.protos.values():
                BRep_Builder().Add(compound, proto.shape)
            BRepMesh_IncrementalMesh(compound, float(merged.get("linear", 0.05)),
                                     False, float(merged.get("angular", 0.20)), True)
            res = build.build(doc, merged, root_collection=root)
        except Exception as e:
            self.report({"ERROR"}, "Re-import failed: %s" % e)
            return {"CANCELLED"}

        # re-apply the stored material rules
        rules = materials.load_ruleset(merged.get("ruleset", ""),
                                       merged.get("custom_rules_path", ""))
        if rules and res.objects:
            log = materials.apply_to_objects(res.objects, rules)
            root["stepforge/materials"] = ", ".join(sorted(log.by_preset))
        root["stepforge/reimported"] = datetime.now().isoformat(timespec="seconds")
        root["stepforge/stats"] = "%d object(s), %d unique mesh(es), %d tri(s)" % (
            res.stats["built"], res.stats["unique_meshes"], res.stats["tris"])

        self.report({"INFO"}, "%s: re-imported, %d object(s)"
                    % (root.name, res.stats["built"]))
        return {"FINISHED"}


classes = (IMPORT_OT_stepforge, OBJECT_OT_stepforge_reimport)
