"""STEPforge materials: physically-based presets + a rule-based mapping engine.

Two complementary mechanisms (both optional):

  * CAD colors -> materials: unique STEP colors become shared materials
    ("STEP #rrggbb"), so a colored assembly renders without a thousand
    duplicate datablocks.
  * Rule mapping: ordered rules match an object's STEP name (regex) or CAD
    color (exact hex) and assign a preset from the built-in PBR library
    (titanium, stainless steel, DLC, PEEK, ...). First match wins; no match
    falls back to the CAD color material, if any.

Rulesets ship with the addon; a custom ruleset JSON can override them.
Assignments are recorded on the object as custom props — auditable, and the
registry never tracks materials (GMI decision 2026-11-16).
"""
import json
import os
import re

import bpy

# (base_color rgba, metallic, roughness) — plausible display values, not
# measured BRDFs: calibrated for lookdev under Filmic/AgX, not for physics.
PRESETS = {
    "titanium":            ((0.54, 0.56, 0.58, 1.0), 1.0, 0.38),
    "titanium_polished":   ((0.55, 0.57, 0.59, 1.0), 1.0, 0.15),
    "titanium_blasted":    ((0.52, 0.54, 0.56, 1.0), 1.0, 0.62),
    "steel":               ((0.60, 0.62, 0.64, 1.0), 1.0, 0.30),
    "steel_polished":      ((0.62, 0.64, 0.66, 1.0), 1.0, 0.10),
    "steel_satin":         ((0.58, 0.60, 0.62, 1.0), 1.0, 0.45),
    "steel_black":         ((0.16, 0.17, 0.18, 1.0), 0.9, 0.40),
    "aluminum":            ((0.75, 0.76, 0.78, 1.0), 1.0, 0.35),
    "aluminum_anodized":   ((0.45, 0.47, 0.52, 1.0), 0.85, 0.42),
    "brass":               ((0.72, 0.55, 0.30, 1.0), 1.0, 0.32),
    "copper":              ((0.72, 0.45, 0.32, 1.0), 1.0, 0.30),
    "chrome":              ((0.70, 0.72, 0.75, 1.0), 1.0, 0.08),
    "gold":                ((0.83, 0.68, 0.32, 1.0), 1.0, 0.22),
    "cobalt_chrome":       ((0.68, 0.70, 0.73, 1.0), 1.0, 0.28),
    "dlc":                 ((0.09, 0.09, 0.11, 1.0), 0.85, 0.16),
    "zirconia":            ((0.93, 0.92, 0.90, 1.0), 0.0, 0.18),
    "alumina":             ((0.94, 0.93, 0.92, 1.0), 0.0, 0.35),
    "peek":                ((0.86, 0.79, 0.62, 1.0), 0.0, 0.50),
    "peek_natural":        ((0.78, 0.72, 0.58, 1.0), 0.0, 0.58),
    "pmma":                ((0.90, 0.92, 0.93, 1.0), 0.0, 0.12),
    "silicone":            ((0.72, 0.70, 0.66, 1.0), 0.0, 0.65),
    "pvc":                 ((0.75, 0.76, 0.78, 1.0), 0.0, 0.45),
    "nylon":               ((0.80, 0.78, 0.74, 1.0), 0.0, 0.55),
    "abs":                 ((0.55, 0.56, 0.58, 1.0), 0.0, 0.42),
    "rubber":              ((0.12, 0.12, 0.13, 1.0), 0.0, 0.85),
    "delrin":              ((0.88, 0.86, 0.80, 1.0), 0.0, 0.38),
}

# Ordered rules: {"field": "name"|"color", "pattern": regex, "hex": "#rrggbb",
#                 "material": preset name | "color" | None}
# First match wins. Materials are preset names, or "color" to keep the CAD
# color, or None to leave the object unassigned.
RULESETS = {
    "default": [
        {"field": "name", "pattern": r"titan", "material": "titanium"},
        {"field": "name", "pattern": r"dlc", "material": "dlc"},
        {"field": "name", "pattern": r"peek", "material": "peek"},
        {"field": "name", "pattern": r"zirco", "material": "zirconia"},
        {"field": "name", "pattern": r"nitinol|muelle|spring", "material": "steel_polished"},
        {"field": "name", "pattern": r"steel|stainless|inox|acero", "material": "steel"},
        {"field": "name", "pattern": r"alum", "material": "aluminum"},
        {"field": "name", "pattern": r"brass|lat[oó]n", "material": "brass"},
        {"field": "name", "pattern": r"silicona|silicone", "material": "silicone"},
    ],
    # GMI product policy (STEP_ff00ff era): implants + abutments titanium,
    # instruments stainless steel; family codes lead GMI file names.
    "gmi": [
        {"field": "name", "pattern": r"^(dea|def|den)", "material": "titanium"},
        {"field": "name", "pattern": r"^(kyl|kda|kyc|exy|dil|enl|tpp)", "material": "steel"},
        {"field": "name", "pattern": r"dlc", "material": "dlc"},
        {"field": "name", "pattern": r"peek", "material": "peek"},
        {"field": "name", "pattern": r"nitinol|muelle|spring", "material": "steel_polished"},
        {"field": "name", "pattern": r"silicona|silicone", "material": "silicone"},
        {"field": "name", "pattern": r"zirco", "material": "zirconia"},
        {"field": "name", "pattern": r"titan", "material": "titanium"},
        {"field": "name", "pattern": r"acero|inox", "material": "steel"},
    ],
}


def load_ruleset(name, custom_path=None):
    """Ordered rules for a ruleset name, or [] for 'none'/unknown."""
    if name == "custom" and custom_path:
        try:
            with open(os.path.abspath(os.path.expanduser(custom_path)),
                      encoding="utf-8") as fh:
                data = json.load(fh)
            rules = data.get("rules", data if isinstance(data, list) else [])
            return [r for r in rules if isinstance(r, dict)]
        except (OSError, ValueError):
            return []
    return list(RULESETS.get(name, []))


def color_material_name(hexcolor):
    return "STEP %s" % (hexcolor or "").lower()


def make_preset_material(preset):
    mat = bpy.data.materials.get(preset)
    if mat is None:
        mat = bpy.data.materials.new(preset)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        rgba, metallic, rough = PRESETS[preset]
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = rough
    return mat


def make_color_material(hexcolor):
    name = color_material_name(hexcolor)
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        h = hexcolor.lstrip("#")
        rgba = tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) + (1.0,)
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Metallic"].default_value = 0.2
        bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def match_rule(rules, name, hexcolor):
    """First matching rule's material spec, or None. Spec: preset | 'color' | None."""
    for rule in rules:
        field = rule.get("field", "name")
        if field == "color":
            want = (rule.get("hex") or "").lower()
            if want and hexcolor and want == hexcolor.lower():
                return rule.get("material")
        else:
            pat = rule.get("pattern", "")
            if pat and re.search(pat, name or "", re.I):
                return rule.get("material")
    return None


class AssignmentLog(object):
    """Per-object material decisions, recorded as custom props."""

    def __init__(self):
        self.by_preset = set()
        self.by_color = set()
        self.unmatched = 0

    def record(self, obj, spec, hexcolor):
        if spec in PRESETS:
            obj["stepforge/material"] = spec
            self.by_preset.add(spec)
        elif spec == "color" and hexcolor:
            obj["stepforge/material"] = color_material_name(hexcolor)
            self.by_color.add(hexcolor.lower())
        else:
            self.unmatched += 1


def apply_to_objects(entries, rules, log=None):
    """Assign materials.

    entries: iterable of (obj, name, hexcolor). Shared meshes keep their
    prototype material on the data; conflicting per-instance assignments
    become OBJECT-linked slot overrides, so instancing is never broken.
    """
    if log is None:
        log = AssignmentLog()
    for obj, name, hexcolor in entries:
        spec = match_rule(rules, name, hexcolor) if rules else None
        if spec is None and hexcolor:
            spec = "color"
        if spec in PRESETS:
            mat = make_preset_material(spec)
        elif spec == "color" and hexcolor:
            mat = make_color_material(hexcolor)
        else:
            log.record(obj, None, hexcolor)
            continue
        existing = obj.data.materials[0] if len(obj.data.materials) else None
        if existing is None:
            obj.data.materials.append(mat)
        elif existing.name != mat.name:
            if len(obj.material_slots) and obj.material_slots[0].link == "OBJECT":
                obj.material_slots[0].material = mat
            else:
                # shared mesh with a different decision: per-object override
                if not obj.material_slots:
                    obj.data.materials.append(existing)  # ensure slot exists
                obj.material_slots[0].link = "OBJECT"
                obj.material_slots[0].material = mat
        log.record(obj, spec, hexcolor)
    return log
