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
