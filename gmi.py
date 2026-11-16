"""Optional GMI workspace integration — activates only when the addon is
installed from inside a GMI pipeline checkout (pipeline/registry/references.json
reachable four levels up). Generic installs never see any of this.

What it does when active:
  * import offers "Register in GMI registry" (default on): a reference new to
    the registry gets an entry (status "imported") pointing at the canonical
    3d_assets/props/<REF>/<REF>.blend artifact location.
  * the root collection is renamed PR-<ref> when the filename leads with a
    GMI reference code, keeping continuity with the existing prop convention.
"""
import datetime
import json
import os
import re
import sys

import bpy

_REF_RE = re.compile(r"^([a-z]{3}\d[a-z]\d{3,4})_", re.I)

_WS = None
_checked = False


def workspace():
    """GMI workspace root if detectable from this install location, else None."""
    global _WS, _checked
    if not _checked:
        _checked = True
        here = os.path.realpath(__file__)
        ws = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
        if os.path.isfile(os.path.join(ws, "pipeline", "registry", "references.json")):
            _WS = ws
    return _WS


def ref_of(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _REF_RE.match(stem)
    return m.group(1).lower() if m else None


def _families():
    """Shared prefix -> category table from the pipeline, with fallback."""
    ws = workspace()
    scripts = os.path.join(ws, "pipeline", "scripts") if ws else None
    if scripts and scripts not in sys.path and os.path.isdir(scripts):
        sys.path.insert(0, scripts)
        try:
            import families
            return families.category_of
        except Exception:
            pass
    table = {"dea": "implant", "def": "abutment", "exy": "instrument",
             "kda": "instrument", "kyc": "kit", "kyl": "instrument"}
    return lambda ref: table.get(ref[:3], "unknown")


def _registry_path():
    return os.path.join(workspace(), "pipeline", "registry", "references.json")


def register_import(root_collection, step_path):
    """Registry hook after a successful import. Returns a status string."""
    ws = workspace()
    ref = ref_of(step_path)
    if not ws or not ref:
        return ""
    reg_path = _registry_path()
    try:
        with open(reg_path, encoding="utf-8") as fh:
            reg = json.load(fh)
    except (OSError, ValueError) as e:
        return "GMI registry unreadable (%s)" % e
    if ref in reg.get("references", {}):
        return "GMI registry: %s known" % ref
    rel = os.path.relpath(os.path.abspath(step_path), ws)
    if rel.startswith(".."):
        rel = os.path.abspath(step_path)
    reg["references"][ref] = {
        "step": rel,
        "base": "3d_assets/props/%s/%s.blend" % (ref.upper(), ref.upper()),
        "category": _families()(ref),
        "mode": "plain",
        "rigged": False,
        "status": "imported",
        "note": ("added by STEPforge %s; interactive import — run step_build "
                 "to produce the base artifact" % datetime.date.today().isoformat()),
    }
    try:
        with open(reg_path, "w", encoding="utf-8") as fh:
            json.dump(reg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    except OSError as e:
        return "GMI registry write FAILED (%s)" % e
    root_collection.name = "PR-" + ref
    return "GMI registry: %s added (status: imported)" % ref


# ---------------------------------------------------------------------------
# D12 interactive save gate (plan D12 / runbook hard rules)
#
# Universal (any file): no save with missing libraries; no links into
# _archive/; no links outside the workspace. Prop artifacts only (files
# under 3d_assets/props/): no local materials under PR-<ref> collections —
# interactive working scenes are not prop files, so rule-assigned local
# materials there never trip the gate.
# ---------------------------------------------------------------------------


def gate_enabled():
    try:
        import bpy
        addon = bpy.context.preferences.addons.get("stepforge")
        if addon is None:
            return False
        return bool(getattr(addon.preferences, "enforce_save_gate", True))
    except Exception:
        return False


def run_gate():
    import bpy
    ws = workspace()
    failures = []
    for lib in bpy.data.libraries:
        path = bpy.path.abspath(lib.filepath)
        if not os.path.isfile(path):
            failures.append("missing library: %s" % path)
            continue
        if "_archive" in path.split(os.sep):
            failures.append("library links into _archive: %s" % path)
        if ws:
            rel = os.path.relpath(path, ws)
            if rel.startswith(".."):
                failures.append("library outside workspace: %s" % path)
    filepath = bpy.data.filepath or (bpy.context.blend_data.filepath or "")
    if os.sep + os.path.join("3d_assets", "props") + os.sep in filepath:
        for coll in bpy.data.collections:
            if not coll.name.startswith("PR-"):
                continue
            for obj in coll.all_objects:
                for slot in obj.material_slots:
                    mat = slot.material
                    if mat is not None and mat.library is None:
                        failures.append("local material in prop: %s on %s"
                                        % (mat.name, obj.name))
                        break
    return failures


def save_gate(dummy):
    """save_pre can report but cannot abort the save in Blender >= 2.8, so
    this stays a loud console flag; the actual blocking happens in
    WM_OT_gmi_save (Ctrl+S is remapped to it in the GMI workspace)."""
    if not workspace() or not gate_enabled():
        return
    failures = run_gate()
    if failures:
        print("STEPforge D12 gate: this file was saved WITH violations "
              "(Ctrl+S remap blocks them; disable in addon prefs): - "
              + " | ".join(failures))


class WM_OT_gmi_save(bpy.types.Operator):
    """Save, but refuse while validation gates are failing (plan D12)."""
    bl_idname = "wm.gmi_save"
    bl_label = "GMI Save (gated)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        failures = run_gate()
        if failures:
            self.report({"ERROR"},
                        "Save blocked by D12 gate: - " + " | ".join(failures))
            return {"CANCELLED"}
        return bpy.ops.wm.save_mainfile("INVOKE_DEFAULT")

    def invoke(self, context, event):
        return self.execute(context)


_keymap_items = []


def install_save_keymap():
    import bpy
    if not workspace() or _keymap_items:
        return
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.get("Window") or kc.keymaps.new(name="Window")
    for kmi in km.keymap_items:
        if kmi.idname == "wm.save_mainfile" and kmi.ctrl and not kmi.shift \
                and not kmi.alt and not kmi.oskey:
            kmi.active = False  # replaced by the gated save
    kmi = km.keymap_items.new("wm.gmi_save", "S", "PRESS", ctrl=True)
    _keymap_items.append((km, kmi))


def remove_save_keymap():
    for km, kmi in _keymap_items:
        km.keymap_items.remove(kmi)
    del _keymap_items[:]
