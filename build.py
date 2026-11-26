"""Build Blender collections/objects from a StepDoc (see xcaf.py).

Mesh extraction: one mesh per prototype, shared by all its instances
(instance transforms become object matrices — CAD-instancing done right).
Colors land on obj.color (viewport) and optionally become shared materials.
"""
import datetime
import hashlib
import os
import time

import bpy
from mathutils import Matrix

from . import materials
from .xcaf import position_scale

SOURCE_PROP = "stepforge/source"
OPTIONS_PROP = "stepforge/options"


def sanitize(name):
    name = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in (name or "")).strip()
    return name.replace(" ", "_") or "part"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _t16_to_matrix(t16):
    return Matrix((t16[0:4], t16[4:8], t16[8:12], t16[12:16]))


def face_node_normals(face, tri):
    """Per-node normals (1-indexed list of gp_Dir) for one tessellated face.

    Preferred: exact CAD surface normals via GeomLProp at each UV node
    (crisp hard edges; singularity nodes filled from tessellation
    averages). Fallback: Poly_Triangulation averages. Face orientation
    applied here; the caller rotates with the face location.
    Shared with the GMI step_build pipeline (single source).
    """
    from OCP.TopAbs import TopAbs_REVERSED
    from OCP.BRep import BRep_Tool
    fn = None
    from_cad = False
    if tri.HasUVNodes():
        try:
            from OCP.GeomLProp import GeomLProp_SLProps
            surf = BRep_Tool.Surface_s(face)
            fn = [None]
            for i in range(1, tri.NbNodes() + 1):
                uv = tri.UVNode(i)
                pr = GeomLProp_SLProps(surf, uv.X(), uv.Y(), 1, 1e-9)
                d = pr.Normal() if pr.IsNormalDefined() else None
                if d is not None and face.Orientation() == TopAbs_REVERSED:
                    d.Reverse()
                fn.append(d)
            if any(d is None for d in fn[1:]):
                if not tri.HasNormals():
                    tri.ComputeNormals()
                if tri.HasNormals():
                    for i in range(1, tri.NbNodes() + 1):
                        if fn[i] is None:
                            fn[i] = tri.Normal(i)
                if any(d is None for d in fn[1:]):
                    fn = None
            if fn is not None:
                from_cad = True
        except Exception:
            fn = None
    if fn is None:
        try:
            if not tri.HasNormals():
                tri.ComputeNormals()
            if tri.HasNormals():
                fn = [None] + [tri.Normal(i) for i in range(1, tri.NbNodes() + 1)]
        except Exception:
            fn = None
    if fn is None:
        return None, False
    return fn, from_cad


def _mesh_from_shape(name, shape, scale, smooth=False):
    """Tessellation -> (mesh, content signature) in metres, or (None, None).

    The signature hashes the tessellated content in the prototype's local
    space, so exploded exports (every instance its own product definition)
    still share one mesh datablock.
    """
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    import hashlib

    verts, faces, seen = [], [], {}
    lnorms = []
    stats = {"cad_normal_faces": 0, "avg_normal_faces": 0, "degenerate_faces": 0}
    h = hashlib.sha1()
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            fn, from_cad = face_node_normals(face, tri)
            if from_cad:
                stats["cad_normal_faces"] += 1
            elif fn:
                stats["avg_normal_faces"] += 1
            nodes = []
            nn = []
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                key = (round(p.X(), 4), round(p.Y(), 4), round(p.Z(), 4))
                h.update(repr(key).encode())
                if key not in seen:
                    seen[key] = len(verts)
                    verts.append((p.X() * scale, p.Y() * scale, p.Z() * scale))
                nodes.append(seen[key])
                d = fn[i] if fn else None
                if d is not None:
                    d = d.Transformed(trsf)  # rotate with the face location
                nn.append(d)
            for i in range(1, tri.NbTriangles() + 1):
                t = tri.Triangle(i)
                try:
                    a, b, c = t.Get()
                except TypeError:
                    a, b, c = t.Get1(), t.Get2(), t.Get3()
                # reversed winding: OCCT outward -> Blender CCW front face
                f = (nodes[c - 1], nodes[b - 1], nodes[a - 1])
                if len(set(f)) < 3:
                    # nodes weld to a zero-area triangle: invisible, and it
                    # crashes Blender's split-normals path on 5.4 alphas
                    stats["degenerate_faces"] += 1
                    continue
                h.update(repr(f).encode())
                faces.append(f)
                if fn:
                    for idx in (c, b, a):
                        d = nn[idx - 1]
                        lnorms.append((d.X(), d.Y(), d.Z()))
        exp.Next()
    if not faces:
        return None, None
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    if smooth and lnorms and len(lnorms) == len(faces) * 3:
        # smooth shading WITH CAD split normals: curved surfaces shade like
        # the CAD model, hard edges stay crisp.
        # 1) geometry orientation: recalc_face_normals makes winding
        #    consistent and outward (OCCT STEP orientation is unreliable).
        #    Loop normals are re-matched by vertex index afterwards.
        # 2) shading normals: CAD normal per corner, sign-matched to the
        #    outward polygon normal; zero/NaN -> polygon normal.
        import bmesh
        import math as _math
        vmap = [{faces[fi][k]: lnorms[fi * 3 + k] for k in range(3)}
                for fi in range(len(faces))]
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(mesh)
        bm.free()
        mesh.update()
        clean = []
        for p in mesh.polygons:
            pn = p.normal
            for li in range(p.loop_start, p.loop_start + p.loop_total):
                vid = mesh.loops[li].vertex_index
                n = vmap[p.index][vid]
                bad = n is None or not all(_math.isfinite(c) for c in n) or \
                    (n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) < 1e-12
                if bad:
                    n = tuple(pn)
                elif (n[0] * pn.x + n[1] * pn.y + n[2] * pn.z) < 0:
                    n = (-n[0], -n[1], -n[2])
                clean.append(n)
        mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        mesh.normals_split_custom_set(clean)
    mesh["stepforge/cad_normal_faces"] = stats["cad_normal_faces"]
    mesh["stepforge/avg_normal_faces"] = stats["avg_normal_faces"]
    mesh["stepforge/degenerate_faces"] = stats["degenerate_faces"]
    mesh.update()
    return mesh, h.hexdigest()


def default_options():
    return {
        "collection_mode": "TREE",     # TREE | FLAT
        "sort_alpha": True,
        "import_colors": True,
        "color_materials": False,
        "ruleset": "default",          # none | default | gmi | custom
        "custom_rules_path": "",
        "share_instances": True,
        "scale": 1.0,                  # extra user multiplier (unit scale is automatic)
        "smooth": False,
        "heal": False,
        "root_prefix": "",
        "gmi_register": True,
    }


class BuildResult(object):
    def __init__(self):
        self.root = None
        self.objects = []        # (obj, name, hexcolor) for material pass
        self.stats = {"instances": 0, "built": 0, "skipped_empty": 0,
                      "prototypes": 0, "unique_meshes": 0, "tris": 0}


def build(doc, options, parent_collection=None, root_collection=None):
    """Build doc into a root collection.

    parent_collection: where to link a NEW root (scene root when None).
    root_collection:   reuse an existing collection (re-import) — parent is
                       ignored, provenance and options props are refreshed.
    """
    res = BuildResult()
    t0 = time.time()
    scale = doc.scale_m() * float(options.get("scale", 1.0))
    scene = bpy.context.scene

    if root_collection is not None:
        root = root_collection
    else:
        parent = parent_collection or scene.collection
        stem = os.path.splitext(os.path.basename(doc.path))[0]
        root_name = options.get("root_prefix", "") + sanitize(stem)
        root = bpy.data.collections.new(root_name)
        parent.children.link(root)
    res.root = root

    mesh_cache = {}  # content signature -> mesh (share_instances=True)

    def mesh_for(proto):
        if not options.get("share_instances", True):
            return _mesh_from_shape(sanitize(proto.name), proto.shape, scale,
                                    options.get("smooth", False))[0]
        mesh, sig = _mesh_from_shape(sanitize(proto.name), proto.shape, scale,
                                     options.get("smooth", False))
        if mesh is None:
            return None
        shared = mesh_cache.get(sig)
        if shared is None:
            mesh_cache[sig] = mesh
            return mesh
        bpy.data.meshes.remove(mesh)
        return shared

    def emit_leaf(node, coll, matrix_world, counter):
        proto = node.proto
        mesh = mesh_for(proto)
        if mesh is None:
            res.stats["skipped_empty"] += 1
            return
        obj = bpy.data.objects.new(sanitize(node.name), mesh)
        coll.objects.link(obj)
        obj.matrix_world = matrix_world
        obj["stepforge/proto"] = proto.name
        hexcolor = node.color or proto.color
        if options.get("import_colors", True) and hexcolor:
            h = hexcolor.lstrip("#")
            obj.color = tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) + (1.0,)
        if options.get("smooth", False):
            mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
        res.objects.append((obj, node.name, hexcolor if options.get("import_colors", True) else None))
        res.stats["built"] += 1

    def walk(node, coll, parent_matrix, counter):
        matrix = parent_matrix @ _t16_to_matrix(position_scale(node.trsf, scale))
        if node.proto is not None:
            emit_leaf(node, coll, matrix, counter)
            return
        sub = coll
        if options.get("collection_mode", "TREE") == "TREE":
            sub = bpy.data.collections.new(sanitize(node.name))
            coll.children.link(sub)
        children = node.children
        if options.get("sort_alpha", True):
            children = sorted(children, key=lambda n: n.name.lower())
        for ch in children:
            walk(ch, sub, matrix, counter)

    roots = doc.roots
    if len(roots) > 1 and options.get("sort_alpha", True):
        roots = sorted(roots, key=lambda n: n.name.lower())
    for r in roots:
        walk(r, root, Matrix.Identity(4), [0])

    # stats
    st = doc.stats()
    res.stats["instances"] = st["instances"]
    res.stats["prototypes"] = st["prototypes"]
    unique = {m.name: m for o, _, _ in res.objects for m in [o.data]}
    res.stats["unique_meshes"] = len(unique)
    res.stats["tris"] = sum(len(m.polygons) for m in unique.values())

    # provenance + options snapshot (drives re-import)
    root[SOURCE_PROP] = os.path.abspath(doc.path)
    root["stepforge/sha256"] = sha256(doc.path)
    root["stepforge/unit"] = doc.unit
    root["stepforge/importer"] = "STEPforge"
    root["stepforge/version"] = _version_string()
    root["stepforge/date"] = datetime.datetime.now().isoformat(timespec="seconds")
    root["stepforge/stats"] = "%d instance(s), %d unique mesh(es), %d tri(s), %s" % (
        res.stats["built"], res.stats["unique_meshes"], res.stats["tris"], doc.unit)
    root[OPTIONS_PROP] = json_dumps(options)
    return res


def _version_string():
    try:
        from . import bl_info
        return ".".join(str(v) for v in bl_info["version"])
    except Exception:
        return "?"


def json_dumps(options):
    import json
    return json.dumps(options, sort_keys=True)


def find_root_collection(obj, scene):
    """Root collection (holding SOURCE_PROP) above obj, or None."""
    parents = {}
    for coll in bpy.data.collections:
        for child in coll.children:
            parents[child] = coll
    for coll in obj.users_collection:
        cur = coll
        while cur in parents and parents[cur] not in (None, scene.collection):
            cur = parents[cur]
        if cur.get(SOURCE_PROP):
            return cur
    return None
