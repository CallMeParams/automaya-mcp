"""Build the deterministic scene the evaluation questions are written against.

Run inside Maya (Script Editor or mayapy) with the repo on sys.path:
    import evals.build_eval_scene as e; e.build()
"""
from maya import cmds


def build():
    cmds.file(new=True, force=True)
    cmds.currentUnit(linear="cm", time="film")
    cmds.playbackOptions(min=1001, max=1096, ast=1001, aet=1096)
    grp = cmds.group(empty=True, name="SET_street")
    for i in range(6):
        c = cmds.polyCube(w=400, h=900 + i * 150, d=400, name="bldg_%02d" % (i + 1))[0]
        cmds.setAttr(c + ".translate", -1250 + i * 500, (900 + i * 150) / 2.0, -600)
        cmds.parent(c, grp)
    road = cmds.polyPlane(w=4000, h=1200, name="road_plane")[0]
    cmds.parent(road, grp)
    hero = cmds.polySphere(r=90, name="hero_ball")[0]
    cmds.setAttr(hero + ".translateY", 90)
    cmds.setKeyframe(hero, attribute="translateX", t=1001, v=-800)
    cmds.setKeyframe(hero, attribute="translateX", t=1096, v=800)
    cam = cmds.camera(name="shotCam_010", focalLength=35)[0]
    cam = cmds.rename(cam, "shotCam_010")
    cmds.setAttr(cam + ".translate", 0, 180, 1400)
    mat = cmds.shadingNode("standardSurface", asShader=True, name="hero_mat")
    sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name="hero_matSG")
    cmds.connectAttr(mat + ".outColor", sg + ".surfaceShader")
    cmds.setAttr(mat + ".baseColor", 0.8, 0.1, 0.1, type="double3")
    cmds.sets(hero, forceElement=sg)
    unfrozen = cmds.polyCube(name="crate_unfrozen")[0]
    cmds.setAttr(unfrozen + ".scale", 2, 2, 2)
    cmds.setAttr(unfrozen + ".translate", 600, 50, 300)
    print("eval scene built")
