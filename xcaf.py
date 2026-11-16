"""XCAF/STEP reading for STEPforge — pure python, no bpy.

Builds a neutral document model from a STEP file via OCP (Open CASCADE):

    StepDoc
      unit          file unit detected from the STEP header text ("mm", "inch", ...)
      mm_per_unit   conversion factor for the delivered coordinates
      protos        {key: Proto}  unique prototype shapes (shared by instances)
      roots         [Node]        one per free shape (assembly or lone part)

    Proto   name, shape (location stripped), color ("#rrggbb" or None), trsf
    Node    instance tree: name, transform (16 floats, row-major, file units),
            color (effective, inherited), children, proto (leaf only)

Conventions
  * OCC converts file units to its target unit on read; with the default
    target (MM) delivered coordinates are millimetres. The builder scales
    positions by mm_per_unit * 0.001 so the scene is always in metres.
  * A prototype's own location (rarely non-identity) is stripped from the
    shape and folded into every leaf instance's transform.
"""
import re

_UNIT_PATTERNS = [
    (re.compile(r"\.MILLI\.,\.METRE\.", re.I), "mm", 1.0),
    (re.compile(r"\.CENTI\.,\.METRE\.", re.I), "cm", 10.0),
    (re.compile(r"\.METRE\.", re.I), "m", 1000.0),
    (re.compile(r"\.MICRO\.,\.METRE\.", re.I), "um", 0.001),
    (re.compile(r"\.MILLI\.,\.INCH\.", re.I), "mil", 0.0254),
    (re.compile(r"'INCH'|\.INCH\.", re.I), "in", 25.4),
]


def detect_unit(path):
    """(unit_name, mm_per_unit) from the STEP header text; STEP defaults mm."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1 << 20).decode("ascii", "ignore")
    except OSError:
        return "mm", 1.0
    for rx, name, mm in _UNIT_PATTERNS:
        if rx.search(head):
            return name, mm
    return "mm", 1.0


class Proto(object):
    __slots__ = ("name", "shape", "color", "trsf")

    def __init__(self, name, shape, color, trsf):
        self.name = name        # prototype (referred shape) name
        self.shape = shape      # TopoDS_Shape, location stripped
        self.color = color      # "#rrggbb" or None
        self.trsf = trsf        # location stripped off (folded into instances)


class Node(object):
    __slots__ = ("name", "trsf", "color", "children", "proto")

    def __init__(self, name, trsf, color=None, proto=None):
        self.name = name
        self.trsf = trsf        # 16 floats row-major, parent space, file units
        self.color = color      # effective color (instance/proto/assembly)
        self.children = []      # Node list (assemblies)
        self.proto = proto      # Proto (leaf parts)


def _trsf16(trsf):
    t = tuple(trsf.Value(r, c) for r in (1, 2, 3) for c in (1, 2, 3, 4))
    return t + (0.0, 0.0, 0.0, 1.0)


def multiply16(a, b):
    """Row-major 4x4 composition a*b (b applied first), like gp_Trsf."""
    out = [0.0] * 16
    out[15] = 1.0
    for r in range(3):
        for c in range(4):
            v = sum(a[r * 4 + k] * b[k * 4 + c] for k in range(3))
            if c == 3:
                v += a[r * 4 + 3]
            out[r * 4 + c] = v
    return tuple(out)


class StepDoc(object):
    def __init__(self, path):
        self.path = path
        self.unit = "mm"
        self.mm_per_unit = 1.0
        self.protos = {}
        self.roots = []
        self._read()

    # -- reading ---------------------------------------------------------

    def _read(self):
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString, TCollection_AsciiString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.TDF import TDF_LabelSequence, TDF_Label
        from OCP.TDataStd import TDataStd_Name
        from OCP.TopLoc import TopLoc_Location

        ext = self.path.lower().rsplit(".", 1)[-1]
        if ext in ("stp", "step"):
            from OCP.STEPCAFControl import STEPCAFControl_Reader
            reader = STEPCAFControl_Reader()
            self.unit, self.mm_per_unit = detect_unit(self.path)
        elif ext in ("igs", "iges"):
            from OCP.IGESCAFControl import IGESCAFControl_Reader
            reader = IGESCAFControl_Reader()
            # OCC converts IGES units to its default target (MM); same as STEP
            self.unit, self.mm_per_unit = "mm", 1.0
        else:
            raise RuntimeError("Unsupported extension: .%s" % ext)

        doc = TDocStd_Document(TCollection_ExtendedString("stepforge"))
        reader.SetNameMode(True)
        if hasattr(reader, "SetColorMode"):
            reader.SetColorMode(True)
        status = reader.ReadFile(self.path)
        if int(status) != 1:
            raise RuntimeError("ReadFile returned %s" % status)
        if not reader.Transfer(doc):
            raise RuntimeError("Transfer failed")

        tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        ctool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

        def lname(label):
            attr = TDataStd_Name()
            if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
                return TCollection_AsciiString(attr.Get()).ToCString()
            return None

        def color_of(label):
            from OCP.Quantity import Quantity_Color
            from OCP.XCAFDoc import XCAFDoc_ColorType
            col = Quantity_Color()
            for ct in (XCAFDoc_ColorType.XCAFDoc_ColorSurf,
                       XCAFDoc_ColorType.XCAFDoc_ColorGen):
                try:
                    if ctool.GetColor_s(label, ct, col):
                        return "#%02x%02x%02x" % (round(col.Red() * 255),
                                                  round(col.Green() * 255),
                                                  round(col.Blue() * 255))
                except TypeError:
                    pass
            return None

        def proto_for(label):
            """Register (or fetch) the prototype for a referred shape label."""
            proto = self.protos.get(label)
            if proto is None:
                shape = tool.GetShape_s(label)
                loc = shape.Location()
                trsf = loc.Transformation() if not loc.IsIdentity() else None
                stripped = shape.Moved(TopLoc_Location()) if trsf else shape
                proto = Proto(lname(label) or "part", stripped,
                              color_of(label), trsf)
                self.protos[label] = proto
            return proto

        def build(label, name, trsf, color):
            """Node for a shape label (assembly or part); color = inherited."""
            color = color_of(label) or color
            if tool.IsAssembly_s(label):
                node = Node(name, trsf, color)
                comps = TDF_LabelSequence()
                tool.GetComponents_s(label, comps)
                for i in range(1, comps.Length() + 1):
                    comp = comps.Value(i)
                    cshape = tool.GetShape_s(comp)
                    ctr = _trsf16(cshape.Location().Transformation())
                    ctr = multiply16(trsf, ctr)
                    cname = lname(comp) or "instance"
                    ccolor = color_of(comp) or color
                    cref = TDF_Label()
                    if not (tool.GetReferredShape_s(comp, cref) and not cref.IsNull()):
                        cref = comp  # defensive: component without referral
                    child = build(cref, cname, ctr, ccolor)
                    node.children.append(child)
                return node
            proto = proto_for(label)
            ptr = _trsf16(proto.trsf) if proto.trsf is not None else None
            ntr = multiply16(trsf, ptr) if ptr else trsf
            return Node(name, ntr, color, proto=proto)

        free = TDF_LabelSequence()
        tool.GetFreeShapes(free)
        for i in range(1, free.Length() + 1):
            label = free.Value(i)
            base = _trsf16(tool.GetShape_s(label).Location().Transformation())
            self.roots.append(build(label, lname(label) or "model", base, None))

    # -- convenience -----------------------------------------------------

    def stats(self):
        instances = 0
        depth = 1

        def walk(node, d):
            nonlocal instances, depth
            depth = max(depth, d)
            if node.proto is not None:
                instances += 1
            for ch in node.children:
                walk(ch, d + 1)
        for r in self.roots:
            walk(r, 1)
        return {"instances": instances, "depth": depth,
                "prototypes": len(self.protos)}

    def scale_m(self):
        """File-unit -> metre factor for positions (default target MM)."""
        return self.mm_per_unit * 0.001


def position_scale(t16, scale):
    """Scale only the translation part — mesh vertices are converted to
    metres at build time, so the 3x3 (rotation/proto-scale) stays as-is."""
    t = list(t16)
    t[3] *= scale
    t[7] *= scale
    t[11] *= scale
    return tuple(t)
