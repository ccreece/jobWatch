"""
Edges to Joints
===============

Builds a joint chain along a selection of polygon edges, with one joint at
every vertex.

Install: drag and drop this file into a Maya viewport. It copies itself to
your user scripts folder and adds an "E2J" button to the current shelf.
Click the button to open the tool window.

Usage:
    1. Select one or more runs of connected edges (an edge loop or part of
       one works well).
    2. Open the tool from the shelf, set the options and click
       "Create Joints".

Each separate run of edges becomes its own chain. A run that branches (a
vertex touching three or more selected edges) can't be made into a single
chain, so the tool stops and says which vertex is the problem.

Works with Maya 2018+ (Python 2 and 3). Needs PyMEL.
"""

from __future__ import print_function

import math
import os
import shutil

import pymel.core as pm


MODULE_NAME = "edges_to_joints"
WINDOW_NAME = "edgesToJointsWin"
SHELF_LABEL = "Edges to Joints"
SHELF_DOC_TAG = "edgesToJointsShelfButton"

ROOT_OPTIONS = [
    "Closest to origin",
    "Farthest from origin",
    "Lowest (-Y)",
    "Highest (+Y)",
]
AXES = ["x", "y", "z"]
WORLD_UP_OPTIONS = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]


# ---------------------------------------------------------------------------
# Edge graph helpers (plain Python, no Maya calls)
# ---------------------------------------------------------------------------

def build_adjacency(edge_pairs):
    """Map each vertex key to the set of vertex keys it shares an edge with."""
    adjacency = {}
    for a, b in edge_pairs:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
    return adjacency


def connected_components(adjacency):
    """Split the vertex graph into groups of vertices joined by edges."""
    seen = set()
    components = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        component = []
        stack = [start]
        seen.add(start)
        while stack:
            key = stack.pop()
            component.append(key)
            for neighbour in adjacency[key]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    stack.append(neighbour)
        components.append(component)
    return components


def walk_chain(adjacency, start):
    """Walk from start along a branchless run of vertices, returning them in
    order. For a closed loop the walk stops before returning to start."""
    order = [start]
    visited = set(order)
    current = start
    while True:
        next_keys = sorted(k for k in adjacency[current] if k not in visited)
        if not next_keys:
            return order
        current = next_keys[0]
        order.append(current)
        visited.add(current)


def order_component(adjacency, component, root_score, reverse=False):
    """Return (ordered vertex keys, is_closed_loop) for one component.

    root_score(key) ranks candidate root vertices; the lowest score wins.
    Raises ValueError if the component branches.
    """
    for key in component:
        if len(adjacency[key]) > 2:
            raise ValueError(key)

    endpoints = [k for k in component if len(adjacency[k]) == 1]
    if endpoints:
        root = min(endpoints, key=root_score)
        if reverse:
            root = max(endpoints, key=root_score)
        return walk_chain(adjacency, root), False

    # Closed loop: every vertex has two neighbours.
    root = min(component, key=root_score)
    order = walk_chain(adjacency, root)
    if reverse:
        order = [order[0]] + order[:0:-1]
    return order, True


def orient_strings(primary, secondary, world_up):
    """Turn axis choices into the joint command's orientJoint and
    secondaryAxisOrient values, e.g. ("x", "y", "+Y") -> ("xyz", "yup")."""
    if primary == secondary:
        raise ValueError("Primary and secondary axes must be different.")
    third = [a for a in AXES if a not in (primary, secondary)][0]
    direction = "up" if world_up[0] == "+" else "down"
    return primary + secondary + third, world_up[1].lower() + direction


# ---------------------------------------------------------------------------
# Joint creation
# ---------------------------------------------------------------------------

def _selected_edge_pairs():
    """Return (list of vertex-key pairs, {vertex key: world position}) for the
    selected mesh edges. A vertex key is (shape name, vertex index)."""
    edges = [c for c in pm.ls(selection=True, flatten=True)
             if isinstance(c, pm.MeshEdge)]
    pairs = []
    positions = {}
    for edge in edges:
        shape = edge.node()
        ids = list(edge.connectedVertices().indices())
        if len(ids) != 2:
            continue
        keys = [(shape.name(), i) for i in ids]
        for key, i in zip(keys, ids):
            if key not in positions:
                point = shape.vtx[i].getPosition(space="world")
                positions[key] = (point.x, point.y, point.z)
        pairs.append((keys[0], keys[1]))
    return pairs, positions


def root_scorer(mode, positions):
    """Return a function scoring vertex keys for root_mode; lowest wins."""
    def distance(key):
        return math.sqrt(sum(c * c for c in positions[key]))
    if mode == "Farthest from origin":
        return lambda k: -distance(k)
    if mode == "Lowest (-Y)":
        return lambda k: positions[k][1]
    if mode == "Highest (+Y)":
        return lambda k: -positions[k][1]
    return distance


def create_joints_from_edges(prefix="edgeChain", radius=1.0,
                             root_mode=ROOT_OPTIONS[0], reverse=False,
                             close_loops=False, orient=True,
                             primary="x", secondary="y", world_up="+Y"):
    """Create one joint chain per run of selected edges. Returns the root
    joints that were made."""
    pairs, positions = _selected_edge_pairs()
    if not pairs:
        pm.warning("Edges to Joints: select some polygon edges first.")
        return []

    if orient:
        orient_joint, secondary_orient = orient_strings(
            primary, secondary, world_up)

    adjacency = build_adjacency(pairs)
    score = root_scorer(root_mode, positions)
    chains = []
    for component in connected_components(adjacency):
        try:
            chains.append(order_component(adjacency, component, score,
                                          reverse))
        except ValueError as err:
            shape, index = err.args[0]
            pm.select("{0}.vtx[{1}]".format(shape, index))
            pm.warning("Edges to Joints: the selected edges branch at "
                       "{0}.vtx[{1}] (now selected). Each run of edges must "
                       "be a single line or loop.".format(shape, index))
            return []

    roots = []
    joint_count = 0
    pm.undoInfo(openChunk=True, chunkName="edgesToJoints")
    try:
        for chain_index, (order, closed) in enumerate(chains, 1):
            if len(chains) == 1:
                chain_prefix = prefix
            else:
                chain_prefix = "{0}{1}".format(prefix, chain_index)
            points = [positions[k] for k in order]
            if closed and close_loops:
                points.append(points[0])

            pm.select(clear=True)
            joints = []
            for i, point in enumerate(points, 1):
                name = "{0}_{1:02d}_jnt".format(chain_prefix, i)
                joints.append(pm.joint(position=point, radius=radius,
                                       name=name))

            if orient and len(joints) > 1:
                pm.joint(joints[0], edit=True, orientJoint=orient_joint,
                         secondaryAxisOrient=secondary_orient,
                         children=True, zeroScaleOrient=True)
                # The end joint has nothing to aim at, so it takes its
                # parent's orientation.
                joints[-1].jointOrient.set(0, 0, 0)
            elif not orient:
                pm.joint(joints[0], edit=True, orientJoint="none",
                         children=True, zeroScaleOrient=True)
            roots.append(joints[0])
            joint_count += len(joints)
    finally:
        pm.undoInfo(closeChunk=True)

    pm.select(roots)
    print("Edges to Joints: made {0} chain(s), {1} joint(s).".format(
        len(roots), joint_count))
    return roots


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

class EdgesToJointsUI(object):

    def __init__(self):
        if pm.window(WINDOW_NAME, exists=True):
            pm.deleteUI(WINDOW_NAME)

        self.window = pm.window(WINDOW_NAME, title="Edges to Joints",
                                sizeable=True, widthHeight=(380, 330))
        with pm.columnLayout(adjustableColumn=True, rowSpacing=6,
                             columnAttach=("both", 8)):
            pm.separator(style="none", height=4)
            pm.text(label="Select edges, then click Create Joints. "
                          "One joint is made at each vertex.",
                    align="left", wordWrap=True)

            with pm.frameLayout(label="Chain", marginWidth=6,
                                marginHeight=6):
                with pm.columnLayout(adjustableColumn=True, rowSpacing=4):
                    self.prefix = pm.textFieldGrp(
                        label="Name prefix", text="edgeChain",
                        columnWidth2=(110, 200))
                    self.radius = pm.floatSliderGrp(
                        label="Joint radius", field=True, value=1.0,
                        minValue=0.01, maxValue=10.0, fieldMaxValue=1000.0,
                        precision=2, columnWidth3=(110, 60, 140))
                    self.root_mode = pm.optionMenuGrp(
                        label="Root joint at", columnWidth2=(110, 200))
                    for option in ROOT_OPTIONS:
                        pm.menuItem(label=option)
                    self.reverse = pm.checkBoxGrp(
                        label="", label1="Reverse direction",
                        columnWidth2=(110, 200))
                    self.close_loops = pm.checkBoxGrp(
                        label="", label1="Closed loops: end joint on root",
                        columnWidth2=(110, 240))

            with pm.frameLayout(label="Orientation", marginWidth=6,
                                marginHeight=6):
                with pm.columnLayout(adjustableColumn=True, rowSpacing=4):
                    self.orient = pm.checkBoxGrp(
                        label="", label1="Orient joints", value1=True,
                        columnWidth2=(110, 200),
                        changeCommand=self._update_orient_enabled)
                    self.primary = pm.radioButtonGrp(
                        label="Primary axis", labelArray3=["X", "Y", "Z"],
                        numberOfRadioButtons=3, select=1,
                        columnWidth4=(110, 50, 50, 50))
                    self.secondary = pm.radioButtonGrp(
                        label="Secondary axis", labelArray3=["X", "Y", "Z"],
                        numberOfRadioButtons=3, select=2,
                        columnWidth4=(110, 50, 50, 50))
                    self.world_up = pm.optionMenuGrp(
                        label="Secondary world", columnWidth2=(110, 200))
                    for option in WORLD_UP_OPTIONS:
                        pm.menuItem(label=option)
                    self.world_up.setValue("+Y")

            with pm.rowLayout(numberOfColumns=2, adjustableColumn=1,
                              columnAttach2=("both", "both")):
                pm.button(label="Create Joints", height=32,
                          command=self._create)
                pm.button(label="Close", height=32, width=80,
                          command=lambda *_: pm.deleteUI(WINDOW_NAME))
        self.window.show()

    def _update_orient_enabled(self, *_):
        enabled = self.orient.getValue1()
        for control in (self.primary, self.secondary, self.world_up):
            control.setEnable(enabled)

    def _create(self, *_):
        primary = AXES[self.primary.getSelect() - 1]
        secondary = AXES[self.secondary.getSelect() - 1]
        orient = self.orient.getValue1()
        if orient and primary == secondary:
            pm.warning("Edges to Joints: primary and secondary axes must be "
                       "different.")
            return
        prefix = self.prefix.getText().strip() or "edgeChain"
        create_joints_from_edges(
            prefix=prefix,
            radius=self.radius.getValue(),
            root_mode=self.root_mode.getValue(),
            reverse=self.reverse.getValue1(),
            close_loops=self.close_loops.getValue1(),
            orient=orient,
            primary=primary,
            secondary=secondary,
            world_up=self.world_up.getValue(),
        )


def show():
    """Open the Edges to Joints window."""
    return EdgesToJointsUI()


# ---------------------------------------------------------------------------
# Drag-and-drop install
# ---------------------------------------------------------------------------

SHELF_COMMAND = """import {0}
{0}.show()
""".format(MODULE_NAME)


def install_shelf_button():
    """Add (or replace) the Edges to Joints button on the current shelf."""
    shelf_top = pm.melGlobals["gShelfTopLevel"]
    shelf = pm.tabLayout(shelf_top, query=True, selectTab=True)
    shelf_path = "{0}|{1}".format(shelf_top, shelf)

    for child in pm.shelfLayout(shelf_path, query=True, childArray=True) \
            or []:
        if (pm.shelfButton(child, exists=True)
                and pm.shelfButton(child, query=True, docTag=True)
                == SHELF_DOC_TAG):
            pm.deleteUI(child)

    pm.shelfButton(parent=shelf_path, label=SHELF_LABEL,
                   annotation="Create a joint chain along selected edges",
                   docTag=SHELF_DOC_TAG, image1="kinJoint.png",
                   imageOverlayLabel="E2J", sourceType="python",
                   command=SHELF_COMMAND)
    pm.mel.saveAllShelves(shelf_top)
    return shelf


def onMayaDroppedPythonFile(*_):
    """Maya calls this when the file is dropped into a viewport."""
    source = os.path.abspath(__file__)
    if source.endswith((".pyc", ".pyo")):
        source = source[:-1]
    scripts_dir = pm.internalVar(userScriptDir=True)
    target = os.path.join(scripts_dir, MODULE_NAME + ".py")
    if os.path.normcase(source) != os.path.normcase(os.path.abspath(target)):
        if not os.path.isdir(scripts_dir):
            os.makedirs(scripts_dir)
        shutil.copy2(source, target)

    shelf = install_shelf_button()
    message = ("Edges to Joints installed to {0}\n"
               "Button added to the \"{1}\" shelf.").format(target, shelf)
    print(message)
    pm.inViewMessage(assistMessage="Edges to Joints added to the "
                                   "<hl>{0}</hl> shelf.".format(shelf),
                     position="topCenter", fade=True)
