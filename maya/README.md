# Edges to Joints (Maya)

A PyMEL tool that builds a joint chain along selected polygon edges, with a
joint at every vertex.

## Install

Drag `edges_to_joints.py` from a file browser into any Maya viewport. It
copies itself into your user scripts folder and adds an **E2J** button to
the shelf that is currently open. Dropping it again updates the script and
replaces the button rather than adding a second one.

## Use

1. Select a run of connected edges (an edge loop, or part of one). You can
   select several separate runs; each becomes its own chain.
2. Click **E2J** on the shelf and set the options:
   - **Name prefix**: joints are named `prefix_01_jnt`, `prefix_02_jnt`, ...
     (`prefix1_01_jnt`, `prefix2_01_jnt`, ... when there are several chains).
   - **Joint radius**: display size of the joints.
   - **Root joint at**: which end of the chain the root goes on (closest to /
     farthest from the origin, lowest, highest). **Reverse direction** flips it.
   - **Closed loops: end joint on root**: for a full edge loop, adds a last
     joint on top of the root so the chain goes all the way round.
   - **Orient joints**: primary axis aims down the chain, secondary axis
     points toward the chosen world direction. The end joint copies its
     parent's orientation. Untick to leave joints world-aligned.
3. Click **Create Joints**. The whole build is one undo step.

Edges that branch (one vertex touching three or more selected edges) can't
form a single chain; the tool selects the vertex where it branches and stops.

Needs PyMEL (Maya 2022+ asks whether to install it during Maya setup; it can
also be added later with `mayapy -m pip install pymel`).
